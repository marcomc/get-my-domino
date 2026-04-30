# get-my-domino

## Table of Contents

- [Overview](#overview)
- [Use and Rights](#use-and-rights)
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

`get-my-domino` is an independent, unofficial CLI for readers who already have
lawful access to content on `rivistadomino.it`. It downloads articles for
offline reading and audio generation, discovers magazine issues and recurring
article feeds, lists articles with their section grouping, exports each article
as clean HTML, UTF-8 text, optional RTF, and lightweight metadata, and can
turn the text into local `.m4a` or `.mp3` audio files with the macOS `say`
voice engine.

## Use and Rights

This project is intended only for people who already have an active, paid, and
lawful right to access the relevant Rivista Domino digital content.

The generated text exports, per-article audio files, and issue-level
audiobooks are intended for strictly private, personal use, including
accessibility and reading-support scenarios such as visual impairment or other
reading difficulties. They are not intended for redistribution, public
performance, publication, sharing with non-subscribers, or any other use that
could infringe third-party rights.

This project is not designed, marketed, or maintained as a tool for unlawful
copying or publication of copyrighted material. You are responsible for using
it only in ways permitted by applicable law and by the terms of your
subscription.

`get-my-domino` is an independent reader-built tool. It is not affiliated with,
endorsed by, sponsored by, or operated by Rivista Domino, Dario Fabbri,
Enrico Mentana, or Edizioni Gommonica S.r.l., which the Rivista Domino site
identifies in its service terms and subscription pages as the service provider
and publishing company for the digital offering.

## Features

- Packaged command-line app exposed as `get-my-domino`
- `python -m get_my_domino` entry point
- Issue and article discovery from configurable link patterns
- Magazine export grouped by issue code, issue title, section, and article
  date
- `La settimana di Domino` export grouped in one dated article collection
- Clean article export to same-name `.html` and `.txt` files, optional `.rtf`,
  and lightweight `metadata.json`; readable text omits source URLs so audio
  does not read them aloud
- Incremental `sync-magazine` and `sync-feed` commands with local manifests
- Optional `.m4a` or `.mp3` synthesis through macOS `say` and `afconvert`
- Optional full-issue `.m4b` audiobook packaging with chapter markers, embedded
  cover art, and issue metadata when downloading or syncing magazine issues
- Issue sidecars as `issue.json` with title, issue URL, release date, summary,
  cover paths, section/order data, and article-folder mapping
- Repair commands to refresh issue metadata from the live site and repackage
  issue audiobooks without regenerating chapter audio
- Default config file at `~/.config/get-my-domino/config.toml`
- Saved authenticated sessions at `~/.config/get-my-domino/session.json`
- Separate sync flow for `La settimana di Domino`
- `uv`-driven sync, lint, test, and install flows
- `ruff`, `mypy`, `pytest`, `markdownlint`, and `shellcheck` wired in
- Maintainer notes for the current Domino site structure and parser touchpoints
  under `docs/`

## Requirements

For users:

- Python `3.11`
- `uv`
- `make`
- Playwright only when using browser-assisted login
- macOS `say` and `afconvert` only when generating audio
- `ffmpeg` only when generating `.mp3` audio
- `ffmpeg` and `ffprobe` when packaging issue audiobooks as `.m4b`

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
- installs the editable speech-normalization prompt to
  `~/.config/get-my-domino/speech-normalize-codex.txt` if it does not exist yet

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
- [config.full.toml](config.full.toml)
- [config.schema.json](config.schema.json)

`config.toml.example` is the recommended user template. It keeps only the
settings most readers are likely to change.

`config.full.toml` is a complete reference file with every supported key,
including advanced site-override and maintenance knobs. It is not loaded
automatically by the CLI. The runtime still reads one config file at a time,
normally `~/.config/get-my-domino/config.toml`.

Example:

```toml
verbose = false
output_parent_dir = "~/Documents"
collection_dir_name = "domino"
# output_dir = "~/Documents/domino"
# audiobook_output_dir = "~/Audiobooks/Domino"
feed_folder_name = "la-settimana-di-domino"
export_formats = ["html", "txt"]
siri_voice = ""
audio_auto = false
audiobook_auto = false
audio_format = "m4a"
speech_normalize_auto = false
speech_normalize_command = "codex"
speech_normalize_model = ""
speech_normalize_prompt_path = "~/.config/get-my-domino/speech-normalize-codex.txt"
magazine_title = "Domino"
filename_separator = "-"
audiobook_name_format = "{magazine_slug}{sep}{year}{sep}{number}{sep}{title_slug}"
auth_username = ""
auth_password = ""
auth_session_path = "~/.config/get-my-domino/session.json"
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

Most users should never need to touch the advanced site-override keys such as:

- `content_selectors`
- `issue_link_patterns`
- `article_link_patterns`
- `feed_article_link_patterns`
- `auth_submit_field`
- `auth_submit_value`

Those exist mainly as recovery knobs if the site markup or login form changes.

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

`catalog` lists all available magazine issues by `YYYY-NN` issue code. `--issue`
 expands one issue by issue code or URL, preserving its sections and article
order. `--all` expands every issue. `--feed` appends the recurring
`La settimana di Domino` entries. Catalog issue lists are sorted by `YYYY-NN`
issue code and omit storefront price text.

Use `catalog` for human browsing. The older `issues`, `articles`, and `feed`
commands remain as raw URL list commands for scripts and JSON output.

Command intent:

| Command | Scope | Intended use |
| --- | --- | --- |
| `catalog` | Lists issues, issue contents, and feed entries | Human browsing before choosing what to download |
| `download` | Downloads known targets by URL, one issue article, or one whole issue | Manual, targeted downloads and repairs |
| `sync-magazine` | Scans every available magazine issue and downloads only missing articles; with `--audio`, also generates missing audio for already synced articles | Periodic archive updates and automation |
| `sync-feed` | Scans the recurring weekly feed and downloads only missing articles; with `--audio`, also generates missing audio for already synced feed articles | Periodic weekly-feed updates and automation |
| `refresh-issue-metadata` | Re-reads downloaded issue articles from the live site and refreshes local `metadata.json` plus `issue.json` | Metadata repairs after parser improvements or site changes |
| `repackage-audiobook` | Runs issue-metadata refresh, then rebuilds the issue `.m4b` from existing chapter audio | Audiobook tag and cover repairs without re-synthesizing audio |
| `rename-audiobooks` | Renames existing `.m4b` files from embedded tags using the configured filename template | Normalizing an audiobook library after changing naming rules |

`download` is intentionally narrow: it does not scan the whole archive unless
you select one issue with `--issue` and `--all`. `sync-magazine` is the
database/archive maintenance command: it walks all available magazine issues,
uses the local manifest to skip articles already present, and adds only new
articles. `sync-feed` does the same for `La settimana di Domino`.

When you add `--audiobook` to `download --issue YYYY-NN --all` or to
`sync-magazine`, the CLI also packages each complete magazine issue as one
chapterized `.m4b` file under `config.audiobooks_dir`, which defaults to
`output_dir/audiobooks/` unless you set `audiobook_output_dir`. The package
reuses the ordered per-article `.m4a` files as audiobook chapters, embeds the
issue cover image when available, and writes issue-level metadata sidecars as
`<issue-folder>/issue.json`. If you set `audiobook_auto = true` in the config,
the same packaging also happens automatically for `sync-magazine` and for full
issue downloads via `download --issue YYYY-NN --all`.

Verified audiobook metadata written into the `.m4b` container:

- `title`
- `album`
- `artist`
- `album_artist`
- `composer` with the unique issue-level contributor list
- `date`
- `genre`
- `comment` using the issue URL
- `description`
- `synopsis`
- chapter titles from the issue article order, expanded to `Title (di Author)`
  when article author metadata is available
- embedded cover art as an attached picture stream

The bundler is backward-compatible with existing archives that already contain
older issue audio filenames, as long as the chapter files still preserve their
article order prefix such as `01-`, `02-`, and so on.

Issue sidecars also keep contributor metadata per article and as a deduplicated
issue-level contributor list under `contributors`.

When you already have the articles and chapter audio on disk, use the repair
commands instead of `download --force`:

```bash
get-my-domino refresh-issue-metadata --issue 2026-04
get-my-domino repackage-audiobook --issue 2026-04
```

`refresh-issue-metadata` updates the per-article `metadata.json` files and the
issue-level `issue.json` from the live site, but does not regenerate chapter
audio and does not rebuild the `.m4b`. `repackage-audiobook` depends on that
refresh step and then rebuilds the `.m4b` from the existing `.m4a` chapter
files already present inside each article folder.

Audiobook filenames are configurable. By default they are written as:

```text
domino-2026-04-guaio-persiano.m4b
```

The default template is:

```text
{magazine_slug}{sep}{year}{sep}{number}{sep}{title_slug}
```

The naming model is:

- `magazine_title` is the base text you choose, for example `Domino` or
  `Rivista Domino`
- `{magazine}` inserts that text after filename-safe cleanup, preserving case
  and spaces
- `{magazine_slug}` is derived from `magazine_title` by lowercasing it and
  replacing spaces or punctuation with the configured separator

So:

- `magazine_title = "Domino"` with `{magazine}` gives `Domino`
- `magazine_title = "Domino"` with `{magazine_slug}` gives `domino`
- `magazine_title = "Rivista Domino"` with separator `-` and
  `{magazine_slug}` gives `rivista-domino`

You can override that per command:

```bash
get-my-domino repackage-audiobook \
  --issue 2026-04 \
  --magazine-title "Rivista Domino" \
  --filename-separator "." \
  --audiobook-name-format "{magazine_slug}{sep}anno-{year}{sep}numero-{number}{sep}{title_slug}"
```

Preferred config keys:

```toml
magazine_title = "Domino"
filename_separator = "-"
audiobook_name_format = "{magazine}{sep}{year}{sep}{number}{sep}{title_slug}"
```

Older keys such as `audiobook_filename_magazine_title`,
`audiobook_filename_separator`, and `audiobook_filename_format` are still
accepted for backward compatibility, but the names above are now the preferred
ones.

If your `.m4b` library lives elsewhere, set for example:

```toml
audiobook_output_dir = "~/Audiobooks/Domino"
```

That redirects only the packaged audiobook destination. Article exports,
issue metadata, and per-article audio still remain under `output_dir/library`.

Available filename fields:

| Field | Meaning | Example |
| --- | --- | --- |
| `{magazine}` | Magazine title after filename-safe cleanup, preserving case and spaces | `Rivista Domino` |
| `{magazine_slug}` | Lowercase slug derived from `magazine_title`, using the configured separator between words | `rivista-domino` |
| `{sep}` | The configured separator | `-` |
| `{year}` | Issue year from `YYYY-NN` | `2026` |
| `{number}` | Issue number within that year from `YYYY-NN` | `04` |
| `{issue}` | Year and issue number joined with the configured separator | `2026-04` |
| `{issue_compact}` | Year and issue number without a separator | `202604` |
| `{title}` | Issue title after filename-safe cleanup, preserving case and spaces | `Guaio persiano` |
| `{title_slug}` | Lowercase slug form of the issue title | `guaio-persiano` |

Allowed separator characters are intentionally narrow so the result is safe on
macOS and Linux:

- `-`
- `_`
- `.`

Examples:

- `{magazine_slug}{sep}{year}{sep}{number}{sep}{title_slug}` -> `domino-2026-04-guaio-persiano`
- `{magazine}{sep}{year}{sep}{number}{sep}{title_slug}` -> `Domino-2026-04-guaio-persiano`
- `{magazine_slug}{sep}anno-{year}{sep}numero-{number}{sep}{title_slug}` -> `rivista-domino-anno-2026-numero-04-guaio-persiano`
- `{number}{sep}{year}{sep}{magazine_slug}{sep}{title_slug}` with `sep = "."` -> `04.2026.rivista-domino.guaio-persiano`

Use `rename-audiobooks` after changing the template to normalize files already
present in your library. It reads the embedded `.m4b` tags and rebuilds the
filename from those tags, so it does not need the original article folders:

```bash
get-my-domino rename-audiobooks --dry-run
get-my-domino rename-audiobooks --library-dir ~/Audiobooks/Domino
get-my-domino rename-audiobooks ~/Audiobooks/Domino/legacy-file.m4b
```

Download specific articles:

```bash
get-my-domino download \
  "https://rivistadomino.it/articolo/example/"
```

You can also download one magazine article by issue code and article order
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
get-my-domino sync-magazine --audio
get-my-domino sync-magazine --audio --force
```

Magazine articles are saved under:

```text
output_dir/
├── audiobooks/
└── library/
    └── rivista/
        └── 2026-04-guaio-persiano/
            ├── metadata.json
            ├── issue.json
            ├── 01-l-editoriale/
            │   └── 01-cosa-fare-a-teheran-quando-sei-morto/
            │       ├── 01-cosa-fare-a-teheran-quando-sei-morto.html
            │       ├── 01-cosa-fare-a-teheran-quando-sei-morto.txt
            │       ├── 01-cosa-fare-a-teheran-quando-sei-morto.m4a
            │       └── metadata.json
            └── 02-la-guerra-va-male/
                └── 02-e-la-casa-bianca-rest-sola/
```

If `audiobook_output_dir` is configured, replace the top-level `audiobooks/`
entry above with that external path.

Single-article audio now lives beside the article exports and metadata, so the
old top-level `output_dir/audio/` tree is no longer the canonical layout.

Scan `La settimana di Domino` and save it under
`output_dir/library/feed_folder_name`:

```bash
get-my-domino sync-feed
get-my-domino sync-feed --audio --pages 3
get-my-domino sync-feed --audio --force
```

`sync-feed --audio` and `sync-magazine --audio` also inspect articles already
present in the local manifest and generate missing audio from local exports.
Use `--force` on sync commands only when you want to refetch/rewrite existing
article exports and regenerate their audio.

Feed articles are saved with date-first names, for example:

```text
output_dir/
└── library/
    └── la-settimana-di-domino/
        └── 2026-04-24-usa-e-globalizzazione-guerra-in-medio-oriente/
            ├── 2026-04-24-usa-e-globalizzazione-guerra-in-medio-oriente.html
            ├── 2026-04-24-usa-e-globalizzazione-guerra-in-medio-oriente.txt
            ├── 2026-04-24-usa-e-globalizzazione-guerra-in-medio-oriente.m4a
            └── metadata.json
```

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
The original `.txt` remains unchanged. The prompt also allows conservative
Italian punctuation fixes for TTS prosody, for example closing clear incisi or
adding a comma at a real clause boundary, but forbids punctuation changes that
would alter syntax or meaning.

The default prompt is installed as a user-editable file at
`~/.config/get-my-domino/speech-normalize-codex.txt`. Set
`speech_normalize_prompt_path` or pass `--speech-normalize-prompt` to use a
different prompt template. The template must keep the placeholders
`{output_path}`, `{source_text_path}`, and `{normalized_text}`.

Model selection for Codex speech normalization already works at all three
layers:

- config file with `speech_normalize_model = "gpt-5.3-codex-spark"`
- command line with `--speech-normalize-model gpt-5.3-codex-spark`
- direct Codex invocation, because the CLI passes `-m <model>` through to
  `codex exec`

Examples:

```bash
get-my-domino speak /path/to/article-dir \
  --speech-normalize \
  --speech-normalize-model gpt-5.3-codex-spark

get-my-domino sync-magazine \
  --audio \
  --speech-normalize \
  --speech-normalize-model gpt-5.3-codex-spark
```

If you want Spark as your default for speech normalization, set:

```toml
speech_normalize_model = "gpt-5.3-codex-spark"
```

Current OpenAI model availability for this setting, verified against official
OpenAI docs on April 29, 2026:

- Recommended default here, when your account has access:
  `gpt-5.3-codex-spark`
- General Codex model examples documented by OpenAI today:
  `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex`,
  `gpt-5.3-codex-spark`
- General API model IDs documented by OpenAI today:
  `gpt-5.5`, `gpt-5.5-pro`, `gpt-5.4`, `gpt-5.4-pro`, `gpt-5.4-mini`,
  `gpt-5.4-nano`, `gpt-5.3-codex`

OpenAI currently documents the following plan-level availability:

- ChatGPT subscriptions:
  all users have GPT-5.3 by default; GPT-5.5 is rolling out in ChatGPT to
  Plus, Pro, Business, and Enterprise; GPT-5.5 Pro is for Pro, Business, and
  Enterprise; GPT-5.4 Thinking remains the earlier paid-tier reasoning model;
  GPT-5.4 mini is available to Free and Go users through Thinking and as a
  fallback for paid GPT-5.4 Thinking users
- Codex subscriptions:
  GPT-5.5 is available in Codex for Plus, Pro, Business, Enterprise, Edu, and
  Go plans; the Codex rate card also currently lists GPT-5.4, GPT-5.4-Mini,
  GPT-5.3-Codex, and GPT-5.3-Codex-Spark as a research preview
- API:
  the current primary GPT family listed by OpenAI is GPT-5.5, GPT-5.5 Pro,
  GPT-5.4, GPT-5.4 Pro, GPT-5.4 mini, GPT-5.4 nano, plus GPT-5.3-Codex for
  coding workflows

Important availability note: `gpt-5.3-codex-spark` launched on February 12,
2026 as a research preview in Codex for ChatGPT Pro users, and OpenAI’s Codex
rate card still describes it as a research preview with non-final credit rates.
OpenAI also says Codex-Spark is in the API only for a small set of design
partners, so you should not assume general API access to Spark.

For `get-my-domino`, the practical rule is:

- if you want the cheapest Codex option and your Codex account exposes it, set
  `speech_normalize_model = "gpt-5.3-codex-spark"`
- if Spark is not available on your account, fall back to
  `gpt-5.4-mini` or `gpt-5.3-codex` depending on whether you prefer lower cost
  or stronger coding-oriented behavior

Speech normalization config:

| Key | Meaning |
| --- | --- |
| `speech_normalize_auto` | Run AI speech normalization automatically before every audio generation. Keep `false` to enable it only with `--speech-normalize`. |
| `speech_normalize_agent` | AI backend name. Only `codex` is implemented; other configured backend names are reserved for future support. |
| `speech_normalize_command` | Executable used for the selected agent, usually `codex`; set a full path if the command is not on `PATH`. |
| `speech_normalize_model` | Optional model name passed to the agent. Empty means the agent CLI uses its default model. |
| `speech_normalize_timeout` | Maximum seconds allowed for one article normalization run before stopping it. |
| `speech_normalize_force` | Regenerate `.speech.txt` even when a reusable speech file already exists. |
| `speech_normalize_fallback` | If normalization fails, continue with the original `.txt` instead of stopping before audio. |
| `speech_normalize_prompt_path` | User-editable prompt template used by the normalizer. |

List the exact voice names that macOS `say` accepts:

```bash
get-my-domino voices
get-my-domino voices --all
```

Set `audio_auto = true` in the config to generate audio automatically whenever
`download`, `sync-magazine`, or `sync-feed` saves new articles. Use
`--no-audio` for one run without synthesis. `audio_format = "mp4a"` is accepted
as an alias for `m4a`.

Set `audiobook_auto = true` in the config to generate issue-level `.m4b`
audiobooks automatically for `sync-magazine` and `download --issue YYYY-NN --all`.
This does not affect `sync-feed`, which only produces article-level audio.

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
├── config.full.toml
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

When the Domino site layout changes, start with the maintainer note at
[docs/domino-site-structure.md](docs/domino-site-structure.md).
It documents the current crawl entry points, issue/feed selectors, article
extraction assumptions, and which config keys usually let you patch the scraper
without changing code.

## Release Notes

Before tagging a release:

1. update the version in `pyproject.toml`
2. update `src/get_my_domino/__init__.py`
3. add release notes to `CHANGELOG.md`
4. verify `make check`

## License

This project is released under the MIT License. See [LICENSE](LICENSE).
Root storage is configurable in two ways:

- set `output_dir` directly when you want one explicit absolute path
- or set `output_parent_dir` plus `collection_dir_name`

When `output_dir` is omitted, the default root folder becomes:

```text
output_parent_dir/collection_dir_name
```

`collection_dir_name` is intended to be a filesystem slug. By default it is
derived from `magazine_title` in lowercase with underscores, for example
`"Rivista Domino"` -> `rivista_domino`.

Audiobooks use a separate destination resolver:

- by default: `output_dir/audiobooks`
- if set explicitly: `audiobook_output_dir`

The HTTP `User-Agent` header is versioned automatically from the installed app.
It is no longer a config key because users should not need to update it by hand
when upgrading the CLI.
