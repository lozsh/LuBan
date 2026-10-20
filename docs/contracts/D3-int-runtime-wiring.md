# 契约 D3-int：Runtime 能力接线（RAG / 记忆 / Skill 进 Agent 主流程）

> 来源：技术方案 §5.6/§5.5、PRD F9/F12（运行期可选能力，默认不启用但都是一等能力）、D3-1/D3-2/D3-3 契约的「范围边界」节。
> 前置：D1-4（AgentRuntime）、D3-1（knowledge）、D3-2（memory）、D3-3（skill）全部完成。
> 性质：把三个能力包接进 D1-4 的运行流程；**全部默认关闭**——Agent 定义未配置对应字段时行为与 D1-4 完全一致（代码强制，回归测试锚定）。

## 范围

- `lubanagent/runtime/agent_loop.py`：接线三处（见行为契约）
- `lubanagent/runtime/capabilities.py`：**新建**（接线逻辑独立成模块，agent_loop 只调用——控制 agent_loop 体积）
- `tests/lubanagent/runtime/test_capabilities.py`：新建

## 契约决策

1. **接线点收敛为一个模块**：`capabilities.py` 暴露 `prepare(...)` 与 `finalize(...)` 两个函数，agent_loop 在「上下文组装前」与「Run 收尾前」各调一次；未配置任何能力时两函数零副作用（显式 early-return，测试锚定）。
2. **KV-cache 语义**：知识片段/长期记忆/Skill 元数据全部注入 **system prompt 尾部**（动态后置，稳定前缀不变）；摘要记忆的前缀由 SummaryMemory.context_prefix 管理（摘要在前、窗口消息在后）。
3. **Skill 工具命名空间**：`skill_load` 工具 key 冲突（用户已注册同名）→ 框架版改名 `luban:skill_load` 并记 warning。

## 接口契约

### capabilities.py
```python
@dataclass(frozen=True)
class CapabilityContext:
    """prepare 的产出：供 agent_loop 注入上下文与工具的增量。"""
    system_suffix: str = ""              # 追加到 system prompt 尾部的文本（知识片段/长期记忆/Skill 元数据，按此序）
    extra_tools: list[RegisteredTool] = []  # skill_load（agent.skills 非空时）
    history: list[BaseMessage] | None = None  # 会话/摘要记忆提供的历史（None = 不启用记忆）

async def prepare(definition: AgentDefinition, *, user_input: str, session_id: str | None,
                  user_key: str | None, db: Database, registry: Registry,
                  skill_registry: SkillRegistry | None = None) -> CapabilityContext:
    """按 definition 配置逐项装配（全部代码强制开关）：
    1. memory.session 或 memory.summary：经 SummaryMemory.context_prefix（summary=True 且配置了 summarizer）
       或 SessionMemory.history（仅 session=True）→ history；session_id 为 None → 跳过（无会话无从记忆）
    2. memory.long_term 且 user_key：LongTermMemory.recall → format_facts_for_prompt 入 suffix
    3. knowledge.sources 非空：逐 source 名 registry.get_knowledge(name).retrieve(user_input, top_k=definition.knowledge.top_k)
       → format_snippets_for_prompt 入 suffix（含引用编号说明句）；connector 未注册 → LubanError(CONFIG_ERROR)（定义引用了不存在的知识源，显式失败）
    4. skills 非空且 skill_registry 给了：format_metadata_for_prompt(definition.skills) 入 suffix
       + make_load_tool(allowed=definition.skills) 入 extra_tools
    """

async def finalize(definition: AgentDefinition, *, session_id: str | None, run_id: str,
                   user_input: str, output: str | None, db: Database) -> None:
    """Run 收尾回写（仅 memory.session/summary 且 session_id 给了时）：
    SessionMemory.append(user) + append(assistant, run_id=run_id)；
    memory.summary=True → SummaryMemory.maybe_compress(window_size=definition.memory.window_size)。
    回写失败只记日志不阻断（Run 已终态，记忆是附属）。
    """
```

### agent_loop.py 改动（最小侵入）
1. `AgentRuntime.__init__` 增加可选参：`skill_registry: SkillRegistry | None = None`、`summarizer: Summarizer | None = None`、`user_key_fn: Callable[[RunRequest], str | None] | None = None`（长期记忆的用户标识来源，缺省 None → 长期记忆跳过）。
2. stream 流程步骤 6（上下文）前：`cap_ctx = await capabilities.prepare(...)`；system prompt += cap_ctx.system_suffix；tools 列表 += cap_ctx.extra_tools；history = cap_ctx.history if not None else request.history。
3. 收尾（步骤 10 finish_run 之后）：`await capabilities.finalize(...)`。
4. **治理注入**（2026-07-23 审稿发现的真实链路缺口，补齐）：构造 RunContext 时把本 Run 的 guard 与 token 注入 `ctx.metadata["budget_guard"]` / `ctx.metadata["cancellation_token"]`；`tool/executor.py` 对签名含 `_ctx` 参数的 handler 传入 ctx（`fn(**args, _ctx=ctx)`）；`graph/tool_adapter.py` 的 flow handler 声明 `_ctx` 并 `runner.run(..., parent_ctx=_ctx)`——使「父 Agent → flow_as_tool → 图 Run」链路的预算继承与取消传播真实可达。

## 行为契约

1. **默认关闭回归**：Agent 定义 knowledge/memory/skills 全默认 → prepare 返回全空 CapabilityContext，Run 行为与 D1-4 逐事件一致（代码强制：事件序列对比测试）。
2. **会话记忆跨轮**：memory.session=True 的 Agent 同 session_id 连跑两次，第二次的模型输入含第一轮问答（FakeLLM 断言收到的 messages）。
3. **RAG 注入**：knowledge.sources=["faq"]（注册 SqlKnowledgeConnector + FakeEmbedding 灌入 FAQ）→ 模型收到的 system prompt 尾部含命中片段与 `[1]` 编号。
4. **Skill 工具**：skills=["refund-sop"] → 工具清单含 skill_load，模型调用后正文返回（经 FakeLLM script 触发 tool_call）。
5. **知识源缺失显式失败**：定义引用未注册 source → Run 失败 error=CONFIG_ERROR（不静默降级）。
6. **长期记忆**：memory.long_term=True + user_key_fn 提供 → suffix 含 facts；无 user_key → 跳过。

## 边界契约

- capabilities.py 可 import core/extension/config/persistence/runtime(仅类型)/knowledge/memory/skill + langchain_core；这是 runtime 层的接线模块，与 agent_loop 同包边界。
- 三能力未配置时零读写（PRD F9：简单项目不被迫引入组件）。

## 验收标准（须实跑，FakeLLM + tmp SQLite）

1. `pytest tests/lubanagent/runtime/test_capabilities.py` 全绿，覆盖行为契约 1-6 全部场景 + finalize 回写（append 两条 + maybe_compress 被调）+ finalize 失败不阻断 Run。
2. 全量回归绿；`ruff check lubanagent/runtime` 无错。

## 存疑项与决策

1. 会话记忆与调用方传入 history 的优先级 → 记忆启用时以 DB 历史为准（cap_ctx.history 覆盖 request.history），未启用用 request.history（决策：行为可预期）。
2. 知识检索的 query 用当前 user_input（不多轮改写，v1 最小）。
