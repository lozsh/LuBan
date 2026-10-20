# 第 7 课：确定性流程与多 Agent——图编排和咨询台团队

> 学完本课你能：读懂并用 GraphBuilder 写一条含人工审批节点的确定性流程；搭一个 supervisor 分诊团队并验证路由正确；说清单 Agent / 图 / 团队各自该用在哪 ｜ 预计耗时 60-90 分钟 ｜ 前置：第 6 课 ｜ 难度 ★★★

这是最硬核的一课，但你会发现图里的每个零件都是老朋友：图节点里的 `agent` 跑的是普通 Agent（前六课的主角），`step` 是普通 Python 函数（第 4 课你就在写），人工节点用的是审批（第 4 课你配过）。新东西只有"把这些零件按图的结构组织起来"这一层。

先交代为什么需要这层。到第 6 课为止，小图的一切行为都是"模型决策"：调不调工具、答不答、怎么答。这对大部分场景够用——但有类场景不行：**顺序和规则本身不能出错的事**。图书馆的逾期处理就是：查逾期 → 分级 → 严重的要人工确认才能冻结 → 算滞纳金 → 通知。"严重逾期必须先有人确认才能冻结权限"是规章，不是建议。你把这套流程写进 prompt 里"请务必按以下步骤……"，模型大概率遵守——但第 0 课就说过，**大概率在涉及权限和金钱的地方不够格**。图编排把这些规则从"请求"变成"结构"：流程是图，条件是分支，人工节点是闸，模型只在需要理解语言的节点出场。

把"不够格"再拆狠一点：prompt 里的流程约定，本质是你在请求一个概率系统"大部分时候别乱来"。它没有强制力——模型上下文被撑长、用户一句话绕路、某次采样抽风，步骤就可能被跳过。而冻结借阅权限这种动作，错一次的成本是学生周一借不了书、投诉到馆长那里。**凡是"错一步就要人善后"的流程，都不该寄存在概率里。**

**带着三个问题往下读**，本课结尾揭晓答案：

1. 冻结权限前的人工确认，为什么是让**整个流程原地暂停**（interrupt），而不是后台轮询一个"等审批"状态？
2. 图的节点里跑着 Agent、调着工具——你第 4 课配的审批、第 6 课配的预算治理，进了图还会不会管用？
3. supervisor 为什么被要求输出 **JSON**，而不是一段自然语言？

## 1. 学习目标

- 用 GraphBuilder 七件套（entry/step/human/when/edge/build/@graph）写一条流程
- 跑通含人工节点的流程：interrupt 暂停 → 人工给值 → resume 续跑
- 搭 supervisor 团队（调度 Agent + 专家成员），验证路由正确性
- 对"单 Agent / 图 / 团队"三选一有自己的判断框架

## 2. 概念讲解：把"流程"从 Prompt 挪进代码

**图编排的零件表**（对照 `app/graphs/refund_flow.py` 看一遍再往下读）：

| DSL | 干什么 | 对应你已会的东西 |
|---|---|---|
| `builder.entry(name)` | 指定入口节点 | — |
| `builder.step(name, fn)` | 确定性步骤：`fn(state: dict) -> dict`，返回值合并进 state | 第 4 课的工具函数同款形态 |
| `builder.agent(name, agent_key)` | 节点 = 跑一个 Agent | 前六课的 Agent |
| `builder.human(name, prompt=...)` | 人工节点：流程**暂停**等人给值，值写进 `state[name]` | 第 4 课的审批（同一个 interrupt 机制） |
| `builder.when(src, cond, then=..., otherwise=...)` | 条件分支：`cond(state)` 真假走两条边 | 第 4 课的风险定级逻辑 |
| `builder.edge(a, b)` | 普通连线 | — |
| `@graph(name=...)` | 注册进 Registry，工作台流程图页可见 | `@tool` 的同款 |

一句话：**step 是代码、agent 是模型、human 是人、when 是分支**——一张图把三种执行者按业务流程编排起来，谁在哪步出场写得死死的。

