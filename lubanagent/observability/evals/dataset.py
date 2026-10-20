"""评测数据集：YAML 列表形态加载为领域模型（契约 D2-1，用户手册 §7）。

YAML 顶层是用例列表，用例名键为 `case`；dataset key 缺省取文件名去后缀。
领域模型（EvalCase/EvalDataset 等）已下沉 `lubanagent.core.evaluation`，
本模块保留原路径 re-export，既有导入零改动。
"""

from pathlib import Path

import yaml
from pydantic import ValidationError

from lubanagent.core import ErrorCode, LubanError
from lubanagent.core.evaluation import (
    EvalCase,
    EvalDataset,
    ExpectOutput,
    JudgeSpec,
)

__all__ = ["EvalCase", "EvalDataset", "ExpectOutput", "JudgeSpec", "load_dataset"]


def load_dataset(path: Path) -> EvalDataset:
    """加载评测数据集 YAML；格式错误一律 VALIDATION_ERROR（不抛原始 yaml/pydantic 异常）。"""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            f"评测数据集 YAML 解析失败: {path}",
            details={"path": str(path)},
        ) from exc
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            f"评测数据集顶层必须是用例列表: {path}",
            details={"path": str(path)},
        )
    try:
        cases = [EvalCase.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            f"评测数据集用例格式错误: {path}",
            details={"path": str(path), "errors": exc.errors(include_url=False)},
        ) from exc
    return EvalDataset(key=Path(path).stem, cases=cases)
