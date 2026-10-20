"""退款政策单点配置（修复风险登记 R9：审批阈值三处硬编码）。

「退款金额 > N 元触发人工审批」的口径此前重复定义在三处、无统一来源：
Tool 描述（app/tools/customer_service/refund.py）、Agent 指令
（app/agents/customer_service.yaml、cs_refund.yaml）、图分支
（app/graphs/refund_flow.py）。本模块是该口径的唯一代码来源，
Tool 与 Graph 均 import 本常量，改一处即改两处。

同步约定（重要）：
Agent 指令是 YAML 静态文本，无法 import 本常量——框架的指令变量注入机制
（ContextBuilder 的 prompt_variables + format_map）不经 YAML 导入链
（yaml_io 不支持 prompt_variables 键，扩展它需改框架领地），故不走注入。
改 REFUND_APPROVAL_THRESHOLD 时必须同步修改两个 Agent YAML 指令中的
阈值数字，守卫测试 tests/demos/customer_service/test_refund_policy.py
会校验 YAML 数字与本常量一致，漏改即测试红。
"""

# 退款审批阈值（元）：金额「严格大于」该值时触发人工审批（判定口径是 >，非 >=）
REFUND_APPROVAL_THRESHOLD = 500.0
