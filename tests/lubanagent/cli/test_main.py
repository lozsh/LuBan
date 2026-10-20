"""luban CLI 端到端：migrate/init/export/eval（CliRunner + tmp SQLite + tmp YAML 目录）。

测试保持同步：CLI 内部以 asyncio.run 驱动 async 实现，测试侧 DB 断言同样用 asyncio.run 包装。
"""

import asyncio
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import create_engine, inspect
from typer.testing import CliRunner

from lubanagent.cli.main import app
from lubanagent.cli.yaml_io import definition_from_yaml, load_yaml_file
from lubanagent.core import AgentDefinition, BrainProfile, ModelDefinition
from lubanagent.knowledge import FakeEmbedding, KnowledgeStore
from lubanagent.model import fake_definition
from lubanagent.observability.evals import EvalReport
from lubanagent.persistence import (
    AgentRepository,
    Database,
    EvalRepository,
    ModelRepository,
)

runner = CliRunner()

KEY = "customer-service-bot"

SAMPLE: dict[str, Any] = {
    "name": KEY,
    "description": "售后客服",
    "instruction": "你是售后客服，先安抚情绪，再查订单",
    "brain": "smart",
    "fallbacks": ["fast"],
    "tools": ["query_order", "refund_apply"],
    "mcp_servers": ["github-mcp"],
    "skills": ["refund-sop"],
    "knowledge": ["product-faq"],
    "memory": "session-summary",
    "limits": {
        "max_steps": 20,
        "budget_tokens": 200000,
        "budget_cost": 1.5,
        "timeout_sec": 120,
    },
    "io_contract": {"input": "text", "output": "text"},
}


def _url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path}/cli.db"


def _write_yaml(agents_dir: Path, data: dict[str, Any], filename: str = "agent.yaml") -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / filename).write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _migrate(tmp_path: Path) -> None:
    result = runner.invoke(app, ["migrate", "--database-url", _url(tmp_path)])
    assert result.exit_code == 0, result.output


def _init(tmp_path: Path, agents_dir: Path) -> Any:
    return runner.invoke(
        app, ["init", "--agents-dir", str(agents_dir), "--database-url", _url(tmp_path)]
    )


def _strip(definition: AgentDefinition) -> dict[str, Any]:
    payload = definition.model_dump(mode="json")
    payload.pop("status")
    payload.pop("version")
    return payload


async def _get_definition_async(url: str, key: str) -> AgentDefinition | None:
    db = Database(url)
    try:
        async with db.session() as session:
            return await AgentRepository(session).get_agent(key)
    finally:
        await db.close()


def _get_definition(url: str, key: str) -> AgentDefinition | None:
    return asyncio.run(_get_definition_async(url, key))


async def _assert_seed(url: str) -> None:
    db = Database(url)
    try:
        async with db.session() as session:
            repo = ModelRepository(session)
            model = await repo.get_model("fake-chat")
            assert model is not None
            for profile in BrainProfile:
                mapped = await repo.get_brain_mapping("dev", profile)
                assert mapped is not None
                assert mapped.key == "fake-chat"
    finally:
        await db.close()


async def _remap_smart(url: str, model_key: str) -> None:
    db = Database(url)
    try:
        async with db.session() as session:
            repo = ModelRepository(session)
            await repo.upsert_model(fake_definition(key=model_key))
            await repo.set_brain_mapping("dev", BrainProfile.SMART, model_key)
            await session.commit()
    finally:
        await db.close()


async def _get_mapping_async(url: str, profile: BrainProfile) -> ModelDefinition | None:
    db = Database(url)
    try:
        async with db.session() as session:
            return await ModelRepository(session).get_brain_mapping("dev", profile)
    finally:
        await db.close()


def _get_mapping(url: str, profile: BrainProfile) -> ModelDefinition | None:
    return asyncio.run(_get_mapping_async(url, profile))


def test_init_preserves_existing_brain_mappings(tmp_path: Path) -> None:
    """槽位播种只填空位：用户已配的真实模型映射，重复 `luban init` 不打回 fake-chat。"""
    _migrate(tmp_path)
    agents_dir = tmp_path / "agents"
    _write_yaml(agents_dir, SAMPLE)
    assert _init(tmp_path, agents_dir).exit_code == 0
    url = _url(tmp_path)

    # 用户把 smart 槽位配成自己的模型（模拟工作台/API 切换真实模型）
    asyncio.run(_remap_smart(url, "my-real-model"))
    mapped = _get_mapping(url, BrainProfile.SMART)
    assert mapped is not None
    assert mapped.key == "my-real-model"

    # 再次 init（如 dev.sh 重启）→ smart 保留，其余空位照常播种
    assert _init(tmp_path, agents_dir).exit_code == 0
    mapped = _get_mapping(url, BrainProfile.SMART)
    assert mapped is not None
    assert mapped.key == "my-real-model"
    for profile in (BrainProfile.FAST, BrainProfile.CHEAP, BrainProfile.VISION):
        seeded = _get_mapping(url, profile)
        assert seeded is not None
        assert seeded.key == "fake-chat"


