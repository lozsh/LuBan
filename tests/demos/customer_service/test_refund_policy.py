"""退款审批阈值同步守卫（修复风险登记 R9 的配套防线）。

REFUND_APPROVAL_THRESHOLD（app/config/refund_policy.py）是「> N 元触发人工审批」
口径的唯一代码来源。Tool 描述与 Graph 分支/审批提示已 import 该常量（编译期绑定，
天然同步）；Agent 指令是 YAML 静态文本无法 import——本测试守卫 YAML 与常量一致，
改常量漏改 YAML 时测试红，逼同步。
"""

from pathlib import Path

import yaml

from app.config.refund_policy import REFUND_APPROVAL_THRESHOLD

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGENTS_DIR = _REPO_ROOT / "app" / "agents"
# 口径在指令文本中的写法：「金额 > 500 元」（与 YAML 现行文案逐字对齐）
_EXPECTED_PHRASE = f"> {REFUND_APPROVAL_THRESHOLD:g} 元"

_AGENT_YAMLS = ("customer_service.yaml", "cs_refund.yaml")


def _instruction(filename: str) -> str:
    """读 Agent YAML 的 instruction 段（YAML 是初始化导入源，指令本体落地）。"""
    data = yaml.safe_load((_AGENTS_DIR / filename).read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{filename} 应是 Agent 定义映射"
    return str(data["instruction"])


def test_agent_instruction_threshold_matches_constant():
    """两个 Agent 指令中的阈值数字必须与 REFUND_APPROVAL_THRESHOLD 一致。"""
    for filename in _AGENT_YAMLS:
        instruction = _instruction(filename)
        assert _EXPECTED_PHRASE in instruction, (
            f"{filename} 指令中未找到「{_EXPECTED_PHRASE}」——"
            "改 REFUND_APPROVAL_THRESHOLD 时漏改了 Agent YAML 指令（口径三处需同步，"
            "见 app/config/refund_policy.py 注释）"
        )


def test_tool_description_renders_constant():
    """Tool 描述由常量渲染（运行期取注册描述符验证，防绕过常量重新硬编码）。"""
    import app.tools.customer_service.refund  # noqa: F401  # 导入即注册
    from lubanagent.extension import default_registry

    registered = default_registry.get_tool("refund_apply")
    assert registered is not None, "refund_apply 应已注册到 default_registry"
    description = registered.descriptor.description
    assert f">{REFUND_APPROVAL_THRESHOLD:g}元触发人工审批" in description


def test_graph_human_prompt_renders_constant():
    """图 human 审批节点提示由常量渲染（经 structure() 只读快照验证）。"""
    from app.graphs.refund_flow import _build_refund_flow

    structure = _build_refund_flow().structure()
    approve = next(node for node in structure.nodes if node.name == "approve")
    assert approve.prompt is not None
    assert f">{REFUND_APPROVAL_THRESHOLD:g} 元" in approve.prompt


def test_no_threshold_hardcode_left_in_code():
    """Tool / Graph 源码中不得残留阈值数字硬编码（必须走常量）。"""
    sources = {
        "app/tools/customer_service/refund.py": _REPO_ROOT
        / "app"
        / "tools"
        / "customer_service"
        / "refund.py",
        "app/graphs/refund_flow.py": _REPO_ROOT / "app" / "graphs" / "refund_flow.py",
    }
    for label, path in sources.items():
        text = path.read_text(encoding="utf-8")
        assert "500" not in text, (
            f"{label} 中残留阈值数字硬编码——请改用 "
            "app.config.refund_policy.REFUND_APPROVAL_THRESHOLD"
        )
