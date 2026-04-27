# get-my-domino

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Project Layout](#project-layout)
- [Development](#development)
- [Release Notes](#release-notes)
- [License](#license)

## Overview

`get-my-domino` downloads articles from `rivistadomino.it` for offline reading
and audio generation. It discovers magazine issues and recurring article feeds,
lists articles with their section grouping, exports each article as clean HTML,
UTF-8 text, RTF, and metadata, and can turn the text into local `.m4a` or
`.mp3` audio files with the macOS `say` voice engine.

## Features

- Packaged command-line app exposed as `get-my-domino`
- `python -m get_my_domino` entry point
- Issue and article discovery from configurable link patterns
- Magazine export grouped by issue month, issue title, section, and article
  date
- `La settimana di Domino` export grouped in one dated article collection
- Clean article export to `article.html`, UTF-8 `article.txt`, `article.rtf`,
  and `metadata.json`; readable text omits source URLs so audio does not read
  them aloud
- Incremental `sync-magazine` and `sync-feed` commands with local manifests
- Optional `.m4a` or `.mp3` synthesis through macOS `say` and `afconvert`
- Default config file at `~/.config/get-my-domino/config.toml`
- Saved authenticated sessions at `~/.config/get-my-domino/session.json`
- Separate sync flow for `La settimana di Domino`
- `uv`-driven sync, lint, test, and install flows
- `ruff`, `mypy`, `pytest`, `markdownlint`, and `shellcheck` wired in

## Requirements

For users:

- Python `3.11`
- `uv`
- `make`
- Playwright only when using browser-assisted login
- macOS `say` and `afconvert` only when generating audio
- `ffmpeg` only when generating `.mp3` audio

For maintainers:

- `markdownlint`
- `shellcheck`

## Installation

Clone the repository and install the standalone runtime:

```bash
git clone <repo-url>
cd get-my-domino
make install
```

`make install`:

- creates a standalone virtual environment in
  `~/.local/share/get-my-domino/venv`
- installs the packaged CLI into that standalone runtime
- links the command to `~/.local/bin/get-my-domino`
- installs a config template to `~/.config/get-my-domino/config.toml` if it
  does not exist yet

If `~/.local/bin` is not on your `PATH`, `make check-deps` prints the shell
snippet to add it.

### Editable Development Install

```bash
make install-dev
```

This points `~/.local/bin/get-my-domino` at the project-local `.venv` so source
edits are reflected immediately.

### Browser Login Support

Browser-assisted login needs the optional browser dependency:

```bash
uv sync --extra browser
```

The command first tries system Chrome. If no supported browser is available,
install Playwright Chromium:

```bash
uv run playwright install chromium
```

## Configuration

The CLI reads optional config from:

- `~/.config/get-my-domino/config.toml`
- or the file passed with `--config`

Start from the example file in this repository:

- [config.toml.example](config.toml.example)
- [config.schema.json](config.schema.json)

Example:

```toml
app_name = "get-my-domino"
default_output = "text"
verbose = false
base_url = "https://www.rivistadomino.it/"
magazine_index_url = "https://www.rivistadomino.it/mio-account/my_domino/"
output_dir = "~/Documents/rivistadomino"
feed_index_url = "https://www.rivistadomino.it/blog/category/la-settimana-di-domino/"
feed_folder_name = "la-settimana-di-domino"
audio_auto = false
audio_format = "m4a"
siri_voice = ""
auth_login_url = "https://www.rivistadomino.it/mio-account/"
auth_username = ""
auth_password = ""
auth_username_field = "username"
auth_password_field = "password"
auth_submit_field = "login"
auth_submit_value = "Accedi"
auth_session_path = "~/.config/get-my-domino/session.json"
auth_browser_timeout = 300
issue_link_patterns = ["?sfoglia=1"]
article_link_patterns = ["/blog/20"]
feed_article_link_patterns = ["/blog/20"]
content_selectors = ["article", "main", ".entry-content"]
```

Authentication can use either a saved session or TOML credentials. Run
`get-my-domino login --browser` to open a browser, authenticate manually, and
save the resulting WordPress/WooCommerce cookies to `auth_session_path`.
Subsequent commands reuse that session without requiring `auth_username` or
`auth_password`. If the saved session expires, run browser login again or fill
the TOML credentials and run `get-my-domino login`.

Domino currently exposes WordPress session cookies, not a separate refresh
token like the Degoo flow used by `cligoo`. The CLI therefore persists cookies
and revalidates them against `mio-account`.

Issue discovery starts from the subscriber `my_domino` page and keeps
`?sfoglia=1` links so article discovery can reach the private issue contents.

## Usage

Print the focused top-level help:

```bash
get-my-domino
```

List available magazine issues:

```bash
get-my-domino issues
```

List the articles under one issue:

```bash
get-my-domino articles "https://www.rivistadomino.it/prodotto/guaio-persiano/?sfoglia=1"
```

The article listing preserves the section headings from the issue page, such as
`L'Editoriale`, `La guerra va male`, `L'Iran resiste`, and
`La guerra altrove`.

List recurring `La settimana di Domino` articles:

```bash
get-my-domino feed
get-my-domino feed --pages 3
```

Browse the magazine catalog with cleaner, grouped output:

```bash
get-my-domino catalog
get-my-domino catalog --issue 2026-04
get-my-domino catalog --issue "https://www.rivistadomino.it/prodotto/guaio-persiano/?sfoglia=1"
get-my-domino catalog --all
get-my-domino catalog --all --feed --pages 3
```

`catalog` lists all available magazine issues by `YYYY-MM` month code. `--issue`
expands one issue by month code or URL, preserving its sections and article
order. `--all` expands every issue. `--feed` appends the recurring
`La settimana di Domino` entries. Catalog issue lists are sorted by `YYYY-MM`
month and omit storefront price text.

Use `catalog` for human browsing. The older `issues`, `articles`, and `feed`
commands remain as raw URL list commands for scripts and JSON output.

Download specific articles:

```bash
get-my-domino download \
  "https://rivistadomino.it/articolo/example/"
```

You can also download one magazine article by issue month and article order
from `catalog --issue`:

```bash
get-my-domino download --issue 2026-04 --article 1
```

Explicit downloads reuse existing article folders from the manifest. If only
audio is missing, the CLI generates audio from the existing UTF-8
`article.txt`. If any export file is missing, it refetches the article and
fills the missing export set. Use `--force` to refetch and rewrite the export
even when all files already exist:

```bash
get-my-domino download --issue 2026-04 --article 1 --force
```

Long download operations print flushed `progress:` messages to stderr for
network fetches, retries, export writes, and audio generation.

Scan all issues, skip articles already in the manifest, and download new text:

```bash
get-my-domino sync-magazine
```

Magazine articles are saved under:

```text
output_dir/
└── 2026-04-guaio-persiano/
    ├── 01-l-editoriale/
    │   └── 01-2026-04-21-cosa-fare-a-teheran-quando-sei-morto/
    └── 02-la-guerra-va-male/
        └── 02-2026-04-21-e-la-casa-bianca-rest-sola/
```

Scan `La settimana di Domino` and save it under `output_dir/feed_folder_name`:

```bash
get-my-domino sync-feed
get-my-domino sync-feed --audio --pages 3
```

Feed articles are saved with date-first names, for example:

```text
output_dir/la-settimana-di-domino/
└── 2026-04-24-usa-e-globalizzazione-guerra-in-medio-oriente/
```

Create audio from downloaded text files:

```bash
get-my-domino speak
get-my-domino speak --audio-format mp3
get-my-domino sync-magazine --audio --audio-format m4a
```

List the exact voice names that macOS `say` accepts:

```bash
get-my-domino voices
get-my-domino voices --all
```

Set `audio_auto = true` in the config to generate audio automatically whenever
`download`, `sync-magazine`, or `sync-feed` saves new articles. Use
`--no-audio` for one run without synthesis. `audio_format = "mp4a"` is accepted
as an alias for `m4a`.

Leave `siri_voice` empty to use the current macOS system voice. This is the
only supported way to use Siri/neural voices from this CLI: the app calls `say`
without `-v`, matching plain `say "ciao"` behavior, and macOS delegates speech
to the configured system voice.

On macOS, open System Settings > Accessibility > Spoken Content, set Language
to `Italian (Italy)` or your preferred language, choose System Voice, click the
information button next to the voice selector, search for the Siri voice you
want, select it, and close Settings.

Set `siri_voice` only when you want to force an exact voice name accepted by
`say -v '?'`. Those are the older `NSSpeechSynthesizer` voices, such as
`Alice`; Siri/neural voices are not reliably selectable through `-v`. Article
text used for synthesis starts with the issue title when known, then the
article title, then `di ...` when an author is detected; the source URL is kept
only in `metadata.json`. If `siri_voice` names a voice that `say` would
silently ignore, the CLI now stops with an explicit error instead of creating
audio with a fallback voice.

Legacy aliases remain available: `sync` for `sync-magazine`, `weekly` for
`feed`, and `sync-weekly` for `sync-feed`.

Inspect the resolved configuration:

```bash
get-my-domino info
get-my-domino --config ./config.toml info --json
```

## Project Layout

```text
.
├── AGENTS.md
├── CHANGELOG.md
├── Makefile
├── README.md
├── TODO.md
├── config.toml.example
├── pyproject.toml
├── scripts/
│   └── install.sh
├── src/
│   └── get_my_domino/
│       ├── __init__.py
│       ├── __main__.py
│       ├── audio.py
│       ├── browser_auth.py
│       ├── cli.py
│       ├── config.py
│       ├── extract.py
│       ├── models.py
│       ├── session_store.py
│       ├── storage.py
│       └── web.py
└── tests/
    └── test_cli.py
```

## Development

Sync the environment and run the default quality gate:

```bash
make check
```

Common commands:

```bash
make sync
make test
make lint
make run
```

## Release Notes

Before tagging a release:

1. update the version in `pyproject.toml`
2. update `src/get_my_domino/__init__.py`
3. add release notes to `CHANGELOG.md`
4. verify `make check`

## License

This project is released under the MIT License. See [LICENSE](LICENSE).