这张零件表还有一层深意：**它逼你在写图之前先做执行者分配。** 流程里每一步，你得先回答"这步是代码能定的、还是必须模型理解的、还是必须人拍板的"——滞纳金几钱是算术，代码算；用户逾期原因怎么措辞是语言，模型答；冻不冻权限是责任，人来担。很多"Agent 不靠谱"的问题，根源是没做这个分配，把本该代码拍板的事推给了模型。

两个容易踩的结构规则：① 同一节点**不能同时有普通边和条件边**（出边要么确定要么二选一，不能三心二意）；② 汇点自动接 END，不用写 `edge(x, END)`。

**跑一条图**：图没有独立的 HTTP 运行入口（`/v1/graphs` 是只读结构视图），跑图用 `GraphRunner`——测试、脚本、或把它包成 Agent 的工具（`flow_as_tool`，延伸阅读里点到）。本课用一个小脚本直跑，恰好让你零距离看到 interrupt/resume 的原始形态。

**多 Agent 团队**是另一个方向的问题。当小图既要办事务（查借阅、挂失）又要答规章，一个 Agent 全扛的下场是人设越来越长、互相干扰。团队做法：一个 **supervisor**（调度员）读用户请求，输出一个 JSON 决策——`{"action": "delegate", "agent": "my-lib-rules", "input": "..."}` 或 `{"action": "answer", ...}`——框架据此把请求转给对应成员，成员的结果回到 supervisor 汇总答复。

注意 supervisor 的产出是**结构化决策，不是自然语言**——路由是数据不是文章。它的指令里那段"只输出一个 JSON 对象"的输出格式**不是能不能跑的前提**：框架的路由提示词本身就内置了这条指令和动作样例，YAML 里省掉这段团队照样能路由——只是模型偶尔夹带多余文字时，解析失败的降级兜底会多触发。建议还是加上，做双重强化（本课参考产出里 supervisor YAML 就是这么写的，照着抄）。防失控有三道：`max_delegations`（委派上限，防两个 Agent 互相踢皮球死循环）、成员必须在登记表里、路由失败降级为直接答复。

**什么时候用哪个**（先给个起步框架，练习 C 会逼你自己长出更细的判断）：

- **单 Agent**：默认选项。一个场景、一套工具、人设写得清楚——多数需求到这为止。
- **图编排**：流程有不可出错的顺序/分支/人工环节（钱、权限、合规）。
- **团队**：多个**异质**场景挤在一个 Agent 里互相干扰（事务 vs 知识 vs …），且量级值得为之付额外延迟和 token。

### 框架为什么这样设计：人工节点是 interrupt 暂停，不是轮询

先想清楚轮询方案长什么样：流程跑到审批节点，把 Run 标成"等审批"，进程继续活着，每隔几秒查一次数据库——"批了吗？批了吗？批了吗？"听起来能跑，但你立刻会撞上三个问题：

**第一，等待的时长是"人的时间尺度"。** 逾期冻结的确认可能几分钟，也可能馆长休假三天后回来才批。让一个进程为了等人类点头空转三天，资源全烧在"查了吗"上——这不是工程，是守灵。

**第二，状态活在哪里？** 轮询方案的 Run 状态在内存里，进程重启一次、机器发版一次，"跑到一半的流程"就没了。而 interrupt 方案下，暂停那一刻的 state 快照**已经持久化**（挂在 `thread_id` 上，这就是第 4 课审批同一个机制的底座）；resume 时从检查点原地续跑——中间进程死八次都无所谓。**暂停必须是"落盘的暂停"，才配得上人的响应速度。**

**第三，暂停的语义必须是"下游一个都不跑"。** 轮询方案里，"审批中"只是一个状态标签，下游代码怎么拦得住？拦不干净就漏执行。interrupt 是引擎级的停：Run 状态就是 `interrupted`，confirm_freeze 之后的 apply_penalty、notify **在代码层面不可达**——不是"约定不执行"，是"结构上到不了"。回看第 4 课你配的高风险工具审批，用的是同一个 interrupt 机制——框架没有为图再造一套"等人工"，因为等人工这件事，从工具层到流程层只需要一种正确做法。

### 框架为什么这样设计：编排 Run 与单 Agent Run 共用一套治理

