# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Initial project scaffold generated from `python-cli-template`.
- Added rivistadomino.it issue discovery, article download, text export, and
  macOS `.m4a` synthesis commands.
- Added configuration fields for subscriber-area authentication credentials.
- Added session-aware WooCommerce login using the configured TOML credentials.
- Added subscriber issue discovery defaults for the `my_domino` page and
  `?sfoglia=1` issue links.
- Added persistent WordPress cookie sessions and browser-assisted login.
- Added `feed` and `sync-feed` commands for recurring `La settimana di Domino`
  articles, with legacy `weekly` and `sync-weekly` aliases.
- Added `sync-magazine` as the primary magazine sync command, with legacy
  `sync` alias.
- Added issue-aware export layout with issue month folders, section folders,
  and dated article folders.
- Added date-first feed article folders for chronological sorting.
- Added UTF-8 text plus optional RTF exports for articles containing
  original-language characters.
- Added configurable `export_formats` and repeated `--format` flags for
  `html`, `txt`, and optional `rtf` article exports.
- Added automatic audio generation through `audio_auto` and configurable
  `audio_format` values `m4a`, `mp4a`, or `mp3`; MP3 conversion uses
  `ffmpeg`.
- Added `catalog` browsing for `YYYY-MM` issue codes, grouped issue contents, full
  issue expansion, and optional feed listings.
- Cleaned catalog issue output by removing storefront price text and sorting
  issues by `YYYY-MM` month.
- Simplified issue detail output by showing the issue publication date once and
  removing repeated dates from article rows.
- Added single-article downloads by issue month and article order.
- Added `download --issue YYYY-MM --all` to download every article from one
  selected issue.
- Documented command intent so `download` is clearly the targeted command,
  while `sync-magazine` and `sync-feed` are archive update commands.
- Reused existing manifest directories for explicit downloads so missing export
  formats or audio can be regenerated without duplicate article folders.
- Added `download --force` to explicitly refetch and rewrite existing article
  exports.
- Reused existing audio during downloads unless the audio file is missing or
  `--force` is requested.
- Documented that deleting one audio file is the narrow repair path for
  regenerating only that article, while `--force` regenerates every selected
  article.
- Added retries for transient HTTP connection drops while fetching Domino pages.
- Added friendly progress output for long download, retry, export, and audio
  generation steps, with an interactive indeterminate progress bar, completion
  check marks, and one total elapsed time per article.
- Reworked `download` output into compact per-article status rows showing
  export state, audio state, and elapsed time, with paths shown only in verbose
  mode.
- Handled `Ctrl-C` cleanly during downloads and speech synthesis, including
  terminating active subprocesses and removing temporary `.aiff` files.
- Added configurable `audio_timeout` and `--audio-timeout` handling so stuck
  `say`, `afconvert`, or `ffmpeg` subprocesses are stopped and temporary
  `.aiff` files are removed.
- Serialized macOS `say` synthesis across concurrent CLI processes to avoid
  overlapping Siri/neural TTS extension runs.
- Added visible queued/audio-engine status and temporary AIFF byte-growth
  feedback while generating audio.
- Added chunked audio synthesis for long articles, with configurable chunk
  size, parallelism, retries, and stall timeout. Chunk AIFF files are merged
  before the final M4A/MP3 conversion to avoid silent truncation from one long
  `say` process.
- Kept multi-article downloads and syncs running when one article audio
  conversion fails, then reported all audio failures at the end.
- Removed source URLs from `article.txt` and `article.rtf` body text so speech
  synthesis does not read article links aloud.
- Added issue title and detected `di ...` author lines to generated article
  text when that metadata is available.
- Renamed article export files to match their containing folder, removed dates
  from magazine article folder names, and slimmed `metadata.json` so it no
  longer stores full article HTML or text.
- Moved generated audio under `output_dir/audio/`, grouped by issue or feed, so
  audio players can browse audio without article export files.
- Added `voices` to list macOS `say` voice names and reject configured voices
  that `say` would silently ignore.
- Documented that Siri/neural voices require leaving `siri_voice` empty so
  audio generation calls `say` without `-v` and uses the macOS system voice.
- Replaced the redundant `weekly_output_dir` config key with
  `feed_folder_name`, derived from the main `output_dir`.
- Removed Domino header, featured, and separator images from exported article
  HTML.
