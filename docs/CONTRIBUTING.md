# Contributing

## Development workflow

```bash
uv sync --group dev
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest
uv build
```

## Guardrails

- Keep changes aligned with the current implementation phase.
- Do not add real user session data to this repository.
- Add migrations and fixture updates together when the SQLite schema changes.