问题 2 的答案藏在架构里。图里的 `agent` 节点跑的是普通 Agent，团队里的成员也是普通 Agent——既然被编排的还是同一批 Agent，**治理凭什么换一套？** 框架的选择是：不换。具体兑现为三件事：

- **每个子 Run 进同一本 Trace**——第 7 步你要去 Traces 页看的，正是图 Run 和 Agent Run 躺在同一个列表里，各自绑定定义版本与实际模型；
- **预算和取消沿调用链传播**——外层 Run 熔断，里头的子 Run 跟着停；不存在"父 Run 取消了、子 Agent 还在烧 token"的孤岛；
- **评测回归照常罩得住**——第 6 课的 `--compare` 门禁，对团队 Run 同样成立。

反面方案是"多 Agent 另起一套治理"：单独的监控口径、单独的预算逻辑、单独的取消机制——很快你就会拥有两套互相对不上的真相，排障时先花半小时确认"这会儿该信哪套"。**一套治理不是省事，是 correctness：被编排的和单独跑的是同一批东西，它们的规矩就必须是同一份。**

### 框架为什么这样设计：路由是 JSON 数据，不是自然语言

问题 3 的答案在"谁消费 supervisor 的输出"。自然语言路由意味着：supervisor 写一段"我觉得应该交给规章专家，因为……"，然后框架还得再请一个模型读懂这段话、提取目的地——两次模型调用，两次理解偏差，路由延迟翻倍。JSON 把决策压成可解析的数据：`action` 是枚举、`agent` 是登记表里的 key、`input` 是转述文本，框架拿到就能确定性分发，不用"理解"。

这也解释了三道防失控的设计：`max_delegations` 是**数据上的护栏**（委派次数是结构化事件，数得清、拦得住——自然语言路由你连"它委派了几次"都数不准）；"成员必须在登记表里"是**引用完整性**（JSON 里的 agent key 查无此人就拒绝，等价于数据库外键约束）；解析失败降级为直接答复是**兜底保可用**（宁可答得差，不能答不出）。你写的 YAML 里那段输出格式说明是双重强化——框架提示词已内置 JSON 要求，模型偶尔跑偏时，解析失败的降级路径会兜住，但兜底触发得越少越好。

## 3. 动手：主线步骤

### 步骤 1：写逾期处理图

新建 `app/graphs/my_library_flow.py`（参考产出是终版；关键是理解结构——四个节点、一条条件边）：

```python
"""逾期处理流水线：查逾期 → 分级 → 严重逾期人工确认 → 处罚 → 通知。"""

from typing import Any
from datetime import date

from lubanagent.extension import default_registry, graph
from lubanagent.graph import GraphBuilder

SEVERE_OVERDUE_DAYS = 30  # 严重逾期线：超过则冻结借阅权（需人工确认）


def _load_overdue(state: dict[str, Any]) -> dict[str, Any]:
    """按学号查借阅记录，算出逾期最重的一册（复用第 4 课的工具，不绕过治理）。"""
    tool = default_registry.get_tool("my_lib_lookup")
    records = tool.handler(student_id=state["student_id"])["records"]
    today = date(2026, 9, 9)  # 教程演示：固定"今天"，真实系统用 date.today()
    worst = max(records, key=lambda r: (today - date.fromisoformat(r["due"])).days)
    overdue_days = (today - date.fromisoformat(worst["due"])).days
    return {"worst_title": worst["title"], "overdue_days": overdue_days}


def _needs_confirm(state: dict[str, Any]) -> bool:
    return state["overdue_days"] > SEVERE_OVERDUE_DAYS


def _apply_penalty(state: dict[str, Any]) -> dict[str, Any]:
    days = state["overdue_days"]
    # 注意：frozen 由逾期天数算出，不读 confirm_freeze 节点的人工确认值——
    # 人工拒绝了照样冻结。这根线故意没接，练习 B 要你补上（见步骤 3 的说明）。
    return {"penalty": round(days * 0.1, 1), "frozen": days > SEVERE_OVERDUE_DAYS}


def _notify(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("frozen"):
        return {"message": f"《{state['worst_title']}》逾期 {state['overdue_days']} 天，"
                            f"滞纳金 {state['penalty']} 元，借阅权限已冻结（缴清后恢复）。"}
    return {"message": f"《{state['worst_title']}》逾期 {state['overdue_days']} 天，"
                        f"滞纳金 {state['penalty']} 元，请尽快归还。"}


def _build() -> "GraphBuilder":
    builder = GraphBuilder("my_library_flow", description="逾期处理：查逾期→分级→严重确认→处罚→通知")
    builder.entry("load_overdue")
    builder.step("load_overdue", _load_overdue)
    builder.human("confirm_freeze", prompt=f"逾期超过 {SEVERE_OVERDUE_DAYS} 天将冻结借阅权限，需人工确认")
    builder.step("apply_penalty", _apply_penalty)
    builder.step("notify", _notify)
    # entry 节点只出条件边：严重逾期走人工确认，轻度直走处罚（同一节点不能混两种边）
    builder.when("load_overdue", _needs_confirm, then="confirm_freeze", otherwise="apply_penalty")
    builder.edge("confirm_freeze", "apply_penalty")
    builder.edge("apply_penalty", "notify")
    return builder


@graph(name="my_library_flow", description="逾期处理：查逾期→分级→严重确认→处罚→通知")
def my_library_flow_builder() -> Any:
    return _build()
```

