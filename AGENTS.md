# Project Agent Notes

## Project Identity

- Project name: `get-my-domino`
- Python package: `get_my_domino`
- Installed CLI: `get-my-domino`
- Module entry point: `python -m get_my_domino`
- Default user config path: `~/.config/get-my-domino/config.toml`
- Default standalone runtime path: `~/.local/share/get-my-domino/venv`
- Default user-facing binary path: `~/.local/bin/get-my-domino`

## New Chat Bootstrap

At the start of every new AI agent chat for this repository, read:

1. `README.md`
2. `Makefile`
3. `pyproject.toml`
4. `CHANGELOG.md`
5. `TODO.md`

## Development Rules

- Keep the project installable as a packaged Python CLI.
- Keep importable application code under `src/get_my_domino/`.
- Keep tests under `tests/`.
- Prefer focused modules instead of one large `cli.py`.
- Keep `python -m get_my_domino` working.
- Preserve the standalone install behavior of `make install`.

## Quality Gates

Use `make check` as the default maintainer validation command.

Expected checks:

- `uv run pytest -q`
- `uv run ruff check src tests`
- `uv run ruff format --check src tests`
- `uv run mypy src tests`
- `markdownlint --config .markdownlint.json README.md CHANGELOG.md TODO.md AGENTS.md docs/*.md`
- `shellcheck --enable=all scripts/*.sh`

## Documentation Rules

- Keep `README.md` accurate for end users.
- Keep `CHANGELOG.md` updated in `Unreleased` for user-visible changes.
- Remove completed items from `TODO.md` when they ship.
- Update config documentation when adding or changing config keys.

## Release Hygiene

When cutting a release, update the version consistently in:

- `pyproject.toml`
- `src/get_my_domino/__init__.py`
- `CHANGELOG.md`
- tests that assert the version string
