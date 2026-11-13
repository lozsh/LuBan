# 模型中心与模型工厂

> 本页讲模型接入、可编辑的大脑槽位，以及"换模型不改代码"是怎么做到的。

**是什么**：模型接入分两层，职责严格分开——

- **连接信息，存数据库**：每个模型是库里的一条记录——固定标识（如 `deepseek-chat`）、供应商、API 地址、密钥引用、能力声明（Tool Calling / 视觉）、成本参数。注册、修改、上下架都是改库里的数据，运行期生效。
- **连接代码，独立模块**：所有"怎么连这个 API"的代码（客户端构造、参数适配、厂商差异）只放在一个独立模块——模型工厂（`lubanagent/model/`）。新增厂商只在这个模块里加代码，业务代码与其他模块零感知。

Agent 从不直接绑定"某个厂商的某个模型"，而是绑定**逻辑大脑**槽位。初始提供 `fast`（路由/分类）、`smart`（主力推理）、`cheap`（低成本任务）、`vision`（多模态），你也可以新增自己的槽位，例如 `support`。槽位 → 模型标识 → 库里连接记录，全部在运行期解析。

**切换模型 = 改库里的映射指向另一个固定标识，不改代码、不发版。** 把 `smart` 从 `deepseek-chat` 切到 `qwen3-max` 就是改一行映射：Agent 定义不动，新 Run 立即走新模型，历史 Run 的 Trace 里仍如实记录着旧模型。

**为什么需要**：换模型（涨价、降智、出新）是数据操作不是工程排期；适配差异收敛在工厂一个模块里，不散落到业务代码——抽象会破，所以适配点必须窄；模型能力在注册时声明，运行时先校验再执行，不支持则**受控降级**到可用模型，而不是报错崩盘。

**接入方式**：全部走 **OpenAI 兼容协议**——DeepSeek、Qwen、GLM 等国内厂商与 vLLM、Ollama 私有化部署一个客户端全覆盖；厂商原生 SDK 工厂按需补充。

**怎么用**：进入工作台「模型中心」，先注册模型，再为大脑槽位选择映射模型。页面使用服务端当前环境，已有映射会回填。点击「新增槽位」填写标识、名称和用途；点击「编辑」可改这三项，也可调整映射。Agent 的主槽位和降级槽位下拉会跟随当前目录。

标识只能用小写字母开头，后接小写字母、数字、下划线或短横线，总长 1–64。名称用于展示，用途说明可留空。改名会保留旧标识作为兼容别名，所有环境的映射一起移到新标识；旧 Agent 版本与历史 Run 不会被改写，新保存版本使用新标识。新标识不能占用其他槽位的当前标识或历史别名。

也可通过 API 新增槽位并同时绑定模型（请求使用默认服务端环境）：

```bash
curl -X POST http://localhost:8000/v1/brain-profiles \
  -H "Content-Type: application/json" \
  -d '{"key":"support","name":"客服大脑","description":"客服问答","model_key":"deepseek-chat"}'
```

映射关系示意：

```text
模型注册表（数据库）：
key=deepseek-chat  provider=deepseek  base_url=https://api.deepseek.com/v1  api_key_env=DEEPSEEK_API_KEY  capability={tool_calling: true}
key=qwen-vl-max    provider=qwen      base_url=...                          api_key_env=DASHSCOPE_API_KEY  capability={tool_calling: true, vision: true}

逻辑大脑映射（数据库，按环境配置，Agent 选择槽位）：
smart  → deepseek-chat
vision → qwen-vl-max
```

**边界**：删除前会列出使用该槽位的 Agent、版本和字段。只要任一已保存的可运行历史版本仍使用主槽位、降级槽位，或开启摘要记忆隐含使用 `cheap`，后端就会拒绝删除（409），不会动目录和映射；只改当前版本不能解除旧版本引用。没有引用时可删除，包括初始预设，删除会移除所有环境映射，重启不会恢复。删除未使用的 `smart`/`cheap` 后，后续默认 Agent、评测 judge 或摘要功能需要显式重新配置，不会自动补回。

目录、使用列表和映射的查看需要 `config:read`；新增/编辑/删除槽位及保存映射需要 `config:write`。后端授权和删除检查才是最终约束。连接代码仍集中在模型工厂，厂商原生 SDK（非 OpenAI 兼容）需加工厂适配。其余替换说明见[改造指南](../customization/overview.md)。