对照概念表读一遍结构：查逾期（step，复用第 4 课工具）→ `when` 按逾期天数分流 → 严重的进 `human`（冻结前必须有人确认）→ 处罚（step，规则是代码算的，不是模型说的）→ 通知（step）。

再看一眼每个函数的**签名形态**：收 `state: dict`、返回 `dict`、返回值合并进 state——这就是零件表里说的"第 4 课工具函数同款"。整个文件没有一个模型调用、没有一段 prompt：**这条流程里的确定性部分，一个字的概率都不掺。** 唯一的"智能"留给分流条件之外的地方——而本图恰好连模型都不需要，这正是它的美德：能不用模型的地方不用，流程才硬。

✅ 检查点：`bash scripts/dev.sh restart` 后，工作台**流程图页**出现 `my_library_flow`，点开看到四个节点（step 蓝 / human 橙）和一条虚线双出边的条件分支。也可用 API 看：`curl http://localhost:8000/v1/graphs/my_library_flow`。

### 步骤 2：跑轻度逾期——直走全流程

图用 `GraphRunner` 跑。新建脚本 `run_flow.py`（放仓库根目录，跑完可删；这是看图运行原始形态的最短路径）：

```python
"""跑逾期处理图：轻度直走 / 重度人工确认。"""
import asyncio
import sys

sys.path.insert(0, ".")
from lubanagent.config import Settings
from lubanagent.extension import default_registry, discover
from lubanagent.graph import GraphRunner
from lubanagent.persistence import Database

from app.graphs.my_library_flow import _build

discover(Settings.load().extension_packages)


async def main():
    db = Database(Settings.load().database_url)
    runner = GraphRunner(db=db, registry=default_registry)
    flow = _build().build()

    r1 = await runner.run(flow, {"student_id": "2023055678"})
    print("轻度逾期:", r1.status, r1.output.get("message"))

    await db.close()


asyncio.run(main())
```

这个脚本本身就是一节小课：`discover()` 把你的扩展领地（工具、图）装进 Registry——和框架服务启动时做的是同一件事；`GraphRunner(db=...)` 里塞了数据库——图 Run 的 Trace 和 interrupt 检查点都要落库，这就是"同库同治理"的入口。你在这里看到的每一个零件，生产环境里的服务进程用的也是同一个。

（学生 `2023055678` 是马上要加进工具数据的轻度逾期读者——先换下面的数据再跑。）

先给 `app/tools/my_library/lookup.py` 的 `_BORROWS` 换成含两个读者的版本（终版数据就是下面这段，直接照抄）：

