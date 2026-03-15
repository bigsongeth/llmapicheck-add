# openclaw-model-ops（OpenClaw 模型运维技能 / Model Ops Skill）

一个面向 **OpenClaw** 的模型运维技能，适配 **有浏览器**与**无浏览器（browserless）**环境。

An OpenClaw model-ops skill for both **browser** and **browserless** environments.

核心流程（API-first）：
1. 调 provider 的 `/models` 获取真实模型 id
2. 拉取 `https://models.dev/api.json` 补全静态参数
3. 真实探活（probe）确认可用性

Core API-first flow:
1. Query provider `/models` for exact model ids
2. Enrich metadata from `https://models.dev/api.json`
3. Run a real probe to confirm runtime compatibility

---

## 能做什么 / What it does

- 生成模型可用性矩阵（含 agent 主模型 / fallback）
- 从 URL + API Key 自动写入 provider 配置
- 模糊匹配用户输入模型名（如 `gpt54` → `gpt-5.4`）
- 自动补齐模型 metadata（context / maxTokens / cost / modalities / reasoning）
- 支持 `--probe-after-add`：添加后立刻验活
- 探活结果自动分类（更像产品输出而不是原始错误堆栈）

- Model availability matrix (with agent primary/fallback)
- Add/update provider config from URL + API key
- Fuzzy model name resolution (e.g. `gpt54` → `gpt-5.4`)
- Auto-fill metadata (context / maxTokens / cost / modalities / reasoning)
- `--probe-after-add` to validate immediately
- Friendly failure classification

---

## 快速使用 / Quick Start

```bash
# 探活（全量）
python3 scripts/model_ops.py check --timeout 12

# 从用户消息添加 provider
python3 scripts/model_ops.py add --from-message "https://example.com sk-xxx gpt54 glm5"

# 添加后立刻 probe
python3 scripts/model_ops.py add --from-message "https://example.com sk-xxx gpt54 glm5" --probe-after-add
```

---

## 探活结果分类（Scale / Legend）

| 图标 | 分类 | 含义 | 建议动作 |
|---|---|---|---|
| ✅ | `ok` | 正常可用 | 可作为 primary / fallback |
| 🔒 | `auth_error` | 鉴权失败（401/403） | 检查 API Key / 权限 |
| 🌐 | `network_error` | 网络异常 | 检查网络 / 重试 |
| ⏳ | `rate_limited` | 被限流 | 等待 `retry_after` 或降频 |
| ⚠️ | `advertised_but_unusable` | `/models` 有，但运行时结构不兼容 | 不要作为默认候选 |
| ❌ | `http_error` / `incompatible_runtime_json` | 其他错误 | 视情况降权或移除 |

Legend (English):
- ✅ `ok` — usable; safe for primary/fallback
- 🔒 `auth_error` — check API key/permissions
- 🌐 `network_error` — retry or fix network
- ⏳ `rate_limited` — wait `retry_after` or slow down
- ⚠️ `advertised_but_unusable` — in `/models` but incompatible at runtime
- ❌ `http_error` / `incompatible_runtime_json` — demote or remove

---

## 为什么这套流程重要？ / Why it matters

`/v1/models` 里出现的模型 **不一定真的能用**。
这套技能把问题拆成三层：

- `/models` → 模型 **id 是否真实存在**
- `models.dev/api.json` → **静态参数** 是否准确
- probe → **运行时兼容性** 是否可信

A model listed in `/v1/models` does **not** guarantee it works at runtime. This skill separates:

- `/models` → exact ids
- `models.dev/api.json` → static metadata
- probe → runtime compatibility

---

## 默认配置路径规则 / Default config path

脚本不再写死 `/root/.openclaw/openclaw.json`，而是按以下顺序探测：

1. `OPENCLAW_CONFIG`
2. `~/.openclaw/openclaw.json`
3. `~/.config/openclaw/openclaw.json`
4. `/etc/openclaw/openclaw.json`
5. `/root/.openclaw/openclaw.json`

---

## 文件结构 / Files

- `SKILL.md`：技能说明与流程
- `scripts/model_ops.py`：统一入口（check + add）
- `scripts/model_matrix.py`：探活矩阵逻辑
- `scripts/add_provider.py`：写入 provider + 模糊匹配 + metadata 补全
- `references/notes.md`：运维注意事项

---

## License

MIT
