# Prompt 库：指令双轨

> 本页讲 Agent 系统提示词的两种写法——内嵌与引用 Prompt 库，以及引用的三条容易踩的语义。Prompt 库页面操作见[开发者工作台](./workbench.md)。

Agent 的指令（系统 Prompt）有两种写法，可以随时切换：

- **内嵌**（默认）：直接把 Prompt 写在定义的 `instructions` 字段里。YAML 一次性导入的 Agent 就是这种；存量 Agent 与 YAML 导入的行为完全不变。
- **引用 Prompt 库**：在定义里填 `prompt_key`（可选再钉 `prompt_version`），指向 Prompt 库里的一个 Prompt。同一个 Prompt 可以被多个 Agent 复用，改一处草稿、发一个新版本，比每个 Agent 各存一份好维护。

引用在**保存时解析**：系统取该 Prompt 的当前版（或你钉住的版本），把全文写进这份 Agent 定义的 `instructions`，变量合并进来（Agent 自己定义的同名变量优先）。所以 Agent 版本快照里存的是**解析后的全文**——运行和回放都只读快照，不会再回查 Prompt 库。

三条容易踩的语义：

1. **Prompt 新版不自动生效**：Prompt 库发了 v3，引用它的 Agent 不会悄悄换内容——Agent 要吃新版，必须显式保存一个新版本（发版时把 `prompt_version` 清空即取当前版）。
2. **整份定义回传会钉住旧版**：编辑 Agent 其他字段（比如换个大脑）时，表单带着旧的 `prompt_version` 回传，新版本会沿用旧 Prompt 内容——不会因为改个无关字段就悄悄换了 Prompt。
3. **想退出引用**：在 Agent 编辑器里把指令"转为内嵌"即可，内容原样落到 `instructions`，之后与 Prompt 库再无关联。

Prompt 库的草拟、发版、版本历史、分配给 Agent，都在工作台的「Prompt 库」页面（见[开发者工作台](./workbench.md)）。
