# openclaw-model-ops (opencode skill)

An opencode skill for OpenClaw model operations:

- Check provider/model availability matrix
- Show agent primary/fallback model mapping
- Add or update provider config from URL + API key (+ optional models)
- Fuzzy resolve user-input model names via `/v1/models`

## Files

- `SKILL.md`: skill definition and workflow
- `scripts/model_ops.py`: unified CLI (`check` and `add`)
- `scripts/model_matrix.py`: model availability probing/reporting
- `scripts/add_provider.py`: provider upsert + fuzzy model mapping
- `references/notes.md`: operational notes

## Quick Start

```bash
python3 scripts/model_ops.py check --timeout 12
python3 scripts/model_ops.py add --from-message "https://example.com sk-xxx gpt-5.3-codex"
```

## License

MIT
