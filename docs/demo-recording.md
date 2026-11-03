# 演示 GIF 录制指引（demo-60s.gif）

> 目标：为 README 首屏「60 秒看真东西」录制一段 30 秒演示 GIF。GIF 本体需人工录制；
> 本文档定死录什么、怎么录、怎么压缩、放在哪。录制完成后取消 README 中的注释占位即可。

## 录什么（30 秒分镜）

| 时间 | 画面 | 要点 |
| --- | --- | --- |
| 0-5s | 终端：`docker compose up -d --build` | 展示"一条命令起全栈"（可快进到启动完成） |
| 5-15s | 浏览器 `http://localhost:8000/chat` | 发送"我买的鞋子想退货"，看分诊 + 退款流程回复 |
| 15-25s | 工作台 Traces → 点开刚才的 Run | 鼠标划过：Agent 版本 → 模型 → 工具调用 → Token/费用 |
| 25-30s | 工作台 Agents 页 → 改一句人设保存 → 回 `/chat` 再说一句 | 强调"改定义即时生效，不发版" |

## 工具选择

- **主体用屏幕录制，不用 asciinema**：asciinema 只能录终端，而本片的 wow 时刻在 Web 工作台（对话、Trace 费用归因），终端只占前 5 秒。
- macOS 录屏任选其一：
  - Kap（免费开源）：直接输出 GIF；
  - macOS 自带录屏（⌘⇧5）→ 得到 .mov，再用下方 ffmpeg 转 GIF；
  - CleanShot X（付费）。
- 录制区建议 1280×720 或 1440×900，浏览器开无痕窗口（干净书签栏）、系统外观统一。

## 压缩（mov → GIF）

```bash
ffmpeg -i demo.mov -vf "fps=12,scale=1280:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse" docs/assets/demo-60s.gif
```

目标：≤ 30 秒、≤ 8MB（GitHub README 加载体感）。超了就把 fps 降到 10、scale 降到 1024。

## 放哪、怎么接

1. 成品放 `docs/assets/demo-60s.gif`（英文 README 在 `docs/` 内引用同名相对路径 `assets/demo-60s.gif`）；
2. 取消 `README.md` 与 `docs/README.en.md`「60 秒看真东西」节的注释占位 `<img ...>`（alt 文案已写好，勿删）；
3. 提交信息建议：`docs(readme): add 60s demo gif`。

## 注意

- 录制前确认演示栈里接了**真模型**（FakeLLM 固定台词没有观感），或至少跑过一次完整退款流程让 Traces 有真实费用数据；
- 不要让截图/录屏带到 `.env` 中的真实 API Key 或内部地址；
- 截图中的数据用 mock-biz 演示数据，不要出现真实客户信息。
