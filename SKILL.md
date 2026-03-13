---
name: openclaw-model-ops
description: 检查和运维 OpenClaw 模型配置（可用性矩阵、agent 主备模型映射、批量探活、新 provider 写入 openclaw.json）。当用户要求“测模型可用性/看每个 agent 首选和 fallback/把群里发的 URL+API Key 添加为新 API（模型列表可有可无）/没有浏览器时自动查模型参数并补 metadata”时使用。
license: MIT
compatibility: opencode
---

# OpenClaw Model Ops

统一处理三类需求：
1) 产出模型可用性可视化表 + agent 主备模型映射
2) 根据用户发来的 URL/API Key/模型名，写入或更新 provider 配置
3) 在**没有浏览器**的环境里，自动通过 API 获取模型列表与静态参数（context / price / modalities）

## 核心原则（browserless 通用流程）

不要依赖网页抄参数。通用顺序固定为：

1. **先查 provider 自己的 `/models`**
   - 目标：拿到真实 model id
   - 这是“名字到底对不对”的唯一可靠来源
2. **再查静态模型注册表**
   - 优先用：`https://models.dev/api.json`
   - 目标：补 `contextWindow` / `maxTokens` / `cost` / `modalities` / `reasoning`
3. **最后做真实 probe**
   - `/models` 里有，不代表真能调用成功
   - 需要 `check`/probe 去区分：可用 / 鉴权问题 / JSON 结构不兼容 / provider 虚挂模型

一句话：
- `/models` 解决“模型名”
- `models.dev/api.json` 解决“模型静态参数”
- `check` 解决“模型是不是真能用”

## 快速使用

- 一体化命令行（推荐）：

```bash
python3 ~/.config/opencode/skills/openclaw-model-ops/scripts/model_ops.py check --timeout 12
python3 ~/.config/opencode/skills/openclaw-model-ops/scripts/model_ops.py check --providers ccll,kegui
```

- 生成模型可用性矩阵（底层脚本）：

```bash
python3 ~/.config/opencode/skills/openclaw-model-ops/scripts/model_matrix.py \
  --config ~/.openclaw/openclaw.json \
  --output /tmp/model-matrix.md \
  --json /tmp/model-matrix.json
```

- 添加/更新 provider（一体化命令行）：

```bash
python3 ~/.config/opencode/skills/openclaw-model-ops/scripts/model_ops.py add \
  --provider ccll \
  --base-url https://ccll.xyz/v1 \
  --api-key sk-xxxx \
  --models claude-opus-4-6 \
  --api-mode openai-completions
```

- 从用户消息里自动提取 URL + key + 模型名：

```bash
python3 ~/.config/opencode/skills/openclaw-model-ops/scripts/model_ops.py add \
  --from-message "https://ccll.xyz sk-xxx claude-opus-4-6"
```

## 默认配置路径（重要）

不要写死 `/root/.openclaw/openclaw.json`。

脚本现在应当按以下顺序自动探测：
1. `OPENCLAW_CONFIG`
2. `~/.openclaw/openclaw.json`
3. `~/.config/openclaw/openclaw.json`
4. `/etc/openclaw/openclaw.json`
5. `/root/.openclaw/openclaw.json`

如果用户明确给了 `--config`，总是以用户输入为准。

## 工作流

### A. 模型可用性检查

1. 运行 `model_matrix.py` 探测所有 provider/model。
2. 将输出表格直接回给用户（Markdown 表格）。
3. 同步输出“Agent 首选 / fallback 模型”表。

说明：
- ✅=2xx + 返回结构符合预期
- 🔒=401/403
- 🌐=网络异常
- ❌=其他错误（包括 200 但 JSON 结构不兼容）
- `openai-completions` 使用 `/v1/chat/completions` 探活。
- `anthropic-messages` 使用 `/v1/messages` 探活。

### B. 新 API 写入（带 metadata 自动补全）

当用户发 URL + key（模型列表可选）时：

1. 提取参数（缺模型也允许先落 provider）。
2. 请求目标网关 `GET /v1/models` 获取真实模型列表。
3. 对用户输入模型名做模糊匹配，映射到真实 model id。
4. 再请求 `https://models.dev/api.json` 拉静态 metadata。
5. 把 metadata 写入 `openclaw.json.models.providers.<provider>.models[]`。
6. 同步 `agents.defaults.models`（便于选择）。
7. 如用户要求，设置为 primary，并将旧 primary 推入 fallback。
8. 可选执行一次 `check --providers <provider>` 做即刻验收。

### C. 模型名模糊匹配（必做）

用户给的模型名不保证 100% 精确（例如 `codex5.3`）。
在 `openai-completions` 模式下，`add_provider.py` 默认会：

1. 请求目标网关 `GET /v1/models` 获取可用模型列表。
2. 对用户输入模型名做模糊解析（标准化/相似度/包含关系）。
3. 自动映射到真实模型 ID 后再写入配置。
4. 在脚本输出中返回 `fuzzyMapping`，用于向用户确认“你输入A，我已映射为B”。

仅当用户明确要求时，才使用 `--no-fuzzy` 关闭该能力。

### D. 静态参数补全（无浏览器场景重点）

`add_provider.py` 默认会请求：
- `https://models.dev/api.json`

并为模型补以下字段（如果匹配成功）：
- `name`
- `reasoning`
- `input`
- `cost`
- `contextWindow`
- `maxTokens`

如果 models.dev 没找到匹配：
- 允许继续写 provider
- 但保留保守默认值
- 并在输出里告知 `modelsDevNote`

## Telegram / Discord 命令触发

已注册 Telegram customCommands，用户在群里发送即触发：

- `/apicheck` → 执行全量模型可用性矩阵（等同 `model_ops.py check`），将结果表格回复到群里
- `/apiadd <url> <key> [模型名]` → 解析参数并添加 provider（等同 `model_ops.py add --from-message "..."`），完成后回复写入结果 + 即刻验活

处理逻辑：
1. 收到 `/apicheck` 时，运行 `model_ops.py check --timeout 12`，将 stdout 输出直接作为回复发送
2. 收到 `/apiadd` 时，将命令后面的文本作为 `--from-message` 参数传入 `model_ops.py add`，然后对新加的 provider 跑一次 `check --providers <id>` 验活

## 约定与注意

- 尽量不在回复中展示完整 key（可打码）。
- 若用户在群里公开 key，提醒其尽快轮换。
- 已知某些服务商可能按 User-Agent 风控，必要时在 provider 里加 `headers.User-Agent`。
- “出现在 `/models` 中” 不等于 “completion 真可用”；必须 probe。
- 某些 provider 可能返回 HTTP 200 但 body 结构不兼容，这应标为失败，而不是成功。

参考：`references/notes.md`
