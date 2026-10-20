"""评测运行器：真跑 AgentRuntime，断言读 DB Trace（契约 D2-1 决策 1，非 mock 假数据）。

单 case 异常不中断整集（记 error、passed=False）；error verdict（judge 解析失败）
不参与 pass/fail 聚合。runtime 在函数内懒导入：避免 runtime → observability → evals
的循环导入。
"""

from typing import TYPE_CHECKING, Any

from lubanagent.core import ErrorCode, LubanError, RunStatus, TraceEvent
from lubanagent.observability.evals.assertions import (
    AssertionVerdict,
    check_output,
    check_refusal,
    check_tool_calls,
)
from lubanagent.observability.evals.dataset import EvalCase, EvalDataset
from lubanagent.observability.evals.judge import LlmJudge, check_judge
from lubanagent.observability.evals.report import CaseResult, EvalReport
from lubanagent.persistence import TraceRepository

if TYPE_CHECKING:
    from lubanagent.runtime import AgentRuntime


def _error_dict(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, LubanError):
        return exc.to_dict()["error"]
    return LubanError(
        ErrorCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
    ).to_dict()["error"]


class EvalRunner:
    """逐 case 跑数据集并汇总报告；judge 缺省时配置 judge 的用例判 fail。"""

    def __init__(self, runtime: "AgentRuntime", *, judge: LlmJudge | None = None) -> None:
        self._runtime = runtime
        self._judge = judge

    async def run_dataset(
        self, agent_key: str, dataset: EvalDataset, *, version: int | None = None
    ) -> EvalReport:
        results: list[CaseResult] = []
        agent_version = version or 0
        for case in dataset.cases:
            result, seen_version = await self._run_case(agent_key, case, version)
            results.append(result)
            if seen_version is not None:
                agent_version = seen_version
        return EvalReport(
            dataset_key=dataset.key,
            agent_key=agent_key,
            agent_version=agent_version,
            results=results,
        )

    async def _run_case(
        self, agent_key: str, case: EvalCase, version: int | None
    ) -> tuple[CaseResult, int | None]:
        from lubanagent.runtime import RunRequest  # 懒导入（见模块 docstring）

        try:
            run = await self._runtime.run(
                RunRequest(agent_key=agent_key, user_input=case.input, version=version)
            )
            events = await self._list_events(run.id)
        except Exception as exc:
            return (
                CaseResult(
                    case=case.name,
                    passed=False,
                    verdicts=[],
                    run_id="",
                    output=None,
                    error=_error_dict(exc),
                ),
                None,
            )
        verdicts = [
            verdict
            for verdict in (
                check_tool_calls(case, events),
                check_output(case, run.output),
                check_refusal(case, events),
                await check_judge(case, self._judge, run.output),
            )
            if verdict is not None
        ]
        # error verdict 中立（judge 解析失败），不参与 pass/fail 聚合
        effective: list[AssertionVerdict] = [v for v in verdicts if v.error is None]
        passed = run.status is RunStatus.COMPLETED and all(v.passed for v in effective)
        return (
            CaseResult(
                case=case.name,
                passed=passed,
                verdicts=verdicts,
                run_id=run.id,
                output=run.output,
            ),
            run.agent_version,
        )

    async def _list_events(self, run_id: str) -> list[TraceEvent]:
        # runtime 未暴露 db 访问器；评测与 runtime 同属框架领地，直接复用其连接
        async with self._runtime._db.session() as session:
            return await TraceRepository(session).list_events(run_id)
