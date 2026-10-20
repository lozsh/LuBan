# 契约 D3-2：`lubanagent/memory` 记忆策略

> 来源：开发计划 D3-2、技术方案 §5.6（三种策略按 Agent 选用可关闭）、PRD F9。
> 前置：D0-1（MemoryConfig）、D1-1（仓储/迁移）。
> **范围边界**：本单元只做 memory 包本体；Runtime 接线（加载历史/回写消息/注入长期记忆）由主 agent 统一集成（D3-int）。

## 范围

- `lubanagent/memory/`：`session.py`、`summary.py`、`long_term.py`、`__init__.py`
- `lubanagent/persistence/models/`：**追加 `session_messages` 与 `long_term_memories` 表（迁移 0003，与 D3-1 的 knowledge_chunks 同迁移文件——主 agent 协调：两个 worker 各写模型与迁移，合并时若冲突以字母序重编号）**
- `tests/lubanagent/memory/`

## 契约决策

1. **会话记忆的消息载体**：新表 `session_messages`（不寄生 runs 表——runs 是执行记录，消息是对话事实）：`id` int pk、`session_id` str(128)、`run_id` str(36) nullable、`role` str(16)（"user"/"assistant"/"system"/"tool"）、`content` text、`created_at` server_default now。index(`session_id`,`id`)。
2. **摘要记忆**：新表 `session_summaries`：`id` int pk、`session_id` str(128) unique、`summary` text、`upto_message_id` int（已压缩到的消息 id）、`updated_at`。压缩触发：消息数 > `window_size` 时，把窗口外最旧消息交给摘要器（Summarizer Protocol，默认 `LlmSummarizer` 经 brain `cheap` 槽位；测试用 `FakeSummarizer`），摘要 + 窗口内消息组成上下文（KV-cache：摘要在前缀、窗口消息 append-only）。
3. **长期记忆**：新表 `long_term_memories`：`id` int pk、`agent_key` str(128)、`user_key` str(128)（业务侧用户标识）、`fact` text、`created_at`。显式写入（API/工具调用），读取按 agent_key+user_key 取最近 N 条注入（开发计划："跨会话关键事实显式写入"）。
4. 三种策略独立可关：MemoryConfig.session/summary/long_term 全 False → 零读写（代码强制）。

## 接口契约

### session.py
```python
class SessionMemory:
    def __init__(self, db: Database) -> None
    async def append(self, session_id: str, role: str, content: str, *,
                     run_id: str | None = None) -> None
    async def history(self, session_id: str, *, window_size: int) -> list[BaseMessage]
        # 最新 window_size 条，按时间升序转 langchain 消息（user→HumanMessage、assistant→AIMessage、system→SystemMessage、tool→ToolMessage(content, tool_call_id="")）
    async def count(self, session_id: str) -> int
    async def clear(self, session_id: str) -> int
```

### summary.py
```python
class Summarizer(Protocol):
    async def summarize(self, messages: list[BaseMessage], *, previous: str | None) -> str: ...
class FakeSummarizer:            # 确定性拼接截断，测试用
class LlmSummarizer:
    def __init__(self, model: BaseChatModel) -> None   # 提示词要求保留事实/决定/待办

class SummaryMemory:
    def __init__(self, db: Database, *, summarizer: Summarizer) -> None
    async def get_summary(self, session_id: str) -> str | None
    async def maybe_compress(self, session_id: str, *, window_size: int) -> bool
        # 消息数 > window_size → 取窗口外最旧消息（连同既有摘要）交 summarizer → upsert session_summaries
        # → 返回 True；否则 False。已压缩消息**不删除**（保留审计，读取时按 upto_message_id 跳过）
    async def context_prefix(self, session_id: str, *, window_size: int) -> list[BaseMessage]
        # 有摘要 → [SystemMessage("此前对话摘要：...")] + 窗口内消息；无 → 窗口内消息（经 SessionMemory）
```

### long_term.py
```python
class LongTermMemory:
    def __init__(self, db: Database) -> None
    async def remember(self, agent_key: str, user_key: str, fact: str) -> int   # 返回 id
    async def recall(self, agent_key: str, user_key: str, *, limit: int = 20) -> list[str]
    async def forget(self, agent_key: str, user_key: str, fact_id: int) -> bool
def format_facts_for_prompt(facts: list[str]) -> str
    # "已知用户信息：\n- fact1\n- fact2"（空列表 → ""，D3-int 注入 system prompt 尾部）
```

### __init__.py
re-export：SessionMemory/SummaryMemory/Summarizer/FakeSummarizer/LlmSummarizer/LongTermMemory/format_facts_for_prompt。

## 行为契约

1. 会话记忆跨轮保上下文：run1 写入 → run2 `history` 读回（代码强制）。
2. 长对话压缩：消息数超窗口 → `maybe_compress` 生成摘要且 `context_prefix` 含摘要 + 窗口内消息（代码强制）。
3. 长期记忆显式写入后跨会话可取（不同 session_id 也能 recall，代码强制）。
4. 全关闭零读写：MemoryConfig 全 False 时 D3-int 不调用本包任何方法（集成点约束，本单元以"无全局状态、不调用不工作"保证）。

## 边界契约

- memory 可 import core/config/persistence + langchain_core（消息类型）/pydantic/标准库；禁止 import runtime/api/app.*/model 具体工厂/mcp/skill/knowledge/graph/multiagent（LlmSummarizer 的模型由调用方造好注入）。
- 记忆不强制启用（架构 §3.3）。

## 验收标准（须实跑）

1. `pytest tests/lubanagent/memory/` 全绿：append/history 窗口截断与类型映射；count/clear；maybe_compress 触发与未触发两态、摘要累积（previous 传入）；context_prefix 有/无摘要两形态；long_term remember/recall 跨 session、forget；`alembic upgrade head` 含新表。
2. 全量回归绿；`ruff check lubanagent/memory` 无错。

## 存疑项与决策

1. 已压缩消息不物理删除（保留审计，读取跳过）→ 决策 2。
2. ToolMessage 的 tool_call_id 会话回放失真问题 → v1 给空串占位（LangChain 容忍），完整工具消息回放待 D3-int 按 Run Trace 重建（TODO）。
