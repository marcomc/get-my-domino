# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
- Human-readable catalog output with `YYYY-MM` issue codes, publication dates,
  issue summaries, grouped article trees, and optional feed listings.
- Targeted downloads by article URL, by issue month plus article number, or by
  complete issue with `download --issue YYYY-MM --all`.
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
