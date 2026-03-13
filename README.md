# openclaw-model-ops (opencode skill)

An opencode skill for OpenClaw model operations.

It is designed for both normal desktop sessions **and browserless agents**.
The core workflow is API-first:

1. query the provider's `/models` endpoint to get real model ids
2. enrich metadata from `https://models.dev/api.json`
3. run a real probe to confirm the model is actually callable

## What it does

- Check provider/model availability matrix
- Show agent primary/fallback model mapping
- Add or update provider config from URL + API key (+ optional models)
- Fuzzy resolve user-input model names via `/v1/models`
- Auto-fill static metadata (`contextWindow`, `maxTokens`, `cost`, `modalities`, `reasoning`) from `models.dev/api.json`
- Optionally probe the provider immediately after add/update
- Classify probe failures into more readable buckets such as:
  - `auth_error`
  - `network_error`
  - `rate_limited`
  - `incompatible_runtime_json`
  - `advertised_but_unusable`

## Files

- `SKILL.md`: skill definition and workflow
- `scripts/model_ops.py`: unified CLI (`check` and `add`)
- `scripts/model_matrix.py`: model availability probing/reporting
- `scripts/add_provider.py`: provider upsert + fuzzy model mapping + models.dev enrichment
- `references/notes.md`: operational notes

## Quick Start

```bash
python3 scripts/model_ops.py check --timeout 12
python3 scripts/model_ops.py add --from-message "https://example.com sk-xxx gpt54 glm5"
python3 scripts/model_ops.py add --from-message "https://example.com sk-xxx gpt54 glm5" --probe-after-add
```

## Why this is useful

A model appearing in `/v1/models` does **not** guarantee it is actually usable.
This skill separates the job into three layers:

- `/models` → exact ids
- `models.dev/api.json` → static metadata
- `check` / probe → actual runtime compatibility

That makes it safer for OpenAI-compatible proxy gateways and other multi-model routers.

## Default config path behavior

The scripts no longer assume `/root/.openclaw/openclaw.json`.
They detect config in this order:

1. `OPENCLAW_CONFIG`
2. `~/.openclaw/openclaw.json`
3. `~/.config/openclaw/openclaw.json`
4. `/etc/openclaw/openclaw.json`
5. `/root/.openclaw/openclaw.json`

## License

MIT
