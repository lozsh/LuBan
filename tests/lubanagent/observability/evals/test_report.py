"""报告与对比：pass_rate、JSON 往返、regressions/improvements/delta。"""

from lubanagent.observability.evals import (
    AssertionVerdict,
    CaseResult,
    EvalReport,
    compare_reports,
)


def _report(results: list[CaseResult]) -> EvalReport:
    return EvalReport(dataset_key="d", agent_key="a", agent_version=1, results=results)


def _result(case: str, passed: bool) -> CaseResult:
    return CaseResult(
        case=case,
        passed=passed,
        verdicts=[AssertionVerdict(kind="output", passed=passed)],
        run_id=f"run-{case}",
        output="o",
    )


def test_pass_rate() -> None:
    report = _report([_result("a", True), _result("b", False)])
    assert report.pass_rate == 0.5
    assert _report([]).pass_rate == 0.0


def test_json_round_trip() -> None:
    report = _report([_result("用例一", True), _result("用例二", False)])
    restored = EvalReport.from_json(report.to_json())
    assert restored == report
    assert restored.pass_rate == 0.5


def test_compare_reports_regression_and_improvement() -> None:
    baseline = _report(
        [_result("曾经过", True), _result("曾经挂", False), _result("稳定过", True)]
    )
    current = _report(
        [_result("曾经过", False), _result("曾经挂", True), _result("稳定过", True)]
    )
    comparison = compare_reports(baseline, current)
    assert comparison.regressions == ["曾经过"]
    assert comparison.improvements == ["曾经挂"]
    assert comparison.pass_rate_delta == 0.0


def test_compare_reports_pass_rate_delta() -> None:
    baseline = _report([_result("a", True), _result("b", True)])
    current = _report([_result("a", True), _result("b", False)])
    comparison = compare_reports(baseline, current)
    assert comparison.regressions == ["b"]
    assert comparison.improvements == []
    assert comparison.pass_rate_delta == -0.5
