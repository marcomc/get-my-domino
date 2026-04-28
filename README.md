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
UTF-8 text, optional RTF, and lightweight metadata, and can turn the text into
local `.m4a` or `.mp3` audio files with the macOS `say` voice engine.

## Features

- Packaged command-line app exposed as `get-my-domino`
- `python -m get_my_domino` entry point
- Issue and article discovery from configurable link patterns
- Magazine export grouped by issue month, issue title, section, and article
  date
- `La settimana di Domino` export grouped in one dated article collection
- Clean article export to same-name `.html` and `.txt` files, optional `.rtf`,
  and lightweight `metadata.json`; readable text omits source URLs so audio
  does not read them aloud
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
audio_timeout = 900
audio_chunked = true
audio_chunk_chars = 2500
audio_chunk_concurrency = 3
audio_chunk_retries = 2
audio_stall_timeout = 45
speech_normalize_auto = false
speech_normalize_agent = "codex"
speech_normalize_command = "codex"
speech_normalize_model = ""
speech_normalize_timeout = 900
speech_normalize_force = false
speech_normalize_fallback = false
export_formats = ["html", "txt"]
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

Command intent:

| Command | Scope | Intended use |
| --- | --- | --- |
| `catalog` | Lists issues, issue contents, and feed entries | Human browsing before choosing what to download |
| `download` | Downloads known targets by URL, one issue article, or one whole issue | Manual, targeted downloads and repairs |
| `sync-magazine` | Scans every available magazine issue and downloads only missing articles | Periodic archive updates and automation |
| `sync-feed` | Scans the recurring weekly feed and downloads only missing articles | Periodic weekly-feed updates and automation |

`download` is intentionally narrow: it does not scan the whole archive unless
you select one issue with `--issue` and `--all`. `sync-magazine` is the
database/archive maintenance command: it walks all available magazine issues,
uses the local manifest to skip articles already present, and adds only new
articles. `sync-feed` does the same for `La settimana di Domino`.

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

Download every article from one magazine issue:

```bash
get-my-domino download --issue 2026-04 --all
```

Explicit downloads reuse existing article folders from the manifest. If only
audio is missing, the CLI generates audio from the existing UTF-8
`.txt` export. If audio already exists, it is reused and not regenerated. If
any configured export file is missing, it refetches the article and fills the
missing export set.

To regenerate one audio file, delete only that `.m4a` or `.mp3` file and rerun
the same `download` command. This is the narrowest repair path when the audio
is corrupt or when you changed the configured system voice and want to rebuild
only one article. Use `--force` when you want to refetch, rewrite exports, and
regenerate audio for every selected article:

```bash
get-my-domino download --issue 2026-04 --article 1 --force
```

Long operations show friendly status messages. In an interactive terminal, the
current step uses an animated indeterminate progress bar and then turns into a
check mark when complete; in logs or redirected output, the CLI prints plain
start and done lines. Download results are summarized as one compact row per
article with export status, audio status, and total elapsed time:

```text
article                                                    export     audio      time
✓ Cosa fare a Teheran quando sei morto                     reused     reused     00:00
✓ E la Casa Bianca restò sola                              written    generated  01:05
```

When terminal colors are available, status labels use restrained color for
quick scanning. Use `--verbose` to also print the article export directory
after each row. The article summary includes one total elapsed time.
macOS `say` does not expose true synthesis percentage, so audio generation
does not show a percentage or ETA. By default, long article text is split into
small paragraph-aware chunks. The CLI synthesizes up to
`audio_chunk_concurrency = 3` chunk AIFF files at a time, retries failed chunks
with `audio_chunk_retries`, joins the AIFF chunks into one temporary AIFF, and
then performs one final `afconvert` or `ffmpeg` conversion. This avoids long
single `say` runs that can silently truncate Siri/neural output.

`audio_timeout` stops a stuck `say`, `afconvert`, or `ffmpeg` command after
the configured number of seconds. `audio_stall_timeout` retries a chunk when
the temporary AIFF file stops growing. These values can be overridden per run:

```bash
get-my-domino download --issue 2026-04 --article 1 --audio-timeout 1200
get-my-domino download --issue 2026-04 --article 1 --audio-jobs 4
get-my-domino download --issue 2026-04 --article 1 --no-audio-chunks
```

Press `Ctrl-C` to stop a run cleanly; the CLI stops active speech synthesis,
removes the temporary `.aiff` file, and prints `interrupted` without a Python
traceback. The chunked synthesis step uses a user-level lock across concurrent
`get-my-domino` processes so two separate CLI runs do not overlap large Siri
speech jobs. If one article audio conversion fails during a multi-article
download or sync, remaining articles continue; the command prints an audio
failure summary at the end and exits non-zero.

Set `export_formats` in the config, or repeat `--format` on `download`,
`sync-magazine`, or `sync-feed`, to choose article export formats. The default
is `["html", "txt"]`; `rtf` is available but not generated unless requested.

```bash
get-my-domino download --issue 2026-04 --article 1 --format txt --format rtf
```

Scan all issues, skip articles already in the manifest, and download new text:

```bash
get-my-domino sync-magazine
```

Magazine articles are saved under:

```text
output_dir/
└── 2026-04-guaio-persiano/
    ├── 01-l-editoriale/
    │   └── 01-cosa-fare-a-teheran-quando-sei-morto/
    │       ├── 01-cosa-fare-a-teheran-quando-sei-morto.html
    │       ├── 01-cosa-fare-a-teheran-quando-sei-morto.txt
    │       └── metadata.json
    └── 02-la-guerra-va-male/
        └── 02-e-la-casa-bianca-rest-sola/
```

Generated magazine audio is saved separately under `output_dir/audio` so audio
players can browse it without article HTML, text, and metadata files:

```text
output_dir/
└── audio/
    └── 2026-04-guaio-persiano/
        ├── 01-cosa-fare-a-teheran-quando-sei-morto.m4a
        └── 02-e-la-casa-bianca-rest-sola.m4a
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

Generated feed audio is saved under `output_dir/audio/feed_folder_name/`.

Create audio from downloaded text files:

```bash
get-my-domino speak
get-my-domino speak --audio-format mp3
get-my-domino sync-magazine --audio --audio-format m4a
get-my-domino download --issue 2026-04 --article 1 --audio-timeout 1200
get-my-domino download --issue 2026-04 --all --audio-jobs 3
```

Optionally create speech-ready text before audio synthesis:

```bash
get-my-domino speech-normalize /path/to/article-dir --diff
get-my-domino download --issue 2026-04 --article 1 --audio --speech-normalize
get-my-domino sync-magazine --audio --speech-normalize
```

Speech normalization is disabled by default because it invokes an external AI
agent and may send article text to the service configured for that agent. The
current implemented backend is `speech_normalize_agent = "codex"`, which calls
the local `codex exec` CLI through `speech_normalize_command`. The config is
structured for future backends (`codex-cloud`, `github-cli`, `github-copilot`,
and `jelly`), but those agents currently fail with a clear “not implemented”
message. When enabled, the CLI writes `<article-basename>.speech.txt` beside
the original `.txt` export and sends that speech-ready file to macOS `say`.
The original `.txt` remains unchanged.

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
