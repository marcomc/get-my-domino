# Developer Notes

This scaffold is intentionally opinionated.

## Included Defaults

- `uv` for environment and package management
- `src/` package layout
- `argparse` for a zero-runtime-dependency CLI
- TOML config loading via `tomllib`
- strict-enough static checks for early signal without heavy ceremony

## Intended Workflow

1. Generate a new project from the template.
2. Rename or replace the placeholder `info` subcommand as soon as the real
   domain behavior is clear.
3. Keep `README.md`, `CHANGELOG.md`, and `TODO.md` current as the project
   evolves.
4. Preserve `make install` as the durable user-facing installation path unless
   you have a reason to redesign distribution.
