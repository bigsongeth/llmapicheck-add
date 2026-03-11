# 模型运维技能参考

## 1) 模型可用性矩阵输出说明

`model_matrix.py` 会输出：

- 总体可用数（ok/total）
- Provider/Model 维度的状态表
- 每个 Agent 的 Primary/Fallback 映射表

状态图标：

- ✅ 可用（2xx）
- 🔒 鉴权/风控（401/403）
- 🌐 网络/超时/连接异常
- ❌ 其他 HTTP 错误

## 2) 新 API 添加约定

当用户在群里发送 URL + API Key + (可选) 模型名时：

1. 提取 `baseUrl`、`apiKey`、`models[]`
2. provider id 默认由域名推断（可显式指定）
3. 写入 `openclaw.json -> models.providers.<provider>`
4. 同步到 `agents.defaults.models`，便于 UI 选择
5. 可选：将该 provider 第一个模型设置为 primary，并把旧 primary 推到 fallback

## 3) 模型名模糊匹配（重要）

用户给的模型名不一定 100% 精确。`add_provider.py` 会：

1. 先请求 `GET /v1/models` 拉取远端可用模型列表（OpenAI 兼容）
2. 对用户输入做模糊匹配（标准化 + 相似度 + 包含关系）
3. 自动把近似名映射到真实模型 ID，并把映射结果回传给调用方

示例：

- 用户输入：`codex5.3`
- 远端列表：`gpt-5.3-codex`
- 最终写入：`gpt-5.3-codex`

若要关闭模糊匹配：`--no-fuzzy`。

## 4) ccll 风控经验

已知 `ccll.xyz` 对某些 User-Agent 可能返回 403 `Your request was blocked`。
可在 provider 中设置：

```json
"headers": {
  "User-Agent": "curl/8.5.0"
}
```

## 5) 安全提醒

- 用户在群里贴出 key 后，建议立即轮换（rotate）
- 报告中不回显完整 key，可只展示前后缀