```python
_BORROWS: dict[str, list[dict]] = {
    "2023011234": [
        {"title": "三体", "due": "2026-08-30", "renewals_left": 1},
        {"title": "机器学习系统设计", "due": "2026-08-01", "renewals_left": 0},
    ],
    "2023055678": [
        {"title": "计算机网络", "due": "2026-09-05", "renewals_left": 1},
    ],
}
```

然后跑（工具数据变了**不用重启服务**——`run_flow.py` 是独立进程，自己 `discover()` 加载工具，改完源码它跑起来就是新的；重启服务只影响工作台页面看到的视图）：

```bash
.venv/bin/python run_flow.py
```

✅ 你应该看到：`轻度逾期: completed 《计算机网络》逾期 4 天，滞纳金 0.4 元，请尽快归还。`——四步直通，没碰人工节点（4 天 < 30 天，`when` 走了 otherwise 边）。滞纳金 0.4 是代码算的（4 × 0.1），不是模型"觉得"的。

**"不是模型觉得的"六个字，值得加粗到这一课的封面上。** 同样一句"滞纳金多少钱"，交给模型算，40 天 × 0.1 它可能说 3.9 也可能说 4 块——每次采样都是一场小型赌博；交给 `round(days * 0.1, 1)`，宇宙热寂之前答案都不会变。图编排的价值不在于"流程图画得好看"，在于**把每一分钱的出处钉死在代码行上**。

### 步骤 3：跑重度逾期——interrupt 暂停

往脚本里加第二段：

```python
    r2 = await runner.run(flow, {"student_id": "2023011234"})
    print("重度逾期:", r2.status, r2.interrupt)

    r3 = await runner.resume(flow, r2.thread_id, True)
    print("人工确认后:", r3.status, r3.output.get("message"))
```

再跑一遍。

✅ 你应该看到三行——

```
轻度逾期: completed 《计算机网络》逾期 4 天，滞纳金 0.4 元，请尽快归还。
重度逾期: interrupted {'node': 'confirm_freeze', 'prompt': '逾期超过 30 天将冻结借阅权限，需人工确认', 'state': {..., 'overdue_days': 39}}
人工确认后: completed 《机器学习系统设计》逾期 39 天，滞纳金 3.9 元，借阅权限已冻结（缴清后恢复）。
```

逐行读：重度逾期的 Run 状态是 **interrupted**——流程在 `confirm_freeze` 节点停住，`interrupt` 里带着节点名、提示语和当时的 state 快照；`resume(flow, thread_id, True)` 把人工决定（True=确认冻结）续进去，流程走完。**在"确认"发生之前，冻结在代码层面就不可能发生**——这就是把流程从 Prompt 挪进代码的全部意义。

把刚才这三行输出对照概念节的 interrupt 三原则验收一遍：状态是落盘的（`thread_id` 拿着就能在任何时刻续），下游是不可达的（39 天逾期、`interrupted` 状态下 notify 的冻结文案压根没生成），人工给值是显式的（`True` 是你亲手传进去的，不是框架"替你默认"的）。三个原则这次都摸到了实物。

把 resume 的第三个参数换成 `False` 试试，看流程怎么走（习题里有一问）。先剧透结果：**拒绝会被忽略**——`_apply_penalty` 里的 `frozen` 是用 `days > SEVERE_OVERDUE_DAYS` 自己算的，人工确认值只写进了 `state["confirm_freeze"]`，没接进处罚逻辑，所以人拒绝了流程照样冻结。这是参考产出特意留下的洞，练习 B 要你补的就是这根线。

顺带看一眼工作台 **Traces 页**：两次图 Run 各自留了完整现场（每个节点的执行都有记录）——图 Run 与 Agent Run 同库同治理，第 0 课的承诺在这里兑现。这也顺手回答了钩子问题 2 的一半：你的图 Run 没有因为"换了个跑法"就掉出治理视野——它和前六课的 Agent Run 躺在同一个列表里，用的是同一套追溯语言。

### 步骤 4：写 supervisor 和规章专家

团队需要新角色。新建 `app/agents/my_lib_triage.yaml`（supervisor，**输出 JSON 决策**是核心，完整版见参考产出）：

