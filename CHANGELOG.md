# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- Added a dedicated `audiobook_output_dir` config key so packaged `.m4b`
  files can be written to an external audiobook library instead of always
  using `output_dir/audiobooks/`.

## [0.1.1] - 2026-04-30

### Issue Audiobooks

- Optional issue-level `.m4b` audiobook packaging for `download --issue
  YYYY-NN --all --audiobook` and `sync-magazine --audiobook`, including chapter
  markers from article order, embedded cover art, and issue metadata tags.
- Issue-level `issue.json` sidecars that capture the issue URL, release date,
  cover image path, publisher tag, summary text, and per-article directory
  mapping for downloaded magazine issues.
- Compatibility fallback for existing issue archives whose chapter audio files
  still use older order-prefixed filenames, so audiobook packaging can reuse
  mixed legacy and current audio trees.
- Issue-level contributor metadata in audiobook packaging, using article author
  sidecars to populate the issue `composer` tag, issue-sidecar contributor
  lists, and chapter titles such as `Title (di Author)`.
- New repair commands: `refresh-issue-metadata` to refresh downloaded issue
  metadata without touching audio, and `repackage-audiobook` to refresh that
  metadata and rebuild the `.m4b` from existing chapter audio.
- Configurable audiobook filename templates with config defaults, per-command
  CLI overrides, and a `rename-audiobooks` command that renames existing
  `.m4b` files from embedded metadata tags.
- Added `audiobook_auto` config support so full-issue sync/download flows can
  package `.m4b` files automatically without passing `--audiobook`.
- Added a clearer README disclaimer that this is an unofficial, unaffiliated,
  private-use accessibility/personal-reading tool for subscribers with lawful
  access, not a redistribution tool.
- Fixed long-running audio progress rendering on interactive terminals so
  `sync-feed`, `sync-magazine`, and other audio-generating commands reuse a
  single progress line instead of cluttering the terminal with wrapped frames,
  and aligned sync output to the same tabular status format used by downloads.
- Issue audiobook chapter titles now include the article index from the issue
  table of contents, and issue-level chapter sidecars now include the source
  section name for each article.
- Preferred naming config keys are now `magazine_title`,
  `filename_separator`, and `audiobook_name_format`, with the older
  `audiobook_filename_*` keys still accepted for backward compatibility.
- The storage layout is now rooted under `output_dir/library/` plus
  `output_dir/audiobooks/`, with magazine issues under `library/rivista`,
  weekly articles under `library/la-settimana-di-domino`, and single-article
  audio co-located in each article folder instead of a separate `audio/`
  tree. Existing legacy issue and audio trees are migrated lazily.
- Added `output_parent_dir` and `collection_dir_name` so the top-level
  collection directory can be derived from a configurable slug when `output_dir`
  is not set explicitly.
- Fixed article-author extraction to prefer explicit metadata and
  `article_byline` markup over unrelated site chrome text, including authors
  with initials such as `Z. Goggi`.
- Fixed `--no-audio` so it is respected even when `--audiobook` is requested.
- Documentation for choosing a specific Codex model for speech normalization
  through config or `--speech-normalize-model`, including examples for
  `gpt-5.3-codex-spark`.
- Refreshed the OpenAI model-availability documentation for speech
  normalization as of April 29, 2026, separating ChatGPT/Codex subscription
  availability from API model availability and clarifying that
  `gpt-5.3-codex-spark` remains a research preview.
- Maintainer documentation for the current Domino site structure, including the
  subscriber issue index, issue article-tab parsing, feed pagination, article
  extraction selectors, and the config/code touchpoints to update if the site
  markup changes.

## [0.1.0] - 2026-04-28

### Added

- Packaged Python CLI named `get-my-domino`, installable with `uv` and the
  project `Makefile`.
- TOML configuration with subscriber credentials, output paths, export formats,
  audio settings, and optional speech-normalization settings.
- Login support for rivistadomino.it using configured credentials or
  browser-assisted WordPress/WooCommerce cookies.
- Subscriber catalog browsing for available Domino magazine issues, issue
  contents, grouped sections, article order, and the recurring `La settimana di
  Domino` feed.
- Human-readable catalog output with `YYYY-NN` issue codes, publication dates,
  issue summaries, grouped article trees, and optional feed listings.
- Targeted downloads by article URL, by issue code plus article number, or by
  complete issue with `download --issue YYYY-NN --all`.
- Archive synchronization commands for magazine issues and feed articles,
  including legacy aliases for earlier command names.
- Incremental local storage using manifests so existing article exports and
  audio are reused unless files are missing or `--force` is requested.
- Clean article exports as HTML, UTF-8 text, optional RTF, and slim
  `metadata.json` files without embedding full article bodies in metadata.
- Configurable export format selection through `export_formats` and repeated
  `--format` flags.
- Magazine and feed folder layouts designed for browsing articles, plus a
  separate `output_dir/audio/` tree for audio-player-friendly files.
- Article text generation that includes issue title, article title, and detected
  author lines while excluding source URLs from speech input.
- HTML cleanup that removes Domino header, featured, and separator images from
  exported article pages.
- Local audio generation through macOS `say` plus `afconvert` for M4A/MP4A or
  `ffmpeg` for MP3.
- Support for the macOS system Siri/neural voice by leaving `siri_voice` empty,
  plus a `voices` command for listing explicit `say` voices.
- Chunked, serialized audio synthesis with retries, timeouts, temporary AIFF
  cleanup, byte-growth feedback, and graceful `Ctrl-C` handling.
- Multi-article download and sync runs continue after per-article audio errors
  and report failed audio files at the end.
- Friendly progress and status output for downloads, exports, reused files,
  audio generation, retries, and elapsed per-article timing.
- Optional AI-assisted speech text normalization through a generic external
  agent interface and an implemented Codex CLI backend.
- Installed, user-editable Codex speech-normalization prompt template for
  conservative Italian TTS cleanup without summarizing or rewriting articles.
- Documentation for commands, configuration keys, speech normalization, audio
  repair workflows, and release maintenance.