def test_migrate_empty_db(tmp_path: Path) -> None:
    url = _url(tmp_path)
    result = runner.invoke(app, ["migrate", "--database-url", url])
    assert result.exit_code == 0, result.output
    # 幂等：已迁移库再跑不报错
    again = runner.invoke(app, ["migrate", "--database-url", url])
    assert again.exit_code == 0, again.output
    engine = create_engine(url.replace("+aiosqlite", "+pysqlite", 1))
    tables = set(inspect(engine).get_table_names())
    assert {"agents", "agent_versions", "models", "brain_mappings"} <= tables
    engine.dispose()


def test_init_import_reimport_and_seed(tmp_path: Path) -> None:
    _migrate(tmp_path)
    agents_dir = tmp_path / "agents"
    _write_yaml(agents_dir, SAMPLE)

    first = _init(tmp_path, agents_dir)
    assert first.exit_code == 0, first.output
    assert f"{KEY}@1" in first.output

    # 库中定义字段与 YAML 映射一致
    url = _url(tmp_path)
    definition = _get_definition(url, KEY)
    assert definition is not None
    assert _strip(definition) == _strip(definition_from_yaml(SAMPLE))

    # 改 YAML 加描述再导入 → 追加新版本（版本只增不改）
    updated = {**SAMPLE, "description": "售后客服 v2"}
    _write_yaml(agents_dir, updated)
    second = _init(tmp_path, agents_dir)
    assert second.exit_code == 0, second.output
    assert f"{KEY}@2" in second.output
    current = _get_definition(url, KEY)
    assert current is not None
    assert current.version == 2
    assert current.description == "售后客服 v2"

    # seed_defaults 生效：fake-chat 与四槽位映射
    asyncio.run(_assert_seed(url))


def test_init_idempotent_unchanged_yaml(tmp_path: Path) -> None:
    """重复 init 幂等：YAML 与当前版本内容一致时不追加版本，输出 unchanged 标记（R7）。"""
    _migrate(tmp_path)
    agents_dir = tmp_path / "agents"
    _write_yaml(agents_dir, SAMPLE)

    first = _init(tmp_path, agents_dir)
    assert first.exit_code == 0, first.output
    assert f"{KEY}@1" in first.output
    assert "unchanged" not in first.output

    # 原样再 init → 版本号不变，输出标明 unchanged
    again = _init(tmp_path, agents_dir)
    assert again.exit_code == 0, again.output
    assert f"{KEY}@1 (unchanged)" in again.output
    url = _url(tmp_path)
    current = _get_definition(url, KEY)
    assert current is not None
    assert current.version == 1

    # 改 YAML 再 init → 内容有变化才追加版本
    updated = {**SAMPLE, "description": "售后客服 v2"}
    _write_yaml(agents_dir, updated)
    third = _init(tmp_path, agents_dir)
    assert third.exit_code == 0, third.output
    assert f"{KEY}@2" in third.output
    assert "unchanged" not in third.output
    current = _get_definition(url, KEY)
    assert current is not None
    assert current.version == 2


def test_export_round_trip(tmp_path: Path) -> None:
    _migrate(tmp_path)
    agents_dir = tmp_path / "agents"
    _write_yaml(agents_dir, SAMPLE)
    assert _init(tmp_path, agents_dir).exit_code == 0
    url = _url(tmp_path)
    current = _get_definition(url, KEY)
    assert current is not None

    # 导出到文件 → definition_from_yaml 与库中定义字段级一致（往返无损）
    output = tmp_path / "out.yaml"
    result = runner.invoke(
        app, ["export", KEY, "--output", str(output), "--database-url", url]
    )
    assert result.exit_code == 0, result.output
    restored = definition_from_yaml(load_yaml_file(output))
    assert _strip(restored) == _strip(current)

    # 导出到 stdout 同样往返无损
    stdout_result = runner.invoke(app, ["export", KEY, "--database-url", url])
    assert stdout_result.exit_code == 0, stdout_result.output
    restored_stdout = definition_from_yaml(yaml.safe_load(stdout_result.output))
    assert _strip(restored_stdout) == _strip(current)