```yaml
name: my-lib-triage
description: 图书馆咨询台 supervisor——判断请求类型并委派成员或直接答复
instruction: |
  # 角色
  你是图书馆咨询台调度「小台」，负责判断读者请求类型，
  决定自己答复还是委派给团队成员。你不解决具体问题，只做准确分流。

  # 判断规则（按优先级）
  1. 借阅记录查询、校园卡挂失、逾期处理 → 委派 my-lib-assistant（借阅事务专员）。
  2. 规章制度咨询（借期/续借/滞纳金/开放时间）→ 委派 my-lib-rules（规章专家）。
  3. 问候、闲聊、感谢 → 自己简短答复，并提示你能转接图书馆业务。
  4. 请求不明确 → 追问一句确认意图，再分流。

  # 委派要点
  - 转述请求时带上全部关键信息（学号、书名、诉求原话），让成员不用再问一遍。

  # 输出格式（必须严格遵守）
  只输出一个 JSON 对象，不要输出其他内容，不要用 markdown 代码块包裹：
  - 自己回答：{"action": "answer", "answer": "<最终答复>"}
  - 委派成员：{"action": "delegate", "agent": "<成员 key>", "input": "<转述请求>"}

  # 边界
  - 一次只做一个动作（answer 或 delegate 二选一），不连环委派。
```

注意"委派要点"那条——**转述带全关键信息**。supervisor 的价值一半在分流，另一半在**上下文搬运**：读者第一句话说"我是学号 2023011234 的学生"，如果委派到 my-lib-assistant 时学号丢了，成员就得反问一遍，用户体验直接退回电话客服转接时代。把"转述带全上下文"写进指令，就是把这个工程要求钉进调度员的行为里。

新建 `app/agents/my_lib_rules.yaml`（规章专家：纯 RAG + 守门，knowledge 段照抄第 5 课——完整版见参考产出）。

导入：

```bash
.venv/bin/luban init
```

✅ 你应该看到：`my-lib-rules@1`、`my-lib-triage@1` 两行新导入。

### 步骤 5：组队

新建 `app/multiagents/my_library_team.py`：

```python
"""图书馆咨询台团队：triage 调度 + 借阅事务/规章专家两成员。"""

from lubanagent.multiagent import Team, TeamBuilder


def build_my_library_team() -> Team:
    builder = TeamBuilder(
        "my-library-team", description="图书馆咨询台：triage 调度 + 事务/规章成员"
    )
    builder.supervisor("my-lib-triage")
    builder.agent("my-lib-assistant", description="借阅记录查询、校园卡挂失、逾期处理")
    builder.agent("my-lib-rules", description="借阅规章、续借规则、滞纳金、开放时间咨询")
    builder.route(max_delegations=4)
    return builder.build()
```

结构就这么多：调度是谁（supervisor）、成员有谁和各自管什么（description 就是给调度看的路由提示）、防失控上限（max_delegations）。

最后一行再咀嚼一下：**成员的 description 不是写给人看的文档，是写给 supervisor 看的路由说明书。** 两个成员的 description 里"借阅记录/挂失"和"规章/续借/滞纳金"泾渭分明——这正是常见坑里"路由总给同一个成员"的解药提前埋下：路由质量的上限，在组队这一天就由这几行 description 决定了。

### 步骤 6：给团队开一个 HTTP 入口

团队的运行入口是**用户领地自定义路由**（框架不预置团队端点——`app/api/teams.py` 里内置客服的 `/v1/teams/cs-team/runs` 就是这个模式的示范）。在 `app/api/teams.py` 末尾**照抄模式加一段**：

```python
# 用户领地自定义路由模式（照抄上面 cs-team 的写法加自己的团队端点）：
@router.post("/my-library-team/runs", response_model=TeamRunResponse)
async def run_my_library_team(body: TeamRunRequest, ctx: CtxDep) -> TeamRunResponse:
    """触发图书馆咨询台团队（教程第 7 课：用户领地自定义团队路由示范）。"""
    from app.multiagents.my_library_team import build_my_library_team

    team = build_my_library_team()
    runner = TeamRunner(db=ctx.db, runtime=ctx.runtime, registry=ctx.registry)
    thread_id = body.thread_id or uuid.uuid4().hex
    result = await runner.run(team, body.input, thread_id=thread_id)
    return TeamRunResponse(
        status=result.status,
        output=result.output,
        run_id=result.run_id,
        thread_id=result.thread_id,
    )
```

