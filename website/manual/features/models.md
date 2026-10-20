# 模型中心与模型工厂

> 本页讲模型接入的两层职责分离、逻辑大脑四槽位，以及"换模型不改代码"是怎么做到的。

**是什么**：模型接入分两层，职责严格分开——

- **连接信息，存数据库**：每个模型是库里的一条记录——固定标识（如 `deepseek-chat`）、供应商、API 地址、密钥引用、能力声明（Tool Calling / 视觉）、成本参数。注册、修改、上下架都是改库里的数据，运行期生效。
- **连接代码，独立模块**：所有"怎么连这个 API"的代码（客户端构造、参数适配、厂商差异）只放在一个独立模块——模型工厂（`lubanagent/model/`）。新增厂商只在这个模块里加代码，业务代码与其他模块零感知。

Agent 从不直接绑定"某个厂商的某个模型"，而是绑定**逻辑大脑**槽位——`fast`（快而便宜，路由/分类）、`smart`（主力推理）、`cheap`（批量低值任务）、`vision`（多模态）。槽位 → 固定标识 → 库里连接记录，全部在运行期解析。

**切换模型 = 改库里的映射指向另一个固定标识，不改代码、不发版。** 把 `smart` 从 `deepseek-chat` 切到 `qwen3-max` 就是改一行映射：Agent 定义不动，新 Run 立即走新模型，历史 Run 的 Trace 里仍如实记录着旧模型。

**为什么需要**：换模型（涨价、降智、出新）是数据操作不是工程排期；适配差异收敛在工厂一个模块里，不散落到业务代码——抽象会破，所以适配点必须窄；模型能力在注册时声明，运行时先校验再执行，不支持则**受控降级**到可用模型，而不是报错崩盘。

**接入方式**：全部走 **OpenAI 兼容协议**——DeepSeek、Qwen、GLM 等国内厂商与 vLLM、Ollama 私有化部署一个客户端全覆盖；厂商原生 SDK 工厂按需补充。

**怎么用**（示意）：

```text
模型注册表（数据库）：
key=deepseek-chat  provider=deepseek  base_url=https://api.deepseek.com/v1  api_key_env=DEEPSEEK_API_KEY  capability={tool_calling: true}
key=qwen-vl-max    provider=qwen      base_url=...                          api_key_env=DASHSCOPE_API_KEY  capability={tool_calling: true, vision: true}

逻辑大脑映射（数据库，可按 Agent / 环境覆盖）：
smart  → deepseek-chat
vision → qwen-vl-max
```

**边界**：连接代码只收敛在模型工厂一个模块；厂商原生 SDK（非 OpenAI 兼容）需要在工厂里加一个工厂文件。其余替换说明见[改造指南](../customization/overview.md)。