def test_export_version_and_not_found(tmp_path: Path) -> None:
    _migrate(tmp_path)
    agents_dir = tmp_path / "agents"
    _write_yaml(agents_dir, SAMPLE)
    assert _init(tmp_path, agents_dir).exit_code == 0
    _write_yaml(agents_dir, {**SAMPLE, "description": "改了"})
    assert _init(tmp_path, agents_dir).exit_code == 0
    url = _url(tmp_path)

    # 指定版本导出 v1（描述仍是旧值）
    v1_result = runner.invoke(app, ["export", KEY, "--version", "1", "--database-url", url])
    assert v1_result.exit_code == 0, v1_result.output
    v1 = definition_from_yaml(yaml.safe_load(v1_result.output))
    assert v1.description == "售后客服"

    # 不存在 key → exit 1 友好报错
    missing = runner.invoke(app, ["export", "nope", "--database-url", url])
    assert missing.exit_code == 1
    assert "AGENT_NOT_FOUND" in missing.stderr

    # 不存在版本 → exit 1 友好报错
    missing_version = runner.invoke(
        app, ["export", KEY, "--version", "99", "--database-url", url]
    )
    assert missing_version.exit_code == 1
    assert "AGENT_VERSION_NOT_FOUND" in missing_version.stderr


def test_init_bad_yaml_friendly_error(tmp_path: Path) -> None:
    _migrate(tmp_path)
    url = _url(tmp_path)

    # 未知 YAML 键 → VALIDATION_ERROR 语义报错，exit 1
    unknown_dir = tmp_path / "unknown"
    _write_yaml(unknown_dir, {**SAMPLE, "bogus": 1})
    unknown = _init(tmp_path, unknown_dir)
    assert unknown.exit_code == 1
    assert "VALIDATION_ERROR" in unknown.stderr

    # 缺 name → VALIDATION_ERROR 语义报错，exit 1
    noname_dir = tmp_path / "noname"
    _write_yaml(noname_dir, {"instruction": "没有名字"})
    noname = _init(tmp_path, noname_dir)
    assert noname.exit_code == 1
    assert "VALIDATION_ERROR" in noname.stderr
    _ = url


# ---------------------------------------------------------------- eval 评测

EVAL_KEY = "eval-bot"

EVAL_AGENT: dict[str, Any] = {
    "name": EVAL_KEY,
    "description": "评测机器人",
    "instruction": "你是客服，简短回答",
    "brain": "smart",
    "tools": [],
}

EVAL_DATASET = [
    {"case": "打招呼", "input": "你好", "expect_output": {"contains": ["你好"]}},
]


def _write_dataset(tmp_path: Path, data: Any = EVAL_DATASET) -> Path:
    path = tmp_path / "dataset.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _prepare_eval_agent(tmp_path: Path) -> str:
    _migrate(tmp_path)
    agents_dir = tmp_path / "agents"
    _write_yaml(agents_dir, EVAL_AGENT)
    assert _init(tmp_path, agents_dir).exit_code == 0
    return _url(tmp_path)


async def _set_fake_script(url: str, script: list) -> None:
    db = Database(url)
    try:
        async with db.session() as session:
            await ModelRepository(session).upsert_model(
                fake_definition(key="fake-chat", script=script)
            )
    finally:
        await db.close()


async def _list_eval_results(url: str, dataset_key: str) -> list:
    db = Database(url)
    try:
        async with db.session() as session:
            return await EvalRepository(session).list_summaries(dataset_key=dataset_key)
    finally:
        await db.close()


async def _get_eval_detail(url: str, result_id: int):
    db = Database(url)
    try:
        async with db.session() as session:
            return await EvalRepository(session).get_detail(result_id)
    finally:
        await db.close()