为什么框架不预置团队端点？因为**暴露方式是业务决策，不是框架决策**：你的团队要挂在内网网关后面还是要走自家业务系统的鉴权，只有你知道。框架把 `TeamRunner` 和 `ctx`（数据库、运行时、Registry）都递到你手上，接不接、怎么接、暴露成什么路径，是用户领地的事——这和第 1 课说的领地隔离一脉相承：框架管引擎，你管门面。

重启服务，然后测路由：

```bash
curl -X POST http://localhost:8000/v1/teams/my-library-team/runs \
  -H "Content-Type: application/json" \
  -d '{"input": "我是学号 2023011234，帮我查下借的书"}'
```

✅ 你应该看到：返回 `status: completed`，output 的 messages 里能看到完整路由过程——supervisor 决定委派 my-lib-assistant（转述里带着学号）、成员调 my_lib_lookup 拿到真实借阅记录作答、supervisor 汇总回复。再问一个规章问题（`书可以续借几次？`），确认这次被路由到 **my-lib-rules**。

> 一个值得知道的幕后：这套团队机制在 0.1.0 上其实跑不通——supervisor 指令里的 JSON 样例会撞上框架的变量渲染模板直接报错（已修复，登记为风险 R13）。你此刻的顺利，是踩平了坑之后的顺利。

留意第二个问题的路由：`书可以续借几次？`没有任何学号、没有任何事务动作，纯粹的知识咨询——supervisor 把它分给了 my-lib-rules。**同一个团队入口，两类请求，两条路径，零人工干预**——这就是"分而治之"落地的样子。回头想想单 Agent 方案的代价：这两个问题原本会挤在同一段人设里，事务指令和规章口径互相争抢上下文。

## 4. 完成自检清单

- [ ] 流程图页看到 my_library_flow 的节点拓扑（step/human 着色、条件分支虚线）
- [ ] 轻度逾期直走 completed，滞纳金由代码算出
- [ ] 重度逾期 interrupted 在 confirm_freeze，resume(True) 后 completed 且冻结
- [ ] resume(False) 的行为亲手看过（练习里有问）
- [ ] 团队两种问题分别路由到正确的成员，supervisor 转述带全上下文

## 5. 常见坑

这一课的坑集中在两类：图结构错误（框架会当场拦下）和团队路由失灵（要回查 supervisor 的定义）。逐条对号入座：

**图结构报错（如"同一节点不能同时有普通边与条件边"）。** `build()` 会做拓扑校验（入口存在/边引用的节点存在/无孤立节点），报错信息会指明问题。最常见的就是给 entry 又加了普通边又加了 when——条件边自带两条出路，不需要再连。

**step 函数里用 `run_until_complete`。** step 可以直接是 async 函数（`async def` + `await`），不要在同步函数里套 `run_until_complete`——图跑在事件循环里，嵌套跑另一个循环必炸。

**团队 Run 报「子 Run 未成功: xxx → failed」。** 先查 supervisor 的指令是否真的强制了 JSON 输出格式——框架提示词虽已内置 JSON 要求，YAML 里再写一段是双重强化，模型跑偏概率更低（省掉也能跑，只是解析失败的兜底会多触发）；解析失败会降级为直接答复，但子 Run 失败通常是别的问题——去 Traces 页看那个子 Run 的现场。

**路由总是给同一个成员。** supervisor 的判断规则段写糊了，或成员 description 没有区分度。修判断规则，别修框架。

**改了团队/图没生效。** 图和团队是 Python 模块，重启服务（discovery）；Agent YAML 则照旧 init。

两类"生效时机"容易混，记一句话：**YAML 走 init（定义入库版本化），Python 走重启（模块重新加载）**——把这条记住，这一课一半的"怎么没生效"都能自诊。

## 6. 练习

