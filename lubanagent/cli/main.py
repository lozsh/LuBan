"""luban 命令行：初始化/迁移/导入导出/评测（契约 D1-8 + D2-1 回填 eval）。

CLI 是初始化/迁移/评测入口，不承担运行期职责；typer 命令函数为同步上下文，
文件扫描与 YAML 解析在同步侧完成，DB 操作用 asyncio.run 包装 async 实现。
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
import yaml
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from lubanagent.app.lifespan import seed_defaults
from lubanagent.cli.yaml_io import (
    definition_from_yaml,
    definition_to_yaml,
    dump_yaml_file,
    load_yaml_file,
)
from lubanagent.config import Settings
from lubanagent.core import AgentDefinition, BrainProfile, ErrorCode, LubanError
from lubanagent.extension import default_registry, discover_packages
from lubanagent.mcp import McpRegistry
from lubanagent.model import BrainResolver, create_chat_model
from lubanagent.observability.evals import (
    ComparisonReport,
    EvalDataset,
    EvalReport,
    EvalRunner,
    LlmJudge,
    compare_reports,
    load_dataset,
)
from lubanagent.persistence import (
    AgentRepository,
    Database,
    DatabaseMcpServerStore,
    EvalRepository,
    LubanCheckpointer,
    ModelRepository,
    RunRepository,
)
from lubanagent.runtime import AgentRuntime, restore_knowledge_sources
from lubanagent.skill import SkillRegistry
from lubanagent.tool import AutoApproveManager

app = typer.Typer(
    name="luban",
    help="LubanAgent 命令行（初始化/迁移/导入导出/评测）",
    no_args_is_help=True,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

DatabaseUrlOption = Annotated[
    str | None, typer.Option(envvar="DATABASE_URL", help="数据库 URL")
]


def _resolve_url(database_url: str | None) -> str:
    return database_url or Settings.load().database_url


def _fail(error: Exception) -> NoReturn:
    """统一友好报错（不打堆栈）：LubanError 带错误码，其余带异常类型。"""
    if isinstance(error, LubanError):
        typer.echo(f"error: {error.code.value}: {error.message}", err=True)
    else:
        typer.echo(f"error: {type(error).__name__}: {error}", err=True)
    raise typer.Exit(1) from error


def _load_definitions(agents_dir: Path) -> list[AgentDefinition]:
    """同步侧：扫描 agents_dir/*.yaml（按文件名排序）并解析为定义。"""
    if not agents_dir.is_dir():
        typer.echo(f"agents 目录不存在，跳过导入: {agents_dir}")
        return []
    return [
        definition_from_yaml(load_yaml_file(path))
        for path in sorted(agents_dir.glob("*.yaml"))
    ]


@app.command()
def init(
    agents_dir: Annotated[Path, typer.Option(help="Agent YAML 目录")] = Path("app/agents"),
    database_url: DatabaseUrlOption = None,
    fill_missing: Annotated[
        bool, typer.Option(help="只导入不存在的 Agent，已存在一律跳过（自动启动用）")
    ] = False,
) -> None:
    """种子落库（只填不存在，重复调用安全）→ 导入 YAML：

    默认：不存在 → create_agent；已存在且内容有变化 → 追加新版本（create_version）；
    内容与当前版本一致 → 跳过（幂等，防重复导入版本膨胀）。
    --fill-missing：已存在一律跳过，不追加版本（dev.sh 自动启动用，
    避免磁盘旧 YAML 把工作台修改打回，H08）。
    打印每个 Agent 的 key@version，未变化的追加 (unchanged) 标记。
    """
    try:
        definitions = _load_definitions(agents_dir)
        results = asyncio.run(
            _init_async(definitions, _resolve_url(database_url), fill_missing=fill_missing)
        )
    except Exception as e:
        _fail(e)
    for key, version, unchanged in results:
        marker = " (unchanged)" if unchanged else ""
        typer.echo(f"{key}@{version}{marker}")


def _strip_runtime_fields(definition: AgentDefinition) -> dict[str, Any]:
    """剔除 version/status 等运行期字段，仅保留定义内容（用于导入幂等比较）。"""
    payload = definition.model_dump(mode="json")
    payload.pop("version", None)
    payload.pop("status", None)
    return payload


async def _init_async(
    definitions: list[AgentDefinition], url: str, *, fill_missing: bool = False
) -> list[tuple[str, int, bool]]:
    db = Database(url)
    try:
        await seed_defaults(db)
        results: list[tuple[str, int, bool]] = []
        async with db.session() as session:
            repo = AgentRepository(session)
            for definition in definitions:
                existing = await repo.get_agent(definition.key)
                if existing is None:
                    definition.version = 1
                    await repo.create_agent(definition)
                    results.append((definition.key, definition.version, False))
                elif fill_missing:
                    # --fill-missing：已存在一律跳过，磁盘旧 YAML 不打回工作台修改（H08）
                    results.append((definition.key, existing.version, True))
                elif _strip_runtime_fields(definition) == _strip_runtime_fields(
                    existing
                ):
                    # 内容与当前版本一致 → 跳过导入，不追加版本（风险登记 R7）
                    results.append((definition.key, existing.version, True))
                else:
                    definition.version = existing.version + 1
                    await repo.create_version(definition.key, definition)
                    results.append((definition.key, definition.version, False))
        return results
    finally:
        await db.close()


@app.command()
def export(
    key: Annotated[str, typer.Argument(help="Agent key")],
    output: Annotated[
        Path | None, typer.Option(help="输出文件（缺省打印到 stdout）")
    ] = None,
    version: Annotated[
        int | None, typer.Option(help="版本号（缺省当前版）")
    ] = None,
    database_url: DatabaseUrlOption = None,
) -> None:
    """导出 Agent 定义为 YAML（缺省当前版）；output 缺省打印到 stdout。往返无损。"""
    try:
        data = asyncio.run(_export_async(key, version, _resolve_url(database_url)))
        if output is None:
            typer.echo(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        else:
            dump_yaml_file(data, output)
            typer.echo(f"已导出: {output}")
    except Exception as e:
        _fail(e)


async def _export_async(key: str, version: int | None, url: str) -> dict[str, Any]:
    db = Database(url)
    try:
        async with db.session() as session:
            repo = AgentRepository(session)
            if version is None:
                definition = await repo.get_agent(key)
                if definition is None:
                    raise LubanError(ErrorCode.AGENT_NOT_FOUND, f"Agent 不存在: {key}")
            else:
                snapshot = await repo.get_version(key, version)
                if snapshot is None:
                    raise LubanError(
                        ErrorCode.AGENT_VERSION_NOT_FOUND,
                        f"Agent 版本不存在: {key}@{version}",
                    )
                definition = snapshot.definition
        return definition_to_yaml(definition)
    finally:
        await db.close()


def _snapshot_logging() -> list[tuple[logging.Logger, int, bool, bool, list[logging.Handler]]]:
    loggers = [logging.getLogger()]
    loggers += [
        logger
        for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]
    return [
        (logger, logger.level, logger.propagate, logger.disabled, logger.handlers[:])
        for logger in loggers
    ]


def _restore_logging(
    snapshot: list[tuple[logging.Logger, int, bool, bool, list[logging.Handler]]],
) -> None:
    for logger, level, propagate, disabled, handlers in snapshot:
        logger.level = level
        logger.propagate = propagate
        logger.disabled = disabled
        logger.handlers[:] = handlers


@app.command()
def migrate(database_url: DatabaseUrlOption = None) -> None:
    """包 Alembic upgrade head（读仓库根 alembic.ini）。"""
    resolved = _resolve_url(database_url)
    config = AlembicConfig(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    config.set_main_option(
        "sqlalchemy.url", Settings(database_url=resolved).sync_database_url
    )
    # migrations/env.py 会从 Settings() 重取 URL（DATABASE_URL env 优先），在此显式注入；
    # env.py 自行把 async 驱动映射为同步驱动。env.py 的 fileConfig 会重置进程 logging，
    # 快照并在迁移后恢复（不破坏宿主进程，如测试运行器）。
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = resolved
    snapshot = _snapshot_logging()
    try:
        alembic_command.upgrade(config, "head")
    except Exception as e:
        _fail(e)
    finally:
        _restore_logging(snapshot)
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
    typer.echo("migrated to head")


# ---------------------------------------------------------------- eval 评测

eval_app = typer.Typer(help="评测：跑数据集、版本对比回归（契约 D2-1）", no_args_is_help=True)
app.add_typer(eval_app, name="eval")


def _parse_agent_spec(spec: str) -> tuple[str, int | None]:
    """'key@3' → ('key', 3)；'key' → ('key', None)；@ 后非整数 → VALIDATION_ERROR。"""
    if "@" not in spec:
        return spec, None
    key, _, suffix = spec.rpartition("@")
    if not key or not suffix.isdigit():
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            f"Agent 规格格式应为 key 或 key@version: {spec}",
            details={"agent": spec},
        )
    return key, int(suffix)


def _fail_evaluation(error: Exception) -> NoReturn:
    """eval 运行错误语义：exit 2（契约 D2-1 决策 4）。"""
    if isinstance(error, LubanError):
        typer.echo(f"error: {error.code.value}: {error.message}", err=True)
    else:
        typer.echo(f"error: {type(error).__name__}: {error}", err=True)
    raise typer.Exit(2) from error


@eval_app.command(name="run")
def eval_run(
    agent: Annotated[str, typer.Argument(help="Agent key，可带 @version（缺省当前版）")],
    dataset: Annotated[Path, typer.Option(help="评测数据集 YAML")],
    compare: Annotated[
        str | None, typer.Option(help="对比对象：版本号或基线报告 JSON 路径")
    ] = None,
    save: Annotated[Path | None, typer.Option(help="本次报告 JSON 输出路径")] = None,
    auto_approve: Annotated[
        bool,
        typer.Option(
            help="自动放行审批型工具：高风险工具真实执行（缺省不执行，仅调用意图被断言）"
        ),
    ] = False,
    database_url: DatabaseUrlOption = None,
) -> None:
    """跑评测数据集：逐 case pass/fail + pass_rate；--compare 命中回归 exit 1，运行错误 exit 2。"""
    try:
        key, version = _parse_agent_spec(agent)
        dataset_obj = load_dataset(dataset)
        # 基线报告文件在同步侧读取（async 侧不做阻塞 IO）
        baseline_json = (
            None
            if compare is None or compare.isdigit()
            else Path(compare).read_text(encoding="utf-8")
        )
        report, comparison = asyncio.run(
            _eval_run_async(
                key,
                version,
                dataset_obj,
                compare,
                baseline_json,
                _resolve_url(database_url),
                auto_approve=auto_approve,
            )
        )
    except typer.Exit:
        raise
    except Exception as e:
        _fail_evaluation(e)
    passed = sum(1 for result in report.results if result.passed)
    for result in report.results:
        mark = "PASS" if result.passed else "FAIL"
        line = f"[{mark}] {result.case}"
        if not result.passed:
            reasons = [v.detail for v in result.verdicts if not v.passed and v.detail]
            if result.error is not None:
                reasons.append(str(result.error.get("message", result.error)))
            if reasons:
                line += f" — {'；'.join(reasons)}"
        typer.echo(line)
    typer.echo(f"pass_rate: {report.pass_rate:.2f} ({passed}/{len(report.results)})")
    if save is not None:
        save.write_text(report.to_json(), encoding="utf-8")
        typer.echo(f"已保存: {save}")
    if comparison is not None:
        typer.echo(f"regressions: {comparison.regressions}")
        typer.echo(f"improvements: {comparison.improvements}")
        typer.echo(f"pass_rate_delta: {comparison.pass_rate_delta:+.2f}")
        if comparison.regressions:
            raise typer.Exit(1)


async def _eval_run_async(
    key: str,
    version: int | None,
    dataset_obj: EvalDataset,
    compare: str | None,
    baseline_json: str | None,
    url: str,
    *,
    auto_approve: bool = False,
) -> tuple[EvalReport, ComparisonReport | None]:
    settings = Settings.load()
    # 扩展扫描：eval 在独立 CLI 进程构建 Runtime，必须先扫描用户扩展包
    # （与 create_app 同语义），否则带工具的 Agent 因注册表为空而全量不可运行。
    discover_packages(settings.extension_packages)
    db = Database(url)
    # 知识源自举：连接器通常在用户领地装配期注册（API 服务进程），
    # CLI 进程按库中已有 chunk 重建通用 SQL connector（种子/托管源行为一致）。
    await restore_knowledge_sources(db, settings)
    # 能力件登记处先建（finally 稳定可用；表登记/扫描在 try 内）
    mcp_registry = McpRegistry(repository=DatabaseMcpServerStore(db))
    skill_registry = SkillRegistry(skills_root=Path(settings.skills_root))
    try:
        # 入口校验：Agent/版本不存在属运行错误（exit 2），而非逐 case fail
        async with db.session() as session:
            agent_repo = AgentRepository(session)
            if version is not None:
                if await agent_repo.get_version(key, version) is None:
                    current = await agent_repo.get_agent(key)
                    code = (
                        ErrorCode.AGENT_NOT_FOUND
                        if current is None
                        else ErrorCode.AGENT_VERSION_NOT_FOUND
                    )
                    raise LubanError(code, f"Agent 版本不存在: {key} v{version}")
            elif await agent_repo.get_agent(key) is None:
                raise LubanError(ErrorCode.AGENT_NOT_FOUND, f"Agent 不存在: {key}")
        judge = None
        if any(case.judge is not None for case in dataset_obj.cases):
            async with db.session() as session:
                resolver = BrainResolver(ModelRepository(session), env=settings.env)
                judge_definition = await resolver.resolve(BrainProfile.SMART)
            judge = LlmJudge(create_chat_model(judge_definition))
        # --auto-approve：审批型工具自动放行（请求即批，Trace 事件流不变）。
        # 缺省不配 manager——审批型工具执行被闸门拦截（工具调用意图仍被
        # expect_tools 断言捕获），断言依赖执行结果的场景须显式开启。
        approval_manager = AutoApproveManager() if auto_approve else None
        # 能力件装配（H09）：与 API 服务同语义 —— Skill 扫描 + MCP 表登记；
        # Agent 未引用时零连接零副作用。
        await skill_registry.scan()
        await mcp_registry.load_from_db()
        runtime = AgentRuntime(
            db=db,
            settings=settings,
            registry=default_registry,
            approval_manager=approval_manager,
            skill_registry=skill_registry,
            mcp_registry=mcp_registry,
            # 缺口 #2：CLI eval 独立进程与 API 服务同语义装配（默认启用 checkpoint，
            # 中断的 eval Run 可 resume；仅本次进程，不跨进程续跑）
            checkpointer=LubanCheckpointer(db),
        )
        runner = EvalRunner(runtime, judge=judge)
        report = await runner.run_dataset(key, dataset_obj, version=version)
        # 结果落库（数据集首次登记；save_report 要求数据集行存在）：
        # 完整报告（逐 case verdicts/judge 结论）+ 聚合列，供 API 列表/详情读真实数据
        async with db.session() as session:
            eval_repo = EvalRepository(session)
            if await eval_repo.get_dataset(dataset_obj.key) is None:
                await eval_repo.create_dataset(dataset_obj, version="1")
            model_key = ""
            for result in report.results:
                if result.run_id:
                    run = await RunRepository(session).get_run(result.run_id)
                    if run is not None and run.model_key:
                        model_key = run.model_key
                        break
            report.model_key = model_key
            await eval_repo.save_report(report)
        comparison = None
        if compare is not None:
            if baseline_json is not None:
                baseline = EvalReport.from_json(baseline_json)
            else:
                baseline = await runner.run_dataset(key, dataset_obj, version=int(compare))
            comparison = compare_reports(baseline, report)
        return report, comparison
    finally:
        await mcp_registry.aclose()
        await db.close()
