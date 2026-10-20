"""数据集加载：YAML 列表形态、key 缺省取文件名、非法输入统一 VALIDATION_ERROR。"""

from pathlib import Path

import pytest
import yaml

from lubanagent.core import ErrorCode, LubanError
from lubanagent.observability.evals import load_dataset

FULL_DATASET = [
    {
        "case": "查不到订单要安抚并给替代方案",
        "input": "我的订单 12345 怎么还没到",
        "expect_tools": ["query_order"],
        "expect_tool_args": {"query_order": {"order_id": "12345"}},
        "expect_output": {"contains": ["安抚", "替代"]},
        "judge": {"criteria": "先安抚情绪，再给解决方案", "pass_score": 4},
    },
    {"case": "只给输入的最小用例", "input": "你好"},
]


def _write(tmp_path: Path, data: object, filename: str = "orders.yaml") -> Path:
    path = tmp_path / filename
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_load_full_dataset(tmp_path: Path) -> None:
    dataset = load_dataset(_write(tmp_path, FULL_DATASET))
    assert dataset.key == "orders"  # key 缺省 = 文件名去后缀
    assert dataset.name == ""
    assert len(dataset.cases) == 2

    full = dataset.cases[0]
    assert full.name == "查不到订单要安抚并给替代方案"
    assert full.input == "我的订单 12345 怎么还没到"
    assert full.expect_tools == ["query_order"]
    assert full.expect_tool_args == {"query_order": {"order_id": "12345"}}
    assert full.expect_output is not None and full.expect_output.contains == ["安抚", "替代"]
    assert full.judge is not None
    assert full.judge.criteria == "先安抚情绪，再给解决方案"
    assert full.judge.pass_score == 4

    minimal = dataset.cases[1]
    assert minimal.expect_tools == []
    assert minimal.expect_tool_args == {}
    assert minimal.expect_output is None
    assert minimal.judge is None


def test_judge_pass_score_default(tmp_path: Path) -> None:
    data = [{"case": "c", "input": "i", "judge": {"criteria": "标准"}}]
    dataset = load_dataset(_write(tmp_path, data))
    assert dataset.cases[0].judge is not None
    assert dataset.cases[0].judge.pass_score == 4


def test_invalid_dataset_raises_validation_error(tmp_path: Path) -> None:
    # 顶层不是列表
    with pytest.raises(LubanError) as not_list:
        load_dataset(_write(tmp_path, {"cases": []}))
    assert not_list.value.code is ErrorCode.VALIDATION_ERROR

    # 列表项不是映射
    with pytest.raises(LubanError) as not_mapping:
        load_dataset(_write(tmp_path, ["只是一串文本"]))
    assert not_mapping.value.code is ErrorCode.VALIDATION_ERROR

    # 缺必填 input
    with pytest.raises(LubanError) as missing_input:
        load_dataset(_write(tmp_path, [{"case": "没有输入"}]))
    assert missing_input.value.code is ErrorCode.VALIDATION_ERROR

    # YAML 语法错误
    with pytest.raises(LubanError) as bad_yaml:
        load_dataset(_write(tmp_path, "- case: x\n  input: [未闭合"))
    assert bad_yaml.value.code is ErrorCode.VALIDATION_ERROR