**练习 A（模仿）**：给图加一个分支——逾期天数 ≤ 3 天的"宽限"路径（不收滞纳金，只提醒）。这需要动 `when` 的条件函数和处罚/通知逻辑，试试保持图结构不动、只改函数内容能不能做到。（能——这正是图结构的稳定性价值：规则微调不 reshape 流程。）

**练习 B（变式）**：`resume(flow, thread_id, False)` 后流程走成什么样？在 state 里加个字段让"拒绝确认"的路径可观察（比如 notify 的文案区分"已撤销冻结"）。想清楚业务语义：人工拒绝了冻结，流程应该怎么收尾？

这道练习是本课最有含金量的一题，因为它逼你直面一个真实系统设计问题：**拒绝不是取消**。人工说"不冻结"，流程还是要给读者一个交代（欠多少钱、什么时候还）；你补的那根线，本质是让"人的意志"真正参与 state 的流向——补完之后，这张图才第一次实现了"人工节点是闸"而不是"人工节点是走过场"。

**练习 C（综合，本课答辩题）**：同一句需求——"学生申请把逾期的书续借"——分别考虑三种实现：单 Agent（人设+工具）、图（步骤编排）、团队（分诊+专家）。写下你的三选一和 100 字理由。判断要点回看 §2 的起步框架，但要用你自己的话。写完对照第 0 课的表格——你现在的判断框架比第 0 课粗看一眼时精细了多少？

## 7. 思考题

1. 如果人工确认要等三天，这三天里你的服务重启了五次——interrupt 方案下，第三次重启后这个流程还能 resume 吗？靠的是什么？（提示：暂停时刻有什么被写进了数据库，`resume` 的第二个参数又是拿什么找的。）
2. 团队里 my-lib-assistant 的挂失工具是 `approval_required` 的——读者在团队入口说"帮我挂失"，这笔审批请求还会被第 4 课那道闸拦住吗？（提示：成员 Agent 还是那个 Agent，治理是跟着定义走的还是跟着入口走的。）
3. 为什么 `max_delegations=4` 是"上限"而不是"精确次数"？设想两个成员互相委派的死循环，数一数这个护栏在哪一刻把循环掐断。（提示：委派是结构化事件，次数在数据上数得出来。）

## 8. 小结与下一课预告

你集齐了全部三种执行形态：单 Agent（第 2-5 课）、图编排（确定性流程 + 人工节点）、团队（supervisor 分诊 + 专家成员）——并且知道它们不是"高级程度"的递进关系，而是**适用场景**的平行选择。你也亲历了 interrupt/resume 的原始形态和用户领地自定义路由的接线方法。

回收开头的三个问题：

1. **人工确认是落盘的暂停，不是轮询**——state 快照挂在 thread_id 上，进程死了都续得上；下游在结构上不可达，不是"约定不执行"；
2. **进了图、进了团队，治理还是同一套**——子 Run 进同一本 Trace、预算和取消沿调用链传播，因为被编排的和单独跑的是同一批 Agent；
3. **supervisor 吐 JSON 是因为路由是数据**——可解析才能确定性分发，委派次数才数得清、拦得住；自然语言路由等于把分发权交还给概率。

最后一课：把这套东西带出你的笔记本。Docker 五服务全栈部署、Langfuse 观测接线、以及这个基座最独特的承诺的实操验证——**跟着上游升级而不丢你的改动**。毕业设计清单也在终点等着你（它单独成了第 9 课）。

**延伸阅读**

- 用户手册：[图编排](../manual/features/graphs.md)（含流程作 Tool 的 flow_as_tool）、[多 Agent 协作](../manual/features/multiagent.md)（编排 Run 与单 Agent Run 同治理的设计）
- 参考产出：[reference/07-library-flow-and-team.py](https://github.com/lozsh/LuBan/blob/master/website/tutorial/reference/07-library-flow-and-team.py)、[reference/07-triage-and-rules.yaml](https://github.com/lozsh/LuBan/blob/master/website/tutorial/reference/07-triage-and-rules.yaml)
- 对照范本：`app/graphs/refund_flow.py`、`app/multiagents/cs_team.py`、`app/agents/cs_triage.yaml`