def test_eval_run_pass_and_save(tmp_path: Path) -> None:
    url = _prepare_eval_agent(tmp_path)
    dataset = _write_dataset(tmp_path)
    report_path = tmp_path / "report.json"

    # 种子 fake-chat 缺省脚本输出含「你好」→ 用例 pass
    result = runner.invoke(
        app,
        [
            "eval", "run", f"{EVAL_KEY}@1",
            "--dataset", str(dataset),
            "--save", str(report_path),
            "--database-url", url,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "[PASS] 打招呼" in result.output
    assert "pass_rate: 1.00" in result.output
    # --save JSON 报告可回读且内容一致（model_key 随报告落盘）
    saved = EvalReport.from_json(report_path.read_text(encoding="utf-8"))
    assert saved.agent_key == EVAL_KEY and saved.pass_rate == 1.0
    assert saved.model_key == "fake-chat"
    # 完整报告同时落库（EvalRepository，R5：聚合列 + 全量逐 case 明细）
    summaries = asyncio.run(_list_eval_results(url, "dataset"))
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.agent_key == EVAL_KEY
    assert summary.agent_version == 1
    assert summary.model_key == "fake-chat"
    assert summary.pass_rate == 1.0
    assert (summary.passed_cases, summary.total_cases) == (1, 1)
    detail = asyncio.run(_get_eval_detail(url, summary.id))
    assert detail is not None
    assert detail.dataset_key == "dataset"
    assert [case.case for case in detail.results] == ["打招呼"]
    assert detail.results[0].passed is True
    assert detail.results[0].verdicts[0].kind == "output"
    assert detail.results[0].output is not None


def test_eval_run_compare_regression_exit_1(tmp_path: Path) -> None:
    url = _prepare_eval_agent(tmp_path)
    dataset = _write_dataset(tmp_path)
    baseline_path = tmp_path / "baseline.json"

    baseline = runner.invoke(
        app,
        [
            "eval", "run", EVAL_KEY,
            "--dataset", str(dataset),
            "--save", str(baseline_path),
            "--database-url", url,
        ],
    )
    assert baseline.exit_code == 0, baseline.output

    # 换掉 fake 脚本 → 同一数据集本次 fail → 对比基线命中回归 → exit 1
    asyncio.run(_set_fake_script(url, ["完全不同的回答"]))
    regressed = runner.invoke(
        app,
        [
            "eval", "run", EVAL_KEY,
            "--dataset", str(dataset),
            "--compare", str(baseline_path),
            "--database-url", url,
        ],
    )
    assert regressed.exit_code == 1, regressed.output
    assert "[FAIL] 打招呼" in regressed.output
    assert "regressions: ['打招呼']" in regressed.output


def test_eval_run_no_regression_exit_0(tmp_path: Path) -> None:
    url = _prepare_eval_agent(tmp_path)
    dataset = _write_dataset(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    args = [
        "eval", "run", EVAL_KEY,
        "--dataset", str(dataset),
        "--database-url", url,
    ]
    first = runner.invoke(app, [*args, "--save", str(baseline_path)])
    assert first.exit_code == 0, first.output
    second = runner.invoke(app, [*args, "--compare", str(baseline_path)])
    assert second.exit_code == 0, second.output
    assert "regressions: []" in second.output


def test_eval_run_agent_not_found_exit_2(tmp_path: Path) -> None:
    url = _prepare_eval_agent(tmp_path)
    dataset = _write_dataset(tmp_path)
    result = runner.invoke(
        app,
        ["eval", "run", "ghost", "--dataset", str(dataset), "--database-url", url],
    )
    assert result.exit_code == 2
    assert "AGENT_NOT_FOUND" in result.stderr


def test_eval_run_discovers_extension_tools(tmp_path: Path, monkeypatch) -> None:
    """带工具的 Agent 在独立 CLI 进程可评测：eval 构建 Runtime 前扫描 EXTENSION_PACKAGES。

    修复前回归：eval 不 discover 用户扩展 → 注册表为空 → 全 case 报「工具已注销」。
    用测试专用扩展包（tests/lubanagent/cli/_ext_tools.py）而非 app.tools，框架测试不依赖用户领地。
    """
    monkeypatch.setenv("EXTENSION_PACKAGES", "tests.lubanagent.cli._ext_tools")
    _migrate(tmp_path)
    agents_dir = tmp_path / "agents"
    _write_yaml(
        agents_dir,
        {
            "name": "eval-tool-bot",
            "description": "带工具的评测机器人",
            "instruction": "你是查询助手，调用工具后作答",
            "brain": "smart",
            "tools": ["eval_ext_lookup"],
        },
    )
    assert _init(tmp_path, agents_dir).exit_code == 0
    url = _url(tmp_path)
    # fake 脚本：第一轮调工具，第二轮输出含「查询完成」
    asyncio.run(
        _set_fake_script(
            url,
            [
                {"tool_calls": [{"name": "eval_ext_lookup", "args": {"code": "A-1"}}]},
                "查询完成：已找到 A-1",
            ],
        )
    )
    dataset = _write_dataset(
        tmp_path,
        [
            {
                "case": "带工具查询",
                "input": "查一下 A-1",
                "expect_tools": ["eval_ext_lookup"],
                "expect_tool_args": {"eval_ext_lookup": {"code": "A-1"}},
                "expect_output": {"contains": ["查询完成"]},
            }
        ],
    )
    result = runner.invoke(
        app,
        ["eval", "run", "eval-tool-bot@1", "--dataset", str(dataset), "--database-url", url],
    )
    assert result.exit_code == 0, result.output
    assert "[PASS] 带工具查询" in result.output
    assert "pass_rate: 1.00" in result.output
    assert "工具已注销" not in result.output


def test_eval_run_bootstraps_knowledge_sources(tmp_path: Path, monkeypatch) -> None:
    """带知识源的 Agent 在独立 CLI 进程可评测：按库中已有 chunk 重建 SQL connector。

    修复前回归：知识源连接器由用户领地装配期注册（API 服务进程），CLI 进程
    注册表为空 → Run 全量 CONFIG_ERROR「知识源未注册」。
    """
    monkeypatch.setenv("EXTENSION_PACKAGES", "tests.lubanagent.cli._ext_tools")
    _migrate(tmp_path)
    agents_dir = tmp_path / "agents"
    _write_yaml(
        agents_dir,
        {
            "name": "eval-knowledge-bot",
            "description": "带知识源的评测机器人",
            "instruction": "你是查询助手，依据知识库作答",
            "brain": "smart",
            "knowledge": ["eval-faq"],
        },
    )
    assert _init(tmp_path, agents_dir).exit_code == 0
    url = _url(tmp_path)
    # 灌入知识 chunk（FakeEmbedding 向量，与自举默认 embedder 同空间）
    [embedding] = asyncio.run(FakeEmbedding().embed(["归还期限为三十天，可续借一次。"]))
    asyncio.run(
        KnowledgeStore(Database(url)).add_chunks(
            "eval-faq",
            [
                {
                    "chunk_index": 0,
                    "content": "归还期限为三十天，可续借一次。",
                    "embedding": embedding,
                }
            ],
        )
    )
    asyncio.run(_set_fake_script(url, ["借期三十天，可续借一次"]))
    dataset = _write_dataset(
        tmp_path,
        [
            {
                "case": "知识问答",
                "input": "书多久要还",
                "expect_output": {"contains": ["三十天"]},
            }
        ],
    )
    result = runner.invoke(
        app,
        ["eval", "run", "eval-knowledge-bot@1", "--dataset", str(dataset), "--database-url", url],
    )
    assert result.exit_code == 0, result.output
    assert "[PASS] 知识问答" in result.output
    assert "知识源未注册" not in result.output


def test_eval_run_auto_approve_flag(tmp_path: Path, monkeypatch) -> None:
    """--auto-approve 语义：断言按「调用意图」判定（TOOL_CALL_STARTED）两态都 PASS，
    区别在执行——缺省审批型工具被闸门拦截（不写标记文件），开启后真实执行。"""
    monkeypatch.setenv("EXTENSION_PACKAGES", "tests.lubanagent.cli._ext_tools")
    _migrate(tmp_path)
    agents_dir = tmp_path / "agents"
    _write_yaml(
        agents_dir,
        {
            "name": "eval-risky-bot",
            "description": "带审批型工具的评测机器人",
            "instruction": "你是操作助手，调用工具完成操作",
            "brain": "smart",
            "tools": ["eval_ext_risky"],
        },
    )
    assert _init(tmp_path, agents_dir).exit_code == 0
    url = _url(tmp_path)
    marker = tmp_path / "executed.flag"
    dataset = _write_dataset(
        tmp_path,
        [
            {
                "case": "高风险操作",
                "input": "处理订单 A-1",
                "expect_tools": ["eval_ext_risky"],
            }
        ],
    )

    async def _script() -> None:
        await _set_fake_script(
            url,
            [
                {"tool_calls": [{"name": "eval_ext_risky", "args": {"target": str(marker)}}]},
                "操作已完成",
            ],
        )

    # 缺省：调用意图被断言捕获（PASS），但审批闸门拦截执行——标记文件不出现
    asyncio.run(_script())
    result = runner.invoke(
        app,
        ["eval", "run", "eval-risky-bot@1", "--dataset", str(dataset), "--database-url", url],
    )
    assert result.exit_code == 0, result.output
    assert "[PASS] 高风险操作" in result.output
    assert not marker.exists()

    # --auto-approve：请求即批，工具真实执行
    asyncio.run(_script())
    result = runner.invoke(
        app,
        [
            "eval", "run", "eval-risky-bot@1",
            "--dataset", str(dataset),
            "--database-url", url,
            "--auto-approve",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "[PASS] 高风险操作" in result.output
    assert marker.exists() and marker.read_text(encoding="utf-8") == "executed"
