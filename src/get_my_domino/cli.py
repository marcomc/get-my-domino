"""Command-line interface for get-my-domino."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import sys
import textwrap
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Sequence

from . import __version__
from .audio import AudioError, available_say_voices, normalize_audio_format, synthesize_audio
from .audiobook import AudiobookChapter, AudiobookError, build_m4b, read_audiobook_tags
from .audiobook_naming import (
    AudiobookFilenameSettings,
    render_audiobook_filename_for_issue,
    render_audiobook_filename_from_tags,
    validate_audiobook_format,
    validate_audiobook_separator,
)
from .browser_auth import BrowserAuthError, login_with_browser
from .config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LIBRARY_FOLDER_NAME,
    AppConfig,
    load_config,
    normalize_audio_timeout,
    normalize_export_formats,
    normalize_non_negative_int,
    normalize_positive_int,
)
from .extract import article_date_from_url, issue_code_from_text, slugify
from .models import Article, Issue, Link
from .session_store import clear_cookies
from .speech_normalize import (
    SpeechNormalizeError,
    SpeechNormalizeSettings,
    normalize_speech_text,
)
from .speech_normalize import (
    ensure_speech_text as _ensure_speech_text,
)
from .storage import (
    article_basename,
    article_text_path,
    missing_article_export_files,
    read_manifest,
    write_article,
    write_article_export,
    write_article_metadata,
    write_article_named,
    write_issue_metadata,
    write_manifest,
)
from .web import FetchError, WebClient, discover_articles, discover_feed_articles, discover_issues

COMMAND_NAMES = (
    "info",
    "login",
    "logout",
    "issues",
    "articles",
    "feed",
    "weekly",
    "catalog",
    "download",
    "sync-magazine",
    "sync",
    "sync-feed",
    "sync-weekly",
    "refresh-issue-metadata",
    "repackage-audiobook",
    "rename-audiobooks",
    "speak",
    "speech-normalize",
    "voices",
)


@dataclass(frozen=True)
class AudioOptions:
    create: bool
    audio_format: str
    timeout: float
    chunked: bool
    chunk_chars: int
    concurrency: int
    retries: int
    stall_timeout: float


@dataclass(frozen=True)
class AudioFailure:
    label: str
    target_dir: Path
    error: str


@dataclass(frozen=True)
class IssueBundlePlan:
    issue: Issue
    issue_dir: Path
    article_dirs: list[Path]


@dataclass(frozen=True)
class SpeechNormalizeOptions:
    enabled: bool
    agent: str
    command: str
    model: str
    timeout: float
    force: bool
    fallback: bool
    prompt_path: Path | None
    diff: bool = False


def format_main_help() -> str:
    return "\n".join(
        [
            "usage: get-my-domino [--version] [--config PATH] [--verbose] <command>",
            "",
            "Download rivistadomino.it articles as clean HTML, text, RTF, and audio.",
            "",
            "Commands:",
            "  catalog       Browse readable issue and feed indexes",
            "  download      Download selected URLs or articles from one issue",
            "  sync-magazine Download new magazine articles",
            "  sync-feed     Download new weekly feed articles",
            "  refresh-issue-metadata Refresh metadata.json and issue.json for downloaded issues",
            "  repackage-audiobook Refresh issue metadata and rebuild issue audiobooks",
            "  speech-normalize Prepare downloaded text for text-to-speech",
            "  speak         Convert downloaded article text to audio",
            "  voices        List macOS say voices available for audio",
            "",
            "Raw list commands:",
            "  issues    Raw issue URL list",
            "  articles  Raw article URL list for one issue",
            "  feed      Raw weekly feed URL list",
            "",
            "Account and diagnostics:",
            "  info      Show resolved configuration and runtime metadata",
            "  login     Create or refresh a saved authenticated session",
            "  logout    Remove the saved authenticated session",
            "",
            "Run `get-my-domino <command> --help` for command-specific help.",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show the installed version and exit.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Optional config file. Defaults to {DEFAULT_CONFIG_PATH}.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose mode for this run.",
    )

    subparsers = parser.add_subparsers(dest="command")

    info_parser = subparsers.add_parser(
        "info",
        help="Show resolved configuration and runtime metadata.",
    )
    info_parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON output.",
    )

    login_parser = subparsers.add_parser(
        "login",
        help="Create or refresh a saved authenticated session.",
    )
    login_parser.add_argument(
        "--browser",
        action="store_true",
        help="Open a browser for interactive login and save the resulting cookies.",
    )

    subparsers.add_parser(
        "logout",
        help="Remove the saved authenticated session.",
    )

    issues_parser = subparsers.add_parser(
        "issues",
        help="List available magazine issues.",
    )
    issues_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    articles_parser = subparsers.add_parser(
        "articles",
        help="List article links for one magazine issue.",
    )
    articles_parser.add_argument("issue_url", help="Issue page URL to scan.")
    articles_parser.add_argument("--json", action="store_true", help="Print JSON output.")

    feed_parser = subparsers.add_parser(
        "feed",
        aliases=["weekly"],
        help="List articles from the recurring article feed.",
    )
    feed_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    feed_parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Number of feed archive pages to scan. Defaults to 1.",
    )

    catalog_parser = subparsers.add_parser(
        "catalog",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Browse subscriber-accessible Domino content in a readable format. "
            "Without options it lists available "
            "issues; --issue YYYY-NN expands one issue; --all expands every issue; --feed "
            "appends weekly feed entries."
        ),
        epilog=(
            "Examples:\n"
            "  get-my-domino catalog\n"
            "  get-my-domino catalog --issue 2026-04\n"
            "  get-my-domino catalog --all --feed"
        ),
        help="Browse readable issue and feed indexes.",
    )
    catalog_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    catalog_parser.add_argument(
        "--all",
        action="store_true",
        help="Show every issue with grouped article contents.",
    )
    catalog_parser.add_argument(
        "--issue",
        help="Show one issue by YYYY-NN issue code or by issue URL.",
    )
    catalog_parser.add_argument(
        "--feed",
        action="store_true",
        help="Also show recurring feed entries.",
    )
    catalog_parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Number of feed archive pages to scan when --feed is used. Defaults to 1.",
    )

    download_parser = subparsers.add_parser(
        "download",
        help="Download selected article URLs, one issue article, or all articles in one issue.",
    )
    download_parser.add_argument(
        "article_urls",
        nargs="*",
        help="Article page URLs to download.",
    )
    download_parser.add_argument(
        "--issue",
        help="Select a magazine issue by YYYY-NN issue code.",
    )
    download_parser.add_argument(
        "--article",
        help="Article order inside --issue, such as 1 or 01.",
    )
    download_parser.add_argument(
        "--all",
        action="store_true",
        help="Download every article in --issue.",
    )
    download_parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload and rewrite exports even when the article already exists.",
    )
    download_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for exported article folders. Defaults to config output_dir.",
    )
    _add_issue_audiobook_option(download_parser)
    _add_audiobook_name_options(download_parser)
    _add_export_format_options(download_parser)
    _add_audio_options(download_parser)

    sync_parser = subparsers.add_parser(
        "sync-magazine",
        aliases=["sync"],
        help="Download new magazine issue articles.",
    )
    _add_issue_audiobook_option(sync_parser)
    _add_audiobook_name_options(sync_parser)
    _add_audio_options(sync_parser)
    _add_export_format_options(sync_parser)
    sync_parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload and rewrite exports and audio for already synced articles.",
    )
    sync_parser.add_argument(
        "--max-articles",
        type=int,
        help="Limit article downloads for smoke tests.",
    )

    sync_feed_parser = subparsers.add_parser(
        "sync-feed",
        aliases=["sync-weekly"],
        help="Download new recurring feed articles.",
    )
    _add_audio_options(sync_feed_parser)
    _add_export_format_options(sync_feed_parser)
    sync_feed_parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload and rewrite exports and audio for already synced feed articles.",
    )
    sync_feed_parser.add_argument(
        "--max-articles",
        type=int,
        help="Limit article downloads for smoke tests.",
    )
    sync_feed_parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Number of feed archive pages to scan. Defaults to 1.",
    )

    refresh_issue_parser = subparsers.add_parser(
        "refresh-issue-metadata",
        help="Refresh article metadata.json and issue.json for downloaded magazine issues.",
    )
    refresh_issue_parser.add_argument(
        "--issue",
        help="Refresh one issue by YYYY-NN issue code or by issue URL.",
    )
    refresh_issue_parser.add_argument(
        "--all",
        action="store_true",
        help="Refresh metadata for every available issue already present in the archive.",
    )
    refresh_issue_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory containing downloaded issue folders. Defaults to config output_dir.",
    )

    repackage_parser = subparsers.add_parser(
        "repackage-audiobook",
        help="Refresh issue metadata and rebuild issue .m4b audiobooks from existing audio files.",
    )
    repackage_parser.add_argument(
        "--issue",
        help="Repackage one issue by YYYY-NN issue code or by issue URL.",
    )
    repackage_parser.add_argument(
        "--all",
        action="store_true",
        help="Repackage every available issue already present in the archive.",
    )
    repackage_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory containing downloaded issue folders. Defaults to config output_dir.",
    )
    _add_audiobook_name_options(repackage_parser)

    rename_parser = subparsers.add_parser(
        "rename-audiobooks",
        help=(
            "Rename existing .m4b files from their embedded metadata using "
            "the configured filename template."
        ),
    )
    rename_parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help=(
            "Specific audiobook files or directories to scan. Defaults to "
            "output_dir/audiobooks recursively."
        ),
    )
    rename_parser.add_argument(
        "--library-dir",
        type=Path,
        help="Root directory to scan recursively for .m4b files.",
    )
    rename_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the rename plan without changing files.",
    )
    _add_audiobook_name_options(rename_parser)

    speak_parser = subparsers.add_parser(
        "speak",
        help="Convert downloaded text files to audio with macOS say.",
    )
    speak_parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="article.txt files or article directories. Defaults to all exports.",
    )
    speak_parser.add_argument("--voice", help="macOS voice name, overriding config siri_voice.")
    speak_parser.add_argument(
        "--audio-format",
        choices=["m4a", "mp4a", "mp3"],
        help="Audio file format. Defaults to config audio_format.",
    )
    speak_parser.add_argument(
        "--audio-timeout",
        type=float,
        help="Seconds before stopping a stuck audio command. Defaults to config audio_timeout.",
    )
    speak_parser.add_argument(
        "--audio-jobs",
        type=int,
        help="Number of article audio chunks to synthesize in parallel. Defaults to config.",
    )
    speak_parser.add_argument(
        "--audio-chunk-chars",
        type=int,
        help="Target characters per audio chunk. Defaults to config audio_chunk_chars.",
    )
    speak_parser.add_argument(
        "--audio-retries",
        type=int,
        help="Retries for a failed audio chunk. Defaults to config audio_chunk_retries.",
    )
    speak_parser.add_argument(
        "--audio-stall-timeout",
        type=float,
        help="Seconds without AIFF growth before retrying a chunk. Defaults to config.",
    )
    speak_parser.add_argument(
        "--no-audio-chunks",
        action="store_true",
        default=False,
        help="Use one macOS say process for the whole article.",
    )
    _add_speech_normalize_options(speak_parser)

    speech_parser = subparsers.add_parser(
        "speech-normalize",
        help="Prepare downloaded text files for text-to-speech with an AI agent.",
    )
    speech_parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Article text files or article directories to normalize.",
    )
    speech_parser.add_argument(
        "--diff",
        action="store_true",
        default=False,
        help="Print a unified diff after writing the speech-ready text.",
    )
    _add_speech_normalize_options(speech_parser)

    voices_parser = subparsers.add_parser(
        "voices",
        help="List voice names supported by macOS say.",
    )
    voices_parser.add_argument(
        "--all",
        action="store_true",
        help="Show all voices instead of only Italian voices.",
    )

    return parser


def _add_audio_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--audio",
        action="store_true",
        default=False,
        help="Create audio for each downloaded article.",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        default=False,
        help="Do not create audio even when config audio_auto is enabled.",
    )
    parser.add_argument(
        "--audio-format",
        choices=["m4a", "mp4a", "mp3"],
        help="Audio file format. Defaults to config audio_format.",
    )
    parser.add_argument(
        "--audio-timeout",
        type=float,
        help="Seconds before stopping a stuck audio command. Defaults to config audio_timeout.",
    )
    parser.add_argument(
        "--audio-jobs",
        type=int,
        help="Number of article audio chunks to synthesize in parallel. Defaults to config.",
    )
    parser.add_argument(
        "--audio-chunk-chars",
        type=int,
        help="Target characters per audio chunk. Defaults to config audio_chunk_chars.",
    )
    parser.add_argument(
        "--audio-retries",
        type=int,
        help="Retries for a failed audio chunk. Defaults to config audio_chunk_retries.",
    )
    parser.add_argument(
        "--audio-stall-timeout",
        type=float,
        help="Seconds without AIFF growth before retrying a chunk. Defaults to config.",
    )
    parser.add_argument(
        "--no-audio-chunks",
        action="store_true",
        default=False,
        help="Use one macOS say process for the whole article.",
    )
    _add_speech_normalize_options(parser)


def _add_issue_audiobook_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--audiobook",
        action="store_true",
        default=False,
        help="Package each full magazine issue as one chapterized .m4b audiobook with cover art.",
    )


def _add_audiobook_name_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--magazine-title",
        "--audiobook-magazine-title",
        dest="magazine_title",
        help="Magazine title token used when rendering audiobook filenames.",
    )
    parser.add_argument(
        "--filename-separator",
        "--audiobook-separator",
        dest="filename_separator",
        help="Safe filename separator token used by audiobook filename templates.",
    )
    parser.add_argument(
        "--audiobook-name-format",
        help=(
            "Audiobook filename template. Available fields: {magazine}, {magazine_slug}, "
            "{sep}, {year}, {number}, {issue}, {issue_compact}, {title}, {title_slug}."
        ),
    )


def _add_speech_normalize_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--speech-normalize",
        action="store_true",
        default=False,
        help="Create and use a speech-ready .speech.txt file before audio synthesis.",
    )
    parser.add_argument(
        "--no-speech-normalize",
        action="store_true",
        default=False,
        help="Disable speech normalization even when config enables it.",
    )
    parser.add_argument(
        "--speech-normalize-agent",
        choices=["codex", "codex-cloud", "github-cli", "github-copilot", "jelly"],
        help="AI agent backend. Only codex is implemented now.",
    )
    parser.add_argument(
        "--speech-normalize-command",
        help="CLI command used by the selected speech normalization agent.",
    )
    parser.add_argument(
        "--speech-normalize-model",
        help="Model name passed to the selected speech normalization agent.",
    )
    parser.add_argument(
        "--speech-normalize-timeout",
        type=float,
        help="Seconds before stopping the AI speech normalization command.",
    )
    parser.add_argument(
        "--speech-normalize-force",
        action="store_true",
        default=False,
        help="Regenerate .speech.txt even when it already exists.",
    )
    parser.add_argument(
        "--speech-normalize-fallback",
        action="store_true",
        default=False,
        help="Use the original .txt if AI speech normalization fails.",
    )
    parser.add_argument(
        "--speech-normalize-prompt",
        type=Path,
        help="Prompt template file passed to the selected speech normalization agent.",
    )


def _add_export_format_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        dest="export_formats",
        action="append",
        choices=["html", "txt", "text", "rtf"],
        help=(
            "Export format to write. Repeat for multiple formats. "
            "Defaults to config export_formats."
        ),
    )


def _audio_options(args: argparse.Namespace, config: AppConfig) -> AudioOptions:
    if bool(getattr(args, "no_audio", False)):
        create_audio = False
    else:
        create_audio = bool(getattr(args, "audio", False)) or config.audio_auto
    raw_format = str(getattr(args, "audio_format", None) or config.audio_format)
    raw_timeout = getattr(args, "audio_timeout", None)
    audio_timeout = normalize_audio_timeout(
        config.audio_timeout if raw_timeout is None else raw_timeout
    )
    raw_jobs = getattr(args, "audio_jobs", None)
    raw_chunk_chars = getattr(args, "audio_chunk_chars", None)
    raw_retries = getattr(args, "audio_retries", None)
    raw_stall_timeout = getattr(args, "audio_stall_timeout", None)
    return AudioOptions(
        create=create_audio,
        audio_format=normalize_audio_format(raw_format),
        timeout=audio_timeout,
        chunked=config.audio_chunked and not bool(getattr(args, "no_audio_chunks", False)),
        chunk_chars=normalize_positive_int(
            config.audio_chunk_chars if raw_chunk_chars is None else raw_chunk_chars,
            key="audio_chunk_chars",
        ),
        concurrency=normalize_positive_int(
            config.audio_chunk_concurrency if raw_jobs is None else raw_jobs,
            key="audio_chunk_concurrency",
        ),
        retries=normalize_non_negative_int(
            config.audio_chunk_retries if raw_retries is None else raw_retries,
            key="audio_chunk_retries",
        ),
        stall_timeout=normalize_audio_timeout(
            config.audio_stall_timeout if raw_stall_timeout is None else raw_stall_timeout
        ),
    )


def _audiobook_filename_settings(
    args: argparse.Namespace,
    config: AppConfig,
) -> AudiobookFilenameSettings:
    magazine_title = str(getattr(args, "magazine_title", None) or config.magazine_title)
    separator = validate_audiobook_separator(
        str(getattr(args, "filename_separator", None) or config.filename_separator)
    )
    format_template = validate_audiobook_format(
        str(getattr(args, "audiobook_name_format", None) or config.audiobook_name_format)
    )
    return AudiobookFilenameSettings(
        magazine_title=magazine_title,
        separator=separator,
        format_template=format_template,
    )


def _configured_audiobook_filename_settings(config: AppConfig) -> AudiobookFilenameSettings:
    return AudiobookFilenameSettings(
        magazine_title=config.magazine_title,
        separator=validate_audiobook_separator(config.filename_separator),
        format_template=validate_audiobook_format(config.audiobook_name_format),
    )


def _library_dir(root_output_dir: Path) -> Path:
    return root_output_dir / "library"


def _magazine_output_dir(root_output_dir: Path) -> Path:
    return _library_dir(root_output_dir) / "rivista"


def _feed_output_dir(root_output_dir: Path, config: AppConfig) -> Path:
    return _library_dir(root_output_dir) / config.feed_folder_name


def _audiobooks_dir(root_output_dir: Path, config: AppConfig) -> Path:
    del root_output_dir
    return config.audiobooks_dir


def _ensure_storage_layout(root_output_dir: Path, config: AppConfig) -> None:
    library_dir = _library_dir(root_output_dir)
    magazine_dir = _magazine_output_dir(root_output_dir)
    weekly_dir = _feed_output_dir(root_output_dir, config)
    audiobook_dir = _audiobooks_dir(root_output_dir, config)
    legacy_audiobook_dir = root_output_dir / "audiobooks"
    library_dir.mkdir(parents=True, exist_ok=True)
    magazine_dir.mkdir(parents=True, exist_ok=True)
    weekly_dir.mkdir(parents=True, exist_ok=True)

    if (
        audiobook_dir != legacy_audiobook_dir
        and legacy_audiobook_dir.exists()
        and not audiobook_dir.exists()
        and not audiobook_dir.is_relative_to(legacy_audiobook_dir)
    ):
        audiobook_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_audiobook_dir), str(audiobook_dir))
    audiobook_dir.mkdir(parents=True, exist_ok=True)

    legacy_manifest = root_output_dir / "manifest.json"
    if legacy_manifest.exists():
        target_manifest = magazine_dir / "manifest.json"
        if not target_manifest.exists():
            legacy_manifest.rename(target_manifest)

    legacy_weekly_dir = root_output_dir / config.feed_folder_name
    if (
        legacy_weekly_dir.exists()
        and legacy_weekly_dir != weekly_dir
        and not any(weekly_dir.iterdir())
    ):
        weekly_dir.rmdir()
        legacy_weekly_dir.rename(weekly_dir)

    for child in root_output_dir.iterdir():
        if not child.is_dir():
            continue
        if child.name in {"library", "audiobooks", "audio", config.feed_folder_name}:
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-.+", child.name):
            continue
        target_dir = magazine_dir / child.name
        if not target_dir.exists():
            child.rename(target_dir)

    _migrate_legacy_audio_tree(root_output_dir, config)


def _migrate_legacy_audio_tree(root_output_dir: Path, config: AppConfig) -> None:
    legacy_audio_dir = root_output_dir / "audio"
    if not legacy_audio_dir.exists():
        return
    for audio_path in sorted(legacy_audio_dir.rglob("*")):
        if not audio_path.is_file():
            continue
        if audio_path.name == ".DS_Store":
            audio_path.unlink(missing_ok=True)
            continue
        target_dir = _legacy_audio_target_dir(
            audio_path,
            root_output_dir=root_output_dir,
            config=config,
        )
        if target_dir is None:
            continue
        target_path = target_dir / f"{target_dir.name}{audio_path.suffix.lower()}"
        target_dir.mkdir(parents=True, exist_ok=True)
        if not target_path.exists():
            shutil.move(str(audio_path), str(target_path))
    for directory in sorted(legacy_audio_dir.rglob("*"), reverse=True):
        if directory.is_dir():
            try:
                directory.rmdir()
            except OSError:
                continue
    try:
        legacy_audio_dir.rmdir()
    except OSError:
        pass


def _legacy_audio_target_dir(
    legacy_audio_path: Path,
    *,
    root_output_dir: Path,
    config: AppConfig,
) -> Path | None:
    stem = legacy_audio_path.stem
    library_dir = _library_dir(root_output_dir)
    direct_matches = [path for path in library_dir.glob(f"**/{stem}") if path.is_dir()]
    if len(direct_matches) == 1:
        return direct_matches[0]

    issue_scope = _magazine_output_dir(root_output_dir) / legacy_audio_path.parent.name
    if issue_scope.exists():
        scoped_match = _legacy_issue_audio_target_dir(issue_scope, stem=stem)
        if scoped_match is not None:
            return scoped_match

    weekly_scope = _feed_output_dir(root_output_dir, config)
    if weekly_scope.exists():
        scoped_match = _legacy_weekly_audio_target_dir(weekly_scope, stem=stem)
        if scoped_match is not None:
            return scoped_match
    return None


def _legacy_issue_audio_target_dir(issue_scope: Path, *, stem: str) -> Path | None:
    order_match = re.match(r"(?P<order>\d{2,3})-(?:\d{4}-\d{2}-\d{2}-)?(?P<slug>.+)", stem)
    if not order_match:
        return None
    order = order_match.group("order")
    slug = order_match.group("slug")
    candidates = [path for path in issue_scope.glob(f"**/{order}-*") if path.is_dir()]
    if len(candidates) == 1:
        return candidates[0]
    slug_candidates = [path for path in candidates if slug in path.name]
    if len(slug_candidates) == 1:
        return slug_candidates[0]
    return None


def _legacy_weekly_audio_target_dir(weekly_scope: Path, *, stem: str) -> Path | None:
    candidates = [path for path in weekly_scope.glob(f"**/{stem}") if path.is_dir()]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _audiobook_requested(args: argparse.Namespace, config: AppConfig) -> bool:
    if bool(getattr(args, "no_audio", False)):
        return False
    return bool(getattr(args, "audiobook", False)) or config.audiobook_auto


def _speech_normalize_options(
    args: argparse.Namespace,
    config: AppConfig,
    *,
    diff: bool = False,
) -> SpeechNormalizeOptions:
    if bool(getattr(args, "no_speech_normalize", False)):
        enabled = False
    else:
        enabled = bool(getattr(args, "speech_normalize", False)) or config.speech_normalize_auto
    raw_timeout = getattr(args, "speech_normalize_timeout", None)
    return SpeechNormalizeOptions(
        enabled=enabled,
        agent=str(getattr(args, "speech_normalize_agent", None) or config.speech_normalize_agent),
        command=str(
            getattr(args, "speech_normalize_command", None) or config.speech_normalize_command
        ),
        model=str(getattr(args, "speech_normalize_model", None) or config.speech_normalize_model),
        timeout=normalize_audio_timeout(
            config.speech_normalize_timeout if raw_timeout is None else raw_timeout
        ),
        force=bool(getattr(args, "speech_normalize_force", False)) or config.speech_normalize_force,
        fallback=bool(getattr(args, "speech_normalize_fallback", False))
        or config.speech_normalize_fallback,
        prompt_path=getattr(args, "speech_normalize_prompt", None)
        or config.speech_normalize_prompt_path,
        diff=diff,
    )


def _speech_settings(options: SpeechNormalizeOptions) -> SpeechNormalizeSettings:
    return SpeechNormalizeSettings(
        enabled=options.enabled,
        agent=options.agent,
        command=options.command,
        model=options.model,
        timeout=options.timeout,
        force=options.force,
        fallback=options.fallback,
        prompt_path=options.prompt_path,
        diff=options.diff,
    )


def ensure_speech_text(
    source_text_path: Path,
    *,
    options: SpeechNormalizeOptions,
) -> Path:
    return _ensure_speech_text(source_text_path, _speech_settings(options))


def _export_format_options(args: argparse.Namespace, config: AppConfig) -> tuple[str, ...]:
    raw_formats = getattr(args, "export_formats", None)
    if raw_formats:
        return normalize_export_formats(raw_formats)
    return config.export_formats


def _info_payload(config: AppConfig, config_path: Path) -> dict[str, object]:
    config_data = asdict(config)
    config_data["auth_password"] = "configured" if config.auth_password else "missing"
    return {
        "project_name": "get-my-domino",
        "cli_name": "get-my-domino",
        "package_name": "get_my_domino",
        "version": __version__,
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "config": config_data,
    }


def _json_default(value: object) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable.")


def _print_links(links: list[Link], *, as_json: bool) -> int:
    if as_json:
        print(json.dumps([asdict(link) for link in links], indent=2, sort_keys=True))
        return 0
    for index, link in enumerate(links, start=1):
        print(f"{index:03d}  {link.title}")
        if link.group:
            print(f"     group: {link.group}")
        if link.published_date:
            print(f"     date: {link.published_date}")
        print(f"     {link.url}")
    return 0


def _handle_catalog(
    config: AppConfig,
    *,
    client: WebClient | None = None,
    all_issues: bool,
    issue_selector: str | None,
    include_feed: bool,
    feed_pages: int,
    as_json: bool,
) -> int:
    web_client = client or WebClient(config)
    issues = _sort_catalog_issues(web_client.discover_issues())
    selected_issues = _selected_catalog_issues(
        web_client,
        issues,
        all_issues=all_issues,
        issue_selector=issue_selector,
    )
    feed_links = web_client.discover_feed_articles(max_pages=feed_pages) if include_feed else []

    if as_json:
        print(
            json.dumps(
                {
                    "issues": [asdict(issue) for issue in issues],
                    "selected_issues": [asdict(issue) for issue in selected_issues],
                    "feed": [asdict(link) for link in feed_links],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if selected_issues:
        for index, issue in enumerate(selected_issues, start=1):
            if index > 1:
                print()
            _print_issue_detail(issue)
    else:
        _print_issue_index(issues)

    if include_feed:
        if selected_issues:
            print()
        _print_feed_index(feed_links)

    return 0


def _selected_catalog_issues(
    client: WebClient,
    issues: list[Link],
    *,
    all_issues: bool,
    issue_selector: str | None,
) -> list[Issue]:
    if all_issues:
        return [client.discover_issue(issue.url) for issue in issues]
    if issue_selector is None:
        return []
    return [client.discover_issue(_resolve_issue_selector(issues, issue_selector))]


def _resolve_issue_selector(issues: list[Link], selector: str) -> str:
    if selector.isdecimal():
        raise ValueError(
            "Numeric issue selectors are no longer used. "
            "Use a YYYY-NN issue code from `catalog`, or pass the issue URL."
        )
    if re.fullmatch(r"\d{4}-\d{2}", selector):
        matches = [issue for issue in issues if issue_code_from_text(issue.title) == selector]
        if not matches:
            raise ValueError(f"Issue code {selector} was not found in the available catalog.")
        if len(matches) > 1:
            raise ValueError(f"Issue code {selector} matched multiple issues; pass the issue URL.")
        return matches[0].url
    return selector


def _print_issue_index(issues: list[Link]) -> None:
    print("Available issues")
    print("================")
    if not issues:
        print("No issues found.")
        return
    for index, issue in enumerate(issues, start=1):
        issue_code, title, synopsis = _issue_summary_parts(issue.title)
        display_issue_code = issue_code or "unknown"
        print(f"{display_issue_code}  {title}")
        if synopsis:
            for line in textwrap.wrap(synopsis, width=88):
                print(f"    {line}")
        print(f"    {issue.url}")
        if index < len(issues):
            print()


def _print_issue_detail(issue: Issue) -> None:
    title = _display_title(issue.title)
    print(title)
    print("=" * len(title))
    if issue.issue_code:
        print(f"issue: {issue.issue_code}")
    published_date = _issue_published_date(issue)
    if published_date:
        print(f"published: {published_date}")
    print(f"url:   {issue.url}")
    if not issue.articles:
        print("\nNo articles found.")
        return
    print("\ncontents:")
    groups = _group_article_links(issue.articles)
    for group_index, (group_name, links) in enumerate(groups, start=1):
        is_last_group = group_index == len(groups)
        group_branch = "└──" if is_last_group else "├──"
        child_prefix = "    " if is_last_group else "│   "
        print(f"{group_branch} {group_index:02d}  {_display_title(group_name)}")
        for article_index, link in enumerate(links, start=1):
            article_branch = "└──" if article_index == len(links) else "├──"
            order = link.order or links.index(link) + 1
            title = _style_article_title(_display_title(link.title))
            print(f"{child_prefix}{article_branch} {order:02d}  {title}")
            print(f"{child_prefix}    url: {link.url}")


def _group_article_links(links: list[Link]) -> list[tuple[str, list[Link]]]:
    groups: list[tuple[str, list[Link]]] = []
    group_indexes: dict[str, int] = {}
    for link in links:
        group_name = link.group or "Articoli"
        if group_name not in group_indexes:
            group_indexes[group_name] = len(groups)
            groups.append((group_name, []))
        groups[group_indexes[group_name]][1].append(link)
    return groups


def _print_feed_index(links: list[Link]) -> None:
    print("La settimana di Domino")
    print("======================")
    if not links:
        print("No feed entries found.")
        return
    for index, link in enumerate(links, start=1):
        date = link.published_date or article_date_from_url(link.url) or "unknown-date"
        print(f"{index:03d}. {date}  {_display_title(link.title)}")
        print(f"     url: {link.url}")


def _display_title(title: str) -> str:
    return _strip_price_text(title)


def _style_article_title(title: str) -> str:
    if not _supports_terminal_color():
        return title
    return f"\033[36m{title}\033[0m"


def _supports_terminal_color() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("CLICOLOR") == "0":
        return False
    return os.environ.get("TERM", "").lower() != "dumb"


def _issue_published_date(issue: Issue) -> str | None:
    dates = {
        date
        for link in issue.articles
        if (date := link.published_date or article_date_from_url(link.url)) is not None
    }
    if not dates:
        return None
    if len(dates) == 1:
        return next(iter(dates))
    return min(dates)


def _sort_catalog_issues(issues: list[Link]) -> list[Link]:
    return sorted(issues, key=lambda issue: _issue_sort_key(issue.title), reverse=True)


def _issue_sort_key(title: str) -> tuple[str, str]:
    issue_code = issue_code_from_text(title) or "0000-00"
    return (issue_code, _strip_price_text(title))


def _issue_summary_parts(title: str) -> tuple[str | None, str, str | None]:
    clean_title = " ".join(title.split())
    issue_code = issue_code_from_text(clean_title)
    first_price = re.search(_PRICE_PATTERN, clean_title)
    if first_price is None:
        return issue_code, _strip_issue_code(_strip_price_text(clean_title)), None

    title_part = clean_title[: first_price.start()]
    synopsis_part = clean_title[first_price.end() :]
    title_part = _strip_issue_code(_strip_price_text(title_part))
    synopsis_part = _strip_price_text(synopsis_part).strip(" -–—:")
    return issue_code, title_part, synopsis_part or None


_CURRENCY_PATTERN = r"(?:€|\bEUR\b)"
_PRICE_PATTERN = (
    rf"(?:\d+(?:[.,]\d{{1,2}})?\s*{_CURRENCY_PATTERN}|"
    rf"{_CURRENCY_PATTERN}\s*\d+(?:[.,]\d{{1,2}})?)"
)


def _strip_price_text(title: str) -> str:
    without_price_range = re.sub(
        rf"Fascia di prezzo:\s*da\s*{_PRICE_PATTERN}\s*a\s*{_PRICE_PATTERN}",
        "",
        title,
        flags=re.IGNORECASE,
    )
    without_price_range = re.sub(
        rf"{_PRICE_PATTERN}\s*[-–—]\s*{_PRICE_PATTERN}",
        "",
        without_price_range,
        flags=re.IGNORECASE,
    )
    without_prices = re.sub(_PRICE_PATTERN, "", without_price_range, flags=re.IGNORECASE)
    return " ".join(without_prices.replace(" - ", " ").split())


def _strip_issue_code(title: str) -> str:
    return re.sub(r"^\d{1,2}/\d{4}\s+", "", title).strip()


def _handle_info(config: AppConfig, config_path: Path, as_json: bool) -> int:
    payload = _info_payload(config=config, config_path=config_path)
    if as_json:
        print(json.dumps(payload, default=_json_default, indent=2, sort_keys=True))
        return 0

    print(f"project_name: {payload['project_name']}")
    print(f"cli_name: {payload['cli_name']}")
    print(f"package_name: {payload['package_name']}")
    print(f"version: {payload['version']}")
    print(f"config_path: {payload['config_path']}")
    print(f"config_exists: {payload['config_exists']}")
    print(f"verbose: {config.verbose}")
    print(f"magazine_index_url: {config.magazine_index_url}")
    print(f"output_parent_dir: {config.output_parent_dir}")
    print(f"collection_dir_name: {config.collection_dir_name}")
    print(f"output_dir: {config.output_dir}")
    print(f"library_dir: {config.library_dir}")
    print(f"magazine_output_dir: {config.magazine_output_dir}")
    print(f"feed_output_dir: {config.feed_output_dir}")
    print(f"audiobooks_dir: {config.audiobooks_dir}")
    print(f"siri_voice: {config.siri_voice or ''}")
    print(f"audio_auto: {config.audio_auto}")
    print(f"audiobook_auto: {config.audiobook_auto}")
    print(f"audio_format: {config.audio_format}")
    print(f"audio_chunked: {config.audio_chunked}")
    print(f"audio_chunk_chars: {config.audio_chunk_chars}")
    print(f"audio_chunk_concurrency: {config.audio_chunk_concurrency}")
    print(f"audio_chunk_retries: {config.audio_chunk_retries}")
    print(f"audio_stall_timeout: {config.audio_stall_timeout}")
    print(f"speech_normalize_auto: {config.speech_normalize_auto}")
    print(f"speech_normalize_agent: {config.speech_normalize_agent}")
    print(f"speech_normalize_command: {config.speech_normalize_command}")
    print(f"speech_normalize_model: {config.speech_normalize_model}")
    print(f"speech_normalize_timeout: {config.speech_normalize_timeout}")
    print(f"speech_normalize_force: {config.speech_normalize_force}")
    print(f"speech_normalize_fallback: {config.speech_normalize_fallback}")
    print(f"speech_normalize_prompt_path: {config.speech_normalize_prompt_path}")
    print(f"export_formats: {', '.join(config.export_formats)}")
    print(f"auth_login_url: {config.auth_login_url}")
    print(f"auth_username: {_auth_username_display(config.auth_username)}")
    print(f"auth_password: {'configured' if config.auth_password else 'missing'}")
    print(f"auth_session_path: {config.auth_session_path}")
    print(f"auth_session_exists: {config.auth_session_path.exists()}")
    return 0


def _auth_username_display(value: str) -> str:
    if value:
        return value
    return "missing (set auth_username in config.toml or use login --browser)"


def _handle_voices(*, all_voices: bool) -> int:
    voices = available_say_voices(locale_prefix=None if all_voices else "it_")
    for voice in voices:
        print(f"{voice.name} [{voice.locale}]")
    return 0


def _handle_login(config: AppConfig, *, use_browser: bool) -> int:
    if use_browser:
        login_with_browser(config)
    else:
        WebClient(config).authenticate()
    print(f"session: {config.auth_session_path}")
    return 0


def _handle_logout(config: AppConfig) -> int:
    clear_cookies(config.auth_session_path)
    print(f"removed: {config.auth_session_path}")
    return 0


def _download_articles(
    article_urls: list[str],
    config: AppConfig,
    output_dir: Path,
    *,
    create_audio: bool,
    audio_format: str,
    audio_timeout: float,
    audio_chunked: bool = True,
    audio_chunk_chars: int = 2500,
    audio_concurrency: int = 3,
    audio_retries: int = 2,
    audio_stall_timeout: float = 45.0,
    speech_options: SpeechNormalizeOptions | None = None,
    export_formats: tuple[str, ...] | None = None,
    force: bool = False,
    issue_titles: dict[str, str] | None = None,
    target_dirs: dict[str, Path] | None = None,
    metadata_by_url: dict[str, dict[str, object]] | None = None,
) -> int:
    client = WebClient(config)
    selected_formats = export_formats or config.export_formats
    manifest = read_manifest(output_dir)
    next_index = len(manifest) + 1
    audio_failures: list[AudioFailure] = []
    print(_style_download_header())
    for article_url in article_urls:
        article_started_at = time.monotonic()
        existing_dir = _existing_article_dir(
            manifest,
            article_url,
            output_dir=output_dir,
        )
        planned_dir = _planned_article_dir(target_dirs, article_url)
        audio_status = "off"
        if existing_dir is None and planned_dir is not None:
            existing_dir = planned_dir
        if existing_dir is not None:
            target_dir = existing_dir
            article_label = target_dir.name
            missing_files = missing_article_export_files(
                target_dir, export_formats=selected_formats
            )
            if force or missing_files:
                reason = "force" if force else f"missing {', '.join(missing_files)}"
                with _progress_step(f"Downloading article ({reason})"):
                    article = client.download_article(article_url)
                article = _with_article_context(article, issue_titles=issue_titles)
                with _progress_step(f"Writing files in {target_dir.name}"):
                    write_article_export(
                        target_dir,
                        article,
                        export_formats=selected_formats,
                        metadata=_article_metadata_context(metadata_by_url, article.url),
                    )
                manifest[article.url] = str(target_dir)
                article_label = article.title
                export_status = "written"
            else:
                export_status = "reused"
            if create_audio:
                audio_status = _ensure_audio_for_download(
                    target_dir,
                    output_dir=output_dir,
                    voice=config.siri_voice,
                    audio_format=audio_format,
                    timeout=audio_timeout,
                    force=force,
                    failures=audio_failures,
                    label=article_label,
                    chunked=audio_chunked,
                    chunk_chars=audio_chunk_chars,
                    concurrency=audio_concurrency,
                    retries=audio_retries,
                    stall_timeout=audio_stall_timeout,
                    speech_options=speech_options,
                )
            _print_download_result(
                article_label,
                export_status=export_status,
                audio_status=audio_status,
                elapsed=_format_duration(time.monotonic() - article_started_at),
                target_dir=target_dir,
                verbose=config.verbose,
            )
            continue

        with _progress_step("Downloading article"):
            article = client.download_article(article_url)
        article = _with_article_context(article, issue_titles=issue_titles)
        metadata_context = _article_metadata_context(metadata_by_url, article.url)
        fallback_author = (
            str(metadata_context.get("issue_author"))
            if metadata_context is not None and metadata_context.get("issue_author")
            else None
        )
        article = _with_fallback_author(article, fallback_author=fallback_author)
        if article.url in manifest:
            target_dir = Path(manifest[article.url]).expanduser()
            with _progress_step(f"Writing files in {target_dir.name}"):
                write_article_export(
                    target_dir,
                    article,
                    export_formats=selected_formats,
                    metadata=metadata_context,
                )
        elif planned_dir is not None:
            target_dir = planned_dir
            with _progress_step(f"Writing files in {target_dir.name}"):
                write_article_export(
                    target_dir,
                    article,
                    export_formats=selected_formats,
                    metadata=metadata_context,
                )
        else:
            target_dir = article_dir_for_index(output_dir, article, index=next_index)
            with _progress_step(f"Writing files in {target_dir.name}"):
                target_dir = write_article(
                    output_dir,
                    article,
                    index=next_index,
                    export_formats=selected_formats,
                    metadata=metadata_context,
                )
            next_index += 1
        manifest[article.url] = str(target_dir)
        export_status = "written"
        if create_audio:
            audio_status = _ensure_audio_for_download(
                target_dir,
                output_dir=output_dir,
                voice=config.siri_voice,
                audio_format=audio_format,
                timeout=audio_timeout,
                force=force,
                failures=audio_failures,
                label=article.title,
                chunked=audio_chunked,
                chunk_chars=audio_chunk_chars,
                concurrency=audio_concurrency,
                retries=audio_retries,
                stall_timeout=audio_stall_timeout,
                speech_options=speech_options,
            )
        _print_download_result(
            article.title,
            export_status=export_status,
            audio_status=audio_status,
            elapsed=_format_duration(time.monotonic() - article_started_at),
            target_dir=target_dir,
            verbose=config.verbose,
        )
    write_manifest(output_dir, manifest)
    if audio_failures:
        _print_audio_failures(audio_failures)
        return 1
    return 0


def _style_download_header() -> str:
    columns = (
        _style_muted("article"),
        _style_muted("export"),
        _style_muted("audio"),
        _style_muted("time"),
    )
    return f"{columns[0]:<58} {columns[1]:<10} {columns[2]:<10} {columns[3]}"


def _print_download_result(
    article_label: str,
    *,
    export_status: str,
    audio_status: str,
    elapsed: str,
    target_dir: Path,
    verbose: bool,
) -> None:
    marker = _style_success("✓")
    title = _truncate(article_label, width=56)
    export_label = _style_status(export_status)
    audio_label = _style_status(audio_status)
    print(f"{marker} {title:<56} {export_label:<10} {audio_label:<10} {elapsed}")
    if verbose:
        print(f"  {target_dir}")


def _style_status(status: str) -> str:
    if status in {"written", "generated"}:
        return _style_success(status)
    if status == "reused":
        return _style_info(status)
    if status == "off":
        return _style_muted(status)
    if status == "failed":
        return _ansi(status, "31")
    return status


def _ensure_audio_for_download(
    raw_path: Path,
    *,
    output_dir: Path,
    voice: str | None,
    audio_format: str,
    timeout: float,
    force: bool,
    failures: list[AudioFailure],
    label: str,
    chunked: bool,
    chunk_chars: int,
    concurrency: int,
    retries: int,
    stall_timeout: float,
    speech_options: SpeechNormalizeOptions | None,
) -> str:
    try:
        status, _ = _ensure_audio(
            raw_path,
            output_dir=output_dir,
            voice=voice,
            audio_format=audio_format,
            timeout=timeout,
            force=force,
            chunked=chunked,
            chunk_chars=chunk_chars,
            concurrency=concurrency,
            retries=retries,
            stall_timeout=stall_timeout,
            speech_options=speech_options,
        )
    except AudioError as exc:
        failures.append(AudioFailure(label=label, target_dir=raw_path, error=str(exc)))
        return "failed"
    return status


def _print_audio_failures(failures: list[AudioFailure]) -> None:
    print(f"audio failures: {len(failures)}", file=sys.stderr)
    for failure in failures:
        print(f"  - {failure.label}", file=sys.stderr)
        print(f"    path: {failure.target_dir}", file=sys.stderr)
        print(f"    reason: {failure.error}", file=sys.stderr)


def _style_success(value: str) -> str:
    return _ansi(value, "32")


def _style_info(value: str) -> str:
    return _ansi(value, "36")


def _style_muted(value: str) -> str:
    return _ansi(value, "2")


def _ansi(value: str, code: str) -> str:
    if not _supports_terminal_color():
        return value
    return f"\033[{code}m{value}\033[0m"


def _truncate(value: str, *, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."


def _planned_article_dir(target_dirs: dict[str, Path] | None, article_url: str) -> Path | None:
    if not target_dirs:
        return None
    return target_dirs.get(article_url) or target_dirs.get(article_url.rstrip("/"))


def _article_metadata_context(
    metadata_by_url: dict[str, dict[str, object]] | None,
    article_url: str,
) -> dict[str, object] | None:
    if not metadata_by_url:
        return None
    return metadata_by_url.get(article_url) or metadata_by_url.get(article_url.rstrip("/"))


def _with_article_context(
    article: Article,
    *,
    issue_titles: dict[str, str] | None,
) -> Article:
    if not issue_titles:
        return article
    issue_title = issue_titles.get(article.url) or issue_titles.get(article.url.rstrip("/"))
    if not issue_title:
        return article
    return replace(article, issue_title=issue_title)


def _with_fallback_author(article: Article, *, fallback_author: str | None) -> Article:
    if article.author or not fallback_author:
        return article
    return replace(article, author=fallback_author)


def _remap_legacy_manifest_dir(path: Path, *, output_dir: Path) -> Path:
    if path.exists():
        return path
    if output_dir.parent.name != DEFAULT_LIBRARY_FOLDER_NAME:
        return path
    root_output_dir = output_dir.parent.parent
    remapped_candidates: list[Path] = []
    legacy_container = root_output_dir / output_dir.name
    try:
        remapped_candidates.append(output_dir / path.relative_to(legacy_container))
    except ValueError:
        pass
    try:
        remapped_candidates.append(output_dir / path.relative_to(root_output_dir))
    except ValueError:
        pass
    for candidate in remapped_candidates:
        if candidate.exists():
            return candidate
    return path


def _existing_article_dir(
    manifest: dict[str, str],
    article_url: str,
    *,
    output_dir: Path,
) -> Path | None:
    if article_url in manifest:
        return _remap_legacy_manifest_dir(
            Path(manifest[article_url]).expanduser(),
            output_dir=output_dir,
        )
    normalized_url = article_url.rstrip("/")
    if normalized_url in manifest:
        return _remap_legacy_manifest_dir(
            Path(manifest[normalized_url]).expanduser(),
            output_dir=output_dir,
        )
    return None


def _resolved_issue_article_dirs(
    issue: Issue,
    *,
    output_dir: Path,
    planned_dirs: dict[str, Path],
) -> list[Path]:
    manifest = read_manifest(output_dir)
    resolved_dirs: list[Path] = []
    for article in issue.articles:
        resolved_dir = _existing_article_dir(
            manifest,
            article.url,
            output_dir=output_dir,
        )
        if resolved_dir is not None:
            resolved_dirs.append(resolved_dir)
            continue
        planned_dir = _planned_article_dir(planned_dirs, article.url)
        if planned_dir is None:
            raise ValueError(f"Missing planned directory for issue article: {article.url}")
        resolved_dirs.append(planned_dir)
    return resolved_dirs


def _planned_issue_article_dirs(issue: Issue, *, issue_dir: Path) -> list[Path]:
    group_indexes = _group_indexes(issue.articles)
    planned_dirs: list[Path] = []
    for fallback_index, article_link in enumerate(issue.articles, start=1):
        group_name = article_link.group or "Articoli"
        planned_dirs.append(
            issue_dir
            / _group_folder_name(group_name, group_indexes[group_name])
            / _article_folder_name(
                article_link,
                fallback_index=article_link.order or fallback_index,
            )
        )
    return planned_dirs


def _stored_issue_article_dirs(
    issue: Issue,
    *,
    issue_dir: Path,
    output_dir: Path,
) -> list[Path]:
    manifest = read_manifest(output_dir)
    planned_issue_dirs = _planned_issue_article_dirs(issue, issue_dir=issue_dir)
    planned_dirs = {
        article.url: planned_dir
        for article, planned_dir in zip(issue.articles, planned_issue_dirs, strict=True)
    }
    stored_dirs = _resolved_issue_article_dirs(
        issue,
        output_dir=output_dir,
        planned_dirs=planned_dirs,
    )
    canonical_dirs: list[Path] = []
    for _article, stored_dir, planned_dir in zip(
        issue.articles,
        stored_dirs,
        planned_issue_dirs,
        strict=True,
    ):
        if stored_dir != planned_dir and stored_dir.exists() and not planned_dir.exists():
            planned_dir.parent.mkdir(parents=True, exist_ok=True)
            stored_dir.rename(planned_dir)
            stored_dir = planned_dir
        canonical_dirs.append(stored_dir)
    missing_dirs = [path for path in canonical_dirs if not path.exists()]
    if missing_dirs:
        raise ValueError(
            "Missing article directories for issue "
            f"{issue.issue_code or issue.title}: {missing_dirs[0]}"
        )
    for article, stored_dir in zip(issue.articles, canonical_dirs, strict=True):
        manifest[article.url] = str(stored_dir)
    write_manifest(output_dir, manifest)
    return canonical_dirs


def _issue_article_metadata(issue: Issue, article: Link) -> dict[str, object]:
    return {
        "issue_title": issue.title,
        "issue_code": issue.issue_code,
        "section": article.group or "Articoli",
        "order": article.order,
        "issue_author": article.author,
        "published_date": article.published_date,
    }


def _refresh_downloaded_issue_metadata(
    client: WebClient,
    issue: Issue,
    *,
    output_dir: Path,
) -> tuple[IssueBundlePlan, Path | None]:
    issue_dir = output_dir / _issue_folder_name(issue.title, issue.issue_code)
    article_dirs = _stored_issue_article_dirs(
        issue,
        issue_dir=issue_dir,
        output_dir=output_dir,
    )
    for article_link, article_dir in zip(issue.articles, article_dirs, strict=True):
        with _progress_step("Downloading article metadata"):
            article = client.download_article(article_link.url)
        article = replace(article, issue_title=issue.title)
        article = _with_fallback_author(article, fallback_author=article_link.author)
        with _progress_step(f"Refreshing metadata in {article_dir.name}"):
            write_article_metadata(
                article_dir,
                article,
                metadata=_issue_article_metadata(issue, article_link),
            )
    cover_image_path = _ensure_issue_cover(client, issue, issue_dir=issue_dir, force=False)
    _write_issue_sidecar(
        issue,
        issue_dir=issue_dir,
        article_dirs=article_dirs,
        cover_image_path=cover_image_path,
    )
    return (
        IssueBundlePlan(issue=issue, issue_dir=issue_dir, article_dirs=article_dirs),
        cover_image_path,
    )


def _selected_issue_details_or_error(
    client: WebClient,
    *,
    all_issues: bool,
    issue_selector: str | None,
    output_dir: Path | None = None,
) -> list[Issue]:
    if not all_issues and issue_selector is None:
        raise ValueError("Use --issue YYYY-NN or --all.")
    issues = _sort_catalog_issues(client.discover_issues())
    if all_issues:
        selected = [
            issue_detail
            for issue_link in issues
            if (issue_detail := client.discover_issue(issue_link.url))
            and (
                output_dir is None
                or (
                    output_dir / _issue_folder_name(issue_detail.title, issue_detail.issue_code)
                ).exists()
            )
        ]
        if not selected and output_dir is not None:
            raise ValueError("No downloaded issues selected.")
        return selected
    selected = _selected_catalog_issues(
        client,
        issues,
        all_issues=all_issues,
        issue_selector=issue_selector,
    )
    if not selected:
        raise ValueError("No issues selected.")
    return selected


def _issue_cover_path(issue_dir: Path, issue: Issue) -> Path | None:
    if not issue.cover_image_url:
        return None
    suffix = Path(issue.cover_image_url.split("?", 1)[0]).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    return issue_dir / f"cover{suffix}"


def _ensure_issue_cover(
    client: WebClient,
    issue: Issue,
    *,
    issue_dir: Path,
    force: bool,
) -> Path | None:
    target_path = _issue_cover_path(issue_dir, issue)
    if target_path is None:
        return None
    if target_path.exists() and not force:
        return target_path
    cover_image_url = issue.cover_image_url
    if cover_image_url is None:
        return None
    data = client.download_binary(cover_image_url)
    issue_dir.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(data)
    return target_path


def _issue_summary(issue: Issue) -> str | None:
    summary = issue.summary
    if summary:
        return summary
    issue_code, title, synopsis = _issue_summary_parts(issue.title)
    del issue_code, title
    return synopsis


def _issue_release_date(issue: Issue) -> str | None:
    dates = [
        date
        for article in issue.articles
        if (date := article.published_date or article_date_from_url(article.url)) is not None
    ]
    if not dates:
        return None
    return min(dates)


def _write_issue_sidecar(
    issue: Issue,
    *,
    issue_dir: Path,
    article_dirs: list[Path],
    cover_image_path: Path | None,
) -> Path:
    article_sidecars = [_article_sidecar_metadata(article_dir) for article_dir in article_dirs]
    contributors = _issue_contributors(article_sidecars)
    relative_article_dirs = [
        str(article_dir.relative_to(issue_dir))
        if article_dir.is_relative_to(issue_dir)
        else str(article_dir)
        for article_dir in article_dirs
    ]
    payload: dict[str, object] = {
        "title": issue.title,
        "url": issue.url,
        "issue_code": issue.issue_code,
        "published_date": _issue_release_date(issue),
        "summary": _issue_summary(issue),
        "cover_image_url": issue.cover_image_url,
        "cover_image_path": str(cover_image_path.relative_to(issue_dir))
        if cover_image_path is not None and cover_image_path.is_relative_to(issue_dir)
        else (str(cover_image_path) if cover_image_path is not None else None),
        "publisher": "Rivista Domino",
        "contributors": contributors,
        "article_count": len(issue.articles),
        "articles": [
            {
                "title": article.title,
                "url": article.url,
                "section": article.group or "Articoli",
                "order": article.order,
                "published_date": article.published_date,
                "author": _article_author(article_sidecars, index),
                "article_dir": relative_article_dirs[index]
                if index < len(relative_article_dirs)
                else None,
            }
            for index, article in enumerate(issue.articles)
        ],
        "chapters": [
            {
                "order": article.order,
                "title": article.title,
                "section": article.group or "Articoli",
                "author": _article_author(article_sidecars, index),
                "article_dir": relative_article_dirs[index]
                if index < len(relative_article_dirs)
                else None,
            }
            for index, article in enumerate(issue.articles)
        ],
    }
    return write_issue_metadata(issue_dir, payload)


def _build_issue_audiobook(
    plan: IssueBundlePlan,
    *,
    root_output_dir: Path,
    config: AppConfig,
    cover_image_path: Path | None,
    filename_settings: AudiobookFilenameSettings,
) -> Path:
    audiobook_dir = _audiobooks_dir(root_output_dir, config)
    output_path = _audiobook_output_path(
        audiobook_dir,
        plan.issue,
        settings=filename_settings,
    )
    article_sidecars = [_article_sidecar_metadata(article_dir) for article_dir in plan.article_dirs]
    contributors = _issue_contributors(article_sidecars)
    chapters = [
        AudiobookChapter(
            title=_chapter_label(
                article.title,
                _article_author(article_sidecars, index),
                order=article.order or index + 1,
            ),
            audio_path=_resolve_issue_audio_path(
                article,
                article_dir,
                root_output_dir=root_output_dir,
                audio_format="m4a",
            ),
            contributor=_article_author(article_sidecars, index),
        )
        for index, (article, article_dir) in enumerate(
            zip(plan.issue.articles, plan.article_dirs, strict=True)
        )
    ]
    metadata_title = plan.issue.title
    joined_contributors = ", ".join(contributors) if contributors else None
    metadata: dict[str, str] = {
        "title": metadata_title,
        "album": metadata_title,
        "artist": joined_contributors or "Rivista Domino",
        "album_artist": joined_contributors or "Rivista Domino",
        "publisher": "Rivista Domino",
        "genre": "Magazine",
        "comment": plan.issue.url,
        "grouping": plan.issue.issue_code or "",
    }
    if joined_contributors:
        metadata["composer"] = joined_contributors
        metadata["contributors"] = joined_contributors
    release_date = _issue_release_date(plan.issue)
    if release_date:
        metadata["date"] = release_date
    summary = _issue_summary(plan.issue)
    if summary:
        metadata["description"] = summary
        metadata["synopsis"] = summary
    built_path = build_m4b(
        output_path,
        title=metadata_title,
        chapters=chapters,
        cover_image_path=cover_image_path,
        metadata=metadata,
    )
    _remove_legacy_audiobook_paths(output_path, plan.issue, audiobook_dir=audiobook_dir)
    return built_path


def _article_sidecar_metadata(article_dir: Path) -> dict[str, object]:
    metadata_path = article_dir / "metadata.json"
    if not metadata_path.exists():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items()}


def _article_author(article_sidecars: list[dict[str, object]], index: int) -> str | None:
    if index >= len(article_sidecars):
        return None
    raw_author = article_sidecars[index].get("author")
    if not isinstance(raw_author, str):
        raw_author = article_sidecars[index].get("issue_author")
    if not isinstance(raw_author, str):
        return None
    author = raw_author.strip()
    return author or None


def _issue_contributors(article_sidecars: list[dict[str, object]]) -> list[str]:
    contributors: list[str] = []
    for index in range(len(article_sidecars)):
        author = _article_author(article_sidecars, index)
        if author and author not in contributors:
            contributors.append(author)
    return contributors


def _chapter_label(title: str, author: str | None, *, order: int) -> str:
    prefix = f"{order:02d}. "
    if not author:
        return prefix + title
    return f"{prefix}{title} (di {author})"


def _audiobook_output_path(
    audiobook_dir: Path,
    issue: Issue,
    *,
    settings: AudiobookFilenameSettings,
) -> Path:
    filename = render_audiobook_filename_for_issue(issue, settings=settings)
    return audiobook_dir / f"{filename}.m4b"


def _remove_legacy_audiobook_paths(
    output_path: Path,
    issue: Issue,
    *,
    audiobook_dir: Path,
) -> None:
    legacy_paths = {
        audiobook_dir / f"{_issue_folder_name(issue.title, issue.issue_code)}.m4b",
    }
    for legacy_path in legacy_paths:
        if legacy_path == output_path:
            continue
        legacy_path.unlink(missing_ok=True)


def _resolve_issue_audio_path(
    article: Link,
    article_dir: Path,
    *,
    root_output_dir: Path,
    audio_format: str,
) -> Path:
    expected_path = _audio_output_path(
        article_dir,
        output_dir=root_output_dir,
        audio_format=audio_format,
    )
    migrated_path = _migrate_co_located_audio_file(
        article_dir,
        expected_path=expected_path,
        audio_format=audio_format,
    )
    if migrated_path is not None:
        return migrated_path
    if expected_path.exists():
        return expected_path
    legacy_audio_path = _legacy_audio_output_path(
        article_dir,
        root_output_dir=root_output_dir,
        audio_format=audio_format,
    )
    if legacy_audio_path.exists():
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_audio_path), str(expected_path))
        return expected_path
    order = article.order or 0
    if order <= 0:
        raise AudiobookError(f"Missing chapter audio file: {expected_path}")
    legacy_dir = legacy_audio_path.parent
    if legacy_dir.exists():
        legacy_order_matches = sorted(legacy_dir.glob(f"{order:02d}-*.{audio_format}"))
        if len(legacy_order_matches) == 1:
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy_order_matches[0]), str(expected_path))
            return expected_path
        article_slug = slugify(article.title, fallback="articolo")
        legacy_slug_matches = [path for path in legacy_order_matches if article_slug in path.stem]
        if len(legacy_slug_matches) == 1:
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy_slug_matches[0]), str(expected_path))
            return expected_path
    order_matches = sorted(expected_path.parent.glob(f"{order:02d}-*.{audio_format}"))
    if len(order_matches) == 1:
        return order_matches[0]
    article_slug = slugify(article.title, fallback="articolo")
    slug_matches = [path for path in order_matches if article_slug in path.stem]
    if len(slug_matches) == 1:
        return slug_matches[0]
    raise AudiobookError(f"Missing chapter audio file: {expected_path}")


def article_dir_for_index(output_dir: Path, article: Article, *, index: int) -> Path:
    return output_dir / f"{index:03d}-{slugify(article.title)}"


@contextmanager
def _progress_step(label: str) -> Iterator[None]:
    if not sys.stderr.isatty():
        print(f"→ {label}...", file=sys.stderr, flush=True)
        try:
            yield
        except BaseException:
            print(f"✗ {label}", file=sys.stderr, flush=True)
            raise
        print(f"✓ {label}", file=sys.stderr, flush=True)
        return

    stop = threading.Event()

    def spin() -> None:
        index = 0
        while not stop.is_set():
            sys.stderr.write("\r\033[K" + _render_progress_line(label, index=index))
            sys.stderr.flush()
            index += 1
            time.sleep(0.1)

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    try:
        yield
    except BaseException:
        stop.set()
        thread.join()
        sys.stderr.write(f"\r\033[K✗ {label}\n")
        sys.stderr.flush()
        raise
    stop.set()
    thread.join()
    sys.stderr.write(f"\r\033[K✓ {label}\n")
    sys.stderr.flush()


@contextmanager
def _audio_progress_step(label: str) -> Iterator[Callable[[str, Path | None, int | None], None]]:
    if not sys.stderr.isatty():
        print(f"→ {label}...", file=sys.stderr, flush=True)

        def progress_plain(event: str, path: Path | None, size: int | None) -> None:
            del path
            if event == "waiting_lock":
                print("  queued: waiting for audio engine lock", file=sys.stderr, flush=True)
            elif event == "chunking" and size is not None:
                print(f"  chunks: {size}", file=sys.stderr, flush=True)
            elif event == "retrying" and size is not None:
                print(f"  retry: attempt {size + 1}", file=sys.stderr, flush=True)
            elif event == "converting":
                print("  converting: final audio format", file=sys.stderr, flush=True)

        try:
            yield progress_plain
        except BaseException:
            print(f"✗ {label}", file=sys.stderr, flush=True)
            raise
        print(f"✓ {label}", file=sys.stderr, flush=True)
        return

    stop = threading.Event()
    state: dict[str, object] = {"event": "starting", "size": 0}

    def progress_tty(event: str, path: Path | None, size: int | None) -> None:
        del path
        state["event"] = event
        if size is not None:
            state["size"] = size

    def spin() -> None:
        index = 0
        while not stop.is_set():
            event = str(state["event"])
            raw_size = state["size"]
            size = raw_size if isinstance(raw_size, int) else 0
            detail = _audio_progress_detail(event, size, index=index)
            sys.stderr.write("\r\033[K" + _render_progress_line(label, detail=detail, index=index))
            sys.stderr.flush()
            index += 1
            time.sleep(0.1)

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    try:
        yield progress_tty
    except BaseException:
        stop.set()
        thread.join()
        sys.stderr.write(f"\r\033[K✗ {label}\n\033[K")
        sys.stderr.flush()
        raise
    stop.set()
    thread.join()
    sys.stderr.write(f"\r\033[K✓ {label}\n\033[K")
    sys.stderr.flush()


def _audio_progress_detail(event: str, size: int, *, index: int) -> str:
    if event == "waiting_lock":
        return "queued: waiting for audio engine lock"
    if event == "converting":
        return "convert: final audio format"
    if event == "chunking":
        return f"chunks: preparing {size} audio chunks"
    if event == "retrying":
        return f"retry: audio chunk attempt {size + 1}"
    if event in {"synthesizing", "aiff_growth"}:
        return f"{_byte_growth_bar(size, frame=index)} {_format_bytes(size)} AIFF written"
    return "starting: preparing audio engine"


def _render_progress_line(label: str, *, index: int, detail: str | None = None) -> str:
    prefix = f"{_indeterminate_bar(index)} "
    available = max(8, _terminal_columns() - len(prefix))
    if not detail:
        return prefix + _truncate(label, width=available)
    detail_width = max(12, min(len(detail), available // 2))
    label_width = max(8, available - detail_width - 3)
    rendered_label = _truncate(label, width=label_width)
    rendered_detail = _truncate(detail, width=available - len(rendered_label) - 3)
    return prefix + rendered_label + " · " + rendered_detail


def _terminal_columns() -> int:
    return shutil.get_terminal_size(fallback=(100, 20)).columns


def _byte_growth_bar(size: int, *, frame: int, width: int = 18) -> str:
    if size <= 0:
        return "[" + (" " * width) + "]"
    marker_count = min(width, max(1, size.bit_length() - 20))
    offset = frame % width
    cells = [" "] * width
    for index in range(marker_count):
        cells[(offset + index) % width] = "█"
    return "[" + "".join(cells) + "]"


def _format_bytes(size: int) -> str:
    value = float(max(0, size))
    for suffix in ("B", "KB", "MB", "GB"):
        if value < 1024 or suffix == "GB":
            if suffix == "B":
                return f"{int(value)} {suffix}"
            return f"{value:.1f} {suffix}"
        value /= 1024
    return f"{value:.1f} GB"


def _format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, remaining_seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes:02d}:{remaining_seconds:02d}"


def _indeterminate_bar(frame: int, *, width: int = 12, chunk_width: int = 3) -> str:
    if width < 3:
        width = 3
    chunk_width = min(max(1, chunk_width), width)
    span = width - chunk_width
    if span == 0:
        position = 0
    else:
        cycle = span * 2
        offset = frame % cycle
        position = offset if offset <= span else cycle - offset
    cells = [" "] * width
    for index in range(position, position + chunk_width):
        cells[index] = "█"
    return "[" + "".join(cells) + "]"


def _progress_done(label: str) -> None:
    print(f"✓ {label}", file=sys.stderr, flush=True)


def _download_issue_article(
    *,
    issue_selector: str,
    article_selector: str,
    config: AppConfig,
    root_output_dir: Path,
    create_audio: bool,
    audio_format: str,
    audio_timeout: float,
    audio_chunked: bool = True,
    audio_chunk_chars: int = 2500,
    audio_concurrency: int = 3,
    audio_retries: int = 2,
    audio_stall_timeout: float = 45.0,
    speech_options: SpeechNormalizeOptions | None = None,
    export_formats: tuple[str, ...] | None = None,
    force: bool = False,
) -> int:
    output_dir = _magazine_output_dir(root_output_dir)
    client = WebClient(config)
    with _progress_step(f"Finding issue {issue_selector}"):
        issues = _sort_catalog_issues(client.discover_issues())
        issue_url = _resolve_issue_selector(issues, issue_selector)
        issue = client.discover_issue(issue_url)
    with _progress_step(f"Selecting article {article_selector}"):
        article_link = _resolve_article_selector(issue.articles, article_selector)
    group_indexes = _group_indexes(issue.articles)
    group_name = article_link.group or "Articoli"
    issue_dir = output_dir / _issue_folder_name(issue.title, issue.issue_code)
    target_dir = (
        issue_dir
        / _group_folder_name(group_name, group_indexes[group_name])
        / _article_folder_name(
            article_link,
            fallback_index=article_link.order or 1,
        )
    )
    return _download_articles(
        [article_link.url],
        config,
        output_dir,
        create_audio=create_audio,
        audio_format=audio_format,
        audio_timeout=audio_timeout,
        audio_chunked=audio_chunked,
        audio_chunk_chars=audio_chunk_chars,
        audio_concurrency=audio_concurrency,
        audio_retries=audio_retries,
        audio_stall_timeout=audio_stall_timeout,
        speech_options=speech_options,
        export_formats=export_formats,
        force=force,
        issue_titles={article_link.url: issue.title},
        target_dirs={article_link.url: target_dir},
        metadata_by_url={
            article_link.url: {
                "issue_title": issue.title,
                "issue_code": issue.issue_code,
                "section": group_name,
                "issue_author": article_link.author,
                "order": article_link.order,
                "published_date": article_link.published_date,
            }
        },
    )


def _download_issue_articles(
    *,
    issue_selector: str,
    config: AppConfig,
    root_output_dir: Path,
    create_audio: bool,
    create_audiobook: bool,
    audio_format: str,
    audio_timeout: float,
    audio_chunked: bool = True,
    audio_chunk_chars: int = 2500,
    audio_concurrency: int = 3,
    audio_retries: int = 2,
    audio_stall_timeout: float = 45.0,
    speech_options: SpeechNormalizeOptions | None = None,
    export_formats: tuple[str, ...] | None = None,
    audiobook_filename_settings: AudiobookFilenameSettings | None = None,
    force: bool = False,
) -> int:
    output_dir = _magazine_output_dir(root_output_dir)
    client = WebClient(config)
    with _progress_step(f"Finding issue {issue_selector}"):
        issues = _sort_catalog_issues(client.discover_issues())
        issue_url = _resolve_issue_selector(issues, issue_selector)
        issue = client.discover_issue(issue_url)

    group_indexes = _group_indexes(issue.articles)
    issue_dir = output_dir / _issue_folder_name(issue.title, issue.issue_code)
    article_urls: list[str] = []
    issue_titles: dict[str, str] = {}
    target_dirs: dict[str, Path] = {}
    metadata_by_url: dict[str, dict[str, object]] = {}

    for fallback_index, article_link in enumerate(issue.articles, start=1):
        group_name = article_link.group or "Articoli"
        target_dir = (
            issue_dir
            / _group_folder_name(group_name, group_indexes[group_name])
            / _article_folder_name(
                article_link,
                fallback_index=article_link.order or fallback_index,
            )
        )
        article_urls.append(article_link.url)
        issue_titles[article_link.url] = issue.title
        target_dirs[article_link.url] = target_dir
        metadata_by_url[article_link.url] = {
            "issue_title": issue.title,
            "issue_code": issue.issue_code,
            "section": group_name,
            "issue_author": article_link.author,
            "order": article_link.order,
            "published_date": article_link.published_date,
        }

    result = _download_articles(
        article_urls,
        config,
        output_dir,
        create_audio=create_audio,
        audio_format=audio_format,
        audio_timeout=audio_timeout,
        audio_chunked=audio_chunked,
        audio_chunk_chars=audio_chunk_chars,
        audio_concurrency=audio_concurrency,
        audio_retries=audio_retries,
        audio_stall_timeout=audio_stall_timeout,
        speech_options=speech_options,
        export_formats=export_formats,
        force=force,
        issue_titles=issue_titles,
        target_dirs=target_dirs,
        metadata_by_url=metadata_by_url,
    )
    resolved_article_dirs = _resolved_issue_article_dirs(
        issue,
        output_dir=output_dir,
        planned_dirs=target_dirs,
    )
    cover_image_path = _ensure_issue_cover(client, issue, issue_dir=issue_dir, force=force)
    _write_issue_sidecar(
        issue,
        issue_dir=issue_dir,
        article_dirs=resolved_article_dirs,
        cover_image_path=cover_image_path,
    )
    if result != 0 or not create_audiobook or not issue.articles:
        return result
    with _progress_step(f"Packaging audiobook {issue_dir.name}.m4b"):
        _build_issue_audiobook(
            IssueBundlePlan(
                issue=issue,
                issue_dir=issue_dir,
                article_dirs=resolved_article_dirs,
            ),
            root_output_dir=root_output_dir,
            config=config,
            cover_image_path=cover_image_path,
            filename_settings=(
                audiobook_filename_settings or _configured_audiobook_filename_settings(config)
            ),
        )
    return result


def _resolve_article_selector(articles: list[Link], selector: str) -> Link:
    if not selector.isdecimal():
        raise ValueError("Article selector must be an article order such as 1 or 01.")
    order = int(selector)
    for article in articles:
        if article.order == order:
            return article
    raise ValueError(f"Article {selector} was not found in the selected issue.")


def _download_new_articles(
    article_links: list[Link],
    *,
    config: AppConfig,
    output_dir: Path,
    create_audio: bool,
    audio_format: str,
    audio_timeout: float,
    audio_chunked: bool = True,
    audio_chunk_chars: int = 2500,
    audio_concurrency: int = 3,
    audio_retries: int = 2,
    audio_stall_timeout: float = 45.0,
    speech_options: SpeechNormalizeOptions | None = None,
    export_formats: tuple[str, ...],
    max_articles: int | None,
    force: bool = False,
) -> int:
    client = WebClient(config)
    manifest = read_manifest(output_dir)
    next_index = len(manifest) + 1
    downloaded_dirs: list[Path] = []
    audio_dirs: list[Path] = []
    selected_count = 0

    _print_feed_sync_header(output_dir)
    for article_link in article_links:
        if max_articles is not None and selected_count >= max_articles:
            break
        existing_dir = _existing_article_dir(
            manifest,
            article_link.url,
            output_dir=output_dir,
        )
        if existing_dir is not None and not force:
            if create_audio:
                audio_dirs.append(existing_dir)
                selected_count += 1
                _print_download_result(
                    article_link.title,
                    export_status="reused",
                    audio_status="pending",
                    elapsed="00:00",
                    target_dir=existing_dir,
                    verbose=config.verbose,
                )
            continue
        article_started_at = time.monotonic()
        article = client.download_article(article_link.url)
        article = replace(article, issue_title="La settimana di Domino")
        if existing_dir is not None:
            target_dir = existing_dir
            write_article_export(
                target_dir,
                article,
                export_formats=export_formats,
                metadata={
                    "feed": "La settimana di Domino",
                    "published_date": article_link.published_date,
                },
            )
        else:
            target_dir = write_article_named(
                output_dir,
                article,
                name=_feed_article_folder_name(article_link),
                export_formats=export_formats,
                metadata={
                    "feed": "La settimana di Domino",
                    "published_date": article_link.published_date,
                },
            )
        manifest[article.url] = str(target_dir)
        downloaded_dirs.append(target_dir)
        selected_count += 1
        if create_audio:
            audio_dirs.append(target_dir)
        _print_download_result(
            article.title,
            export_status="written",
            audio_status="pending" if create_audio else "off",
            elapsed=_format_duration(time.monotonic() - article_started_at),
            target_dir=target_dir,
            verbose=config.verbose,
        )
        next_index += 1

    write_manifest(output_dir, manifest)
    audio_result = 0
    if create_audio:
        audio_result = _speak_paths(
            audio_dirs,
            output_dir=config.output_dir,
            voice=config.siri_voice,
            audio_format=audio_format,
            timeout=audio_timeout,
            chunked=audio_chunked,
            chunk_chars=audio_chunk_chars,
            concurrency=audio_concurrency,
            retries=audio_retries,
            stall_timeout=audio_stall_timeout,
            speech_options=speech_options,
            force=force,
        )
    print(f"new_articles: {len(downloaded_dirs)}")
    return audio_result


def _print_feed_sync_header(output_dir: Path) -> None:
    _print_sync_header("La settimana di Domino", output_dir=output_dir)


def _print_sync_header(title: str, *, output_dir: Path) -> None:
    print(title)
    print("=" * len(title))
    print(f"folder: {output_dir}")
    print()
    print(_style_download_header())


def _handle_sync(
    config: AppConfig,
    *,
    create_audio: bool,
    create_audiobook: bool,
    audiobook_filename_settings: AudiobookFilenameSettings | None = None,
    audio_format: str,
    audio_timeout: float,
    audio_chunked: bool = True,
    audio_chunk_chars: int = 2500,
    audio_concurrency: int = 3,
    audio_retries: int = 2,
    audio_stall_timeout: float = 45.0,
    speech_options: SpeechNormalizeOptions | None = None,
    export_formats: tuple[str, ...],
    max_articles: int | None,
    force: bool = False,
) -> int:
    root_output_dir = config.output_dir
    output_dir = _magazine_output_dir(root_output_dir)
    client = WebClient(config)
    issues = client.discover_issues()
    manifest = read_manifest(output_dir)
    downloaded_dirs: list[Path] = []
    audio_dirs: list[Path] = []
    issue_bundle_plans: list[IssueBundlePlan] = []
    selected_count = 0

    _print_sync_header(config.magazine_title, output_dir=output_dir)
    for issue in issues:
        issue_detail = client.discover_issue(issue.url)
        issue_dir = output_dir / _issue_folder_name(issue_detail.title, issue_detail.issue_code)
        issue_article_dirs: list[Path] = []
        print(f"issue: {issue_detail.title}")
        group_indexes = _group_indexes(issue_detail.articles)
        for article_link in issue_detail.articles:
            if max_articles is not None and selected_count >= max_articles:
                break
            article_started_at = time.monotonic()
            existing_dir = _existing_article_dir(
                manifest,
                article_link.url,
                output_dir=output_dir,
            )
            if existing_dir is not None and not force:
                if create_audio:
                    audio_dirs.append(existing_dir)
                    selected_count += 1
                    _print_download_result(
                        article_link.title,
                        export_status="reused",
                        audio_status="pending",
                        elapsed="00:00",
                        target_dir=existing_dir,
                        verbose=config.verbose,
                    )
                issue_article_dirs.append(existing_dir)
                continue
            article = client.download_article(article_link.url)
            article = replace(article, issue_title=issue_detail.title)
            article = _with_fallback_author(article, fallback_author=article_link.author)
            group_name = article_link.group or "Articoli"
            group_dir = issue_dir / _group_folder_name(group_name, group_indexes[group_name])
            metadata: dict[str, object] = {
                "issue_title": issue_detail.title,
                "issue_code": issue_detail.issue_code,
                "section": group_name,
                "order": article_link.order,
                "published_date": article_link.published_date,
            }
            if existing_dir is not None:
                target_dir = existing_dir
                write_article_export(
                    target_dir,
                    article,
                    export_formats=export_formats,
                    metadata=metadata,
                )
            else:
                target_dir = write_article_named(
                    group_dir,
                    article,
                    name=_article_folder_name(
                        article_link,
                        fallback_index=article_link.order or len(downloaded_dirs) + 1,
                    ),
                    export_formats=export_formats,
                    metadata=metadata,
                )
            manifest[article.url] = str(target_dir)
            downloaded_dirs.append(target_dir)
            issue_article_dirs.append(target_dir)
            selected_count += 1
            if create_audio:
                audio_dirs.append(target_dir)
            _print_download_result(
                article.title,
                export_status="written",
                audio_status="pending" if create_audio else "off",
                elapsed=_format_duration(time.monotonic() - article_started_at),
                target_dir=target_dir,
                verbose=config.verbose,
            )
        if max_articles is not None and selected_count >= max_articles:
            cover_image_path = _ensure_issue_cover(
                client, issue_detail, issue_dir=issue_dir, force=force
            )
            _write_issue_sidecar(
                issue_detail,
                issue_dir=issue_dir,
                article_dirs=issue_article_dirs,
                cover_image_path=cover_image_path,
            )
            if create_audiobook and len(issue_article_dirs) == len(issue_detail.articles):
                issue_bundle_plans.append(
                    IssueBundlePlan(
                        issue=issue_detail,
                        issue_dir=issue_dir,
                        article_dirs=issue_article_dirs,
                    )
                )
            break
        cover_image_path = _ensure_issue_cover(
            client, issue_detail, issue_dir=issue_dir, force=force
        )
        _write_issue_sidecar(
            issue_detail,
            issue_dir=issue_dir,
            article_dirs=issue_article_dirs,
            cover_image_path=cover_image_path,
        )
        if (
            create_audiobook
            and issue_detail.articles
            and len(issue_article_dirs) == len(issue_detail.articles)
        ):
            issue_bundle_plans.append(
                IssueBundlePlan(
                    issue=issue_detail,
                    issue_dir=issue_dir,
                    article_dirs=issue_article_dirs,
                )
            )

    write_manifest(output_dir, manifest)
    audio_result = 0
    if create_audio:
        audio_result = _speak_paths(
            audio_dirs,
            output_dir=output_dir,
            voice=config.siri_voice,
            audio_format=audio_format,
            timeout=audio_timeout,
            chunked=audio_chunked,
            chunk_chars=audio_chunk_chars,
            concurrency=audio_concurrency,
            retries=audio_retries,
            stall_timeout=audio_stall_timeout,
            speech_options=speech_options,
            force=force,
        )
    if create_audiobook and audio_result == 0:
        for plan in issue_bundle_plans:
            cover_image_path = _issue_cover_path(plan.issue_dir, plan.issue)
            with _progress_step(f"Packaging audiobook {plan.issue_dir.name}.m4b"):
                _build_issue_audiobook(
                    plan,
                    root_output_dir=root_output_dir,
                    config=config,
                    filename_settings=(
                        audiobook_filename_settings
                        or _configured_audiobook_filename_settings(config)
                    ),
                    cover_image_path=cover_image_path
                    if cover_image_path and cover_image_path.exists()
                    else None,
                )
    print(f"new_articles: {len(downloaded_dirs)}")
    return audio_result


def _issue_folder_name(title: str, issue_code: str | None) -> str:
    prefix = issue_code or "unknown-issue"
    return f"{prefix}-{slugify(title, fallback='numero')}"


def _group_folder_name(title: str, index: int) -> str:
    return f"{index:02d}-{slugify(title, fallback='parte')}"


def _group_indexes(links: list[Link]) -> dict[str, int]:
    indexes: dict[str, int] = {}
    for link in links:
        group_name = link.group or "Articoli"
        if group_name not in indexes:
            indexes[group_name] = len(indexes) + 1
    return indexes


def _article_folder_name(link: Link, *, fallback_index: int) -> str:
    order = link.order or fallback_index
    return f"{order:02d}-{slugify(link.title, fallback='articolo')}"


def _feed_article_folder_name(link: Link) -> str:
    date = link.published_date or article_date_from_url(link.url) or "unknown-date"
    return f"{date}-{slugify(link.title, fallback='articolo')}"


def _handle_sync_feed(
    config: AppConfig,
    *,
    create_audio: bool,
    audio_format: str,
    audio_timeout: float,
    audio_chunked: bool = True,
    audio_chunk_chars: int = 2500,
    audio_concurrency: int = 3,
    audio_retries: int = 2,
    audio_stall_timeout: float = 45.0,
    speech_options: SpeechNormalizeOptions | None = None,
    export_formats: tuple[str, ...],
    max_articles: int | None,
    pages: int,
    force: bool = False,
) -> int:
    links = discover_feed_articles(config, max_pages=pages)
    return _download_new_articles(
        links,
        config=config,
        output_dir=_feed_output_dir(config.output_dir, config),
        create_audio=create_audio,
        audio_format=audio_format,
        audio_timeout=audio_timeout,
        audio_chunked=audio_chunked,
        audio_chunk_chars=audio_chunk_chars,
        audio_concurrency=audio_concurrency,
        audio_retries=audio_retries,
        audio_stall_timeout=audio_stall_timeout,
        speech_options=speech_options,
        export_formats=export_formats,
        max_articles=max_articles,
        force=force,
    )


def _handle_refresh_issue_metadata(
    config: AppConfig,
    *,
    root_output_dir: Path,
    all_issues: bool,
    issue_selector: str | None,
) -> int:
    output_dir = _magazine_output_dir(root_output_dir)
    client = WebClient(config)
    issues = _selected_issue_details_or_error(
        client,
        all_issues=all_issues,
        issue_selector=issue_selector,
        output_dir=output_dir,
    )
    for issue in issues:
        print(f"issue: {issue.title}")
        _refresh_downloaded_issue_metadata(client, issue, output_dir=output_dir)
    return 0


def _handle_repackage_audiobook(
    config: AppConfig,
    *,
    root_output_dir: Path,
    all_issues: bool,
    issue_selector: str | None,
    filename_settings: AudiobookFilenameSettings,
) -> int:
    output_dir = _magazine_output_dir(root_output_dir)
    client = WebClient(config)
    issues = _selected_issue_details_or_error(
        client,
        all_issues=all_issues,
        issue_selector=issue_selector,
        output_dir=output_dir,
    )
    for issue in issues:
        print(f"issue: {issue.title}")
        plan, cover_image_path = _refresh_downloaded_issue_metadata(
            client,
            issue,
            output_dir=output_dir,
        )
        with _progress_step(f"Packaging audiobook {plan.issue_dir.name}.m4b"):
            _build_issue_audiobook(
                plan,
                root_output_dir=root_output_dir,
                config=config,
                cover_image_path=cover_image_path,
                filename_settings=filename_settings,
            )
    return 0


def _iter_audiobook_files(
    paths: list[Path],
    *,
    library_dir: Path | None,
    output_dir: Path,
) -> list[Path]:
    candidates: list[Path]
    if paths:
        candidates = paths
    elif library_dir is not None:
        candidates = [library_dir]
    else:
        candidates = [output_dir]

    files: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir():
            files.extend(sorted(candidate.glob("**/*.m4b")))
        elif candidate.suffix.lower() == ".m4b" and candidate.exists():
            files.append(candidate)
    return files


def _handle_rename_audiobooks(
    config: AppConfig,
    *,
    paths: list[Path],
    library_dir: Path | None,
    output_dir: Path,
    filename_settings: AudiobookFilenameSettings,
    dry_run: bool,
) -> int:
    audiobook_files = _iter_audiobook_files(paths, library_dir=library_dir, output_dir=output_dir)
    if not audiobook_files:
        raise ValueError("No .m4b files found to rename.")

    renamed = 0
    for path in audiobook_files:
        tags = read_audiobook_tags(path)
        title = tags.get("title")
        date = tags.get("date")
        if not title or not date:
            print(f"skip: {path} (missing title/date tags)")
            continue
        filename = render_audiobook_filename_from_tags(
            title=title,
            date=date,
            issue_code=tags.get("grouping"),
            settings=filename_settings,
        )
        target_path = path.with_name(f"{filename}.m4b")
        if target_path == path:
            print(f"keep: {path.name}")
            continue
        if target_path.exists():
            if not _is_case_only_rename(path, target_path):
                raise ValueError(f"Target audiobook filename already exists: {target_path}")
        print(f"rename: {path.name} -> {target_path.name}")
        if not dry_run:
            _rename_audiobook_path(path, target_path)
        renamed += 1
    print(f"renamed: {renamed}")
    return 0


def _is_case_only_rename(path: Path, target_path: Path) -> bool:
    return path.parent == target_path.parent and path.name.casefold() == target_path.name.casefold()


def _rename_audiobook_path(path: Path, target_path: Path) -> None:
    if not _is_case_only_rename(path, target_path):
        path.rename(target_path)
        return
    temporary_path = path.with_name(f".{path.name}.renaming")
    suffix = 0
    while temporary_path.exists():
        suffix += 1
        temporary_path = path.with_name(f".{path.name}.renaming-{suffix}")
    path.rename(temporary_path)
    temporary_path.rename(target_path)


def _resolve_text_paths(output_dir: Path, paths: list[Path]) -> list[Path]:
    if paths:
        candidates = paths
    else:
        candidates = sorted(output_dir.glob("**/*.txt"))

    text_paths: list[Path] = []
    for path in candidates:
        if path.is_dir():
            text_paths.append(article_text_path(path))
        else:
            text_paths.append(path)
    return text_paths


def _speak_paths(
    paths: list[Path],
    *,
    output_dir: Path,
    voice: str | None,
    audio_format: str,
    timeout: float,
    chunked: bool = True,
    chunk_chars: int = 2500,
    concurrency: int = 3,
    retries: int = 2,
    stall_timeout: float = 45.0,
    speech_options: SpeechNormalizeOptions | None = None,
    force: bool = False,
) -> int:
    normalized_format = normalize_audio_format(audio_format)
    failures: list[AudioFailure] = []
    print(_style_download_header())
    for raw_path in paths:
        started_at = time.monotonic()
        text_path = article_text_path(raw_path) if raw_path.is_dir() else raw_path
        article_label = text_path.stem
        try:
            status, output_path = _ensure_audio(
                raw_path,
                output_dir=output_dir,
                voice=voice,
                audio_format=normalized_format,
                timeout=timeout,
                force=force,
                chunked=chunked,
                chunk_chars=chunk_chars,
                concurrency=concurrency,
                retries=retries,
                stall_timeout=stall_timeout,
                speech_options=speech_options,
            )
        except AudioError as exc:
            failures.append(AudioFailure(label=raw_path.name, target_dir=raw_path, error=str(exc)))
            continue
        _print_download_result(
            article_label,
            export_status="off",
            audio_status=status,
            elapsed=_format_duration(time.monotonic() - started_at),
            target_dir=output_path,
            verbose=False,
        )
    if failures:
        _print_audio_failures(failures)
        return 1
    return 0


def _handle_speech_normalize(
    text_paths: list[Path],
    *,
    speech_options: SpeechNormalizeOptions,
) -> int:
    for text_path in text_paths:
        with _progress_step(f"Preparing speech text {text_path.name}"):
            result = normalize_speech_text(text_path, _speech_settings(speech_options))
        print(f"speech: {result.path}")
        if result.diff_text:
            print(result.diff_text, end="")
    return 0


def _ensure_audio(
    raw_path: Path,
    *,
    output_dir: Path,
    voice: str | None,
    audio_format: str,
    timeout: float,
    chunked: bool = True,
    chunk_chars: int = 2500,
    concurrency: int = 3,
    retries: int = 2,
    stall_timeout: float = 45.0,
    speech_options: SpeechNormalizeOptions | None = None,
    force: bool = False,
) -> tuple[str, Path]:
    normalized_format = normalize_audio_format(audio_format)
    text_path = article_text_path(raw_path) if raw_path.is_dir() else raw_path
    if not text_path.exists():
        raise AudioError(f"Text file not found: {text_path}")
    output_path = _audio_output_path(
        text_path.parent,
        output_dir=output_dir,
        audio_format=normalized_format,
    )
    migrated_path = _migrate_co_located_audio_file(
        text_path.parent,
        expected_path=output_path,
        audio_format=normalized_format,
    )
    if migrated_path is not None and not force:
        return "reused", migrated_path
    legacy_output_path = _legacy_audio_output_path(
        text_path.parent,
        root_output_dir=output_dir,
        audio_format=normalized_format,
    )
    if not output_path.exists() and legacy_output_path.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_output_path), str(output_path))
    if output_path.exists() and not force:
        return "reused", output_path
    speech_source_path = text_path
    if speech_options is not None and speech_options.enabled:
        with _progress_step(f"Preparing speech text {text_path.name}"):
            speech_source_path = ensure_speech_text(
                text_path,
                options=speech_options,
            )
    with _audio_progress_step(f"Generating audio {output_path.name}") as progress:
        synthesize_audio(
            speech_source_path,
            output_path,
            voice=voice,
            audio_format=normalized_format,
            timeout=timeout,
            progress=progress,
            chunked=chunked,
            chunk_chars=chunk_chars,
            concurrency=concurrency,
            retries=retries,
            stall_timeout=stall_timeout,
        )
    return "generated", output_path


def _audio_output_path(article_dir: Path, *, output_dir: Path, audio_format: str) -> Path:
    del output_dir
    article_dir.mkdir(parents=True, exist_ok=True)
    return article_dir / f"{article_basename(article_dir)}.{audio_format}"


def _migrate_co_located_audio_file(
    article_dir: Path,
    *,
    expected_path: Path,
    audio_format: str,
) -> Path | None:
    if expected_path.exists():
        return expected_path
    legacy_article_path = article_dir / f"article.{audio_format}"
    if legacy_article_path.exists():
        shutil.move(str(legacy_article_path), str(expected_path))
        return expected_path
    candidates = [path for path in article_dir.glob(f"*.{audio_format}") if path.is_file()]
    if len(candidates) == 1:
        shutil.move(str(candidates[0]), str(expected_path))
        return expected_path
    return None


def _legacy_audio_output_path(
    article_dir: Path,
    *,
    root_output_dir: Path,
    audio_format: str,
) -> Path:
    basename = article_basename(article_dir)
    magazine_dir = _magazine_output_dir(root_output_dir)
    try:
        relative = article_dir.relative_to(magazine_dir)
    except ValueError:
        legacy_dir = root_output_dir / "audio"
    else:
        issue_name = relative.parts[0] if relative.parts else ""
        legacy_dir = root_output_dir / "audio" / issue_name
    return legacy_dir / f"{basename}.{audio_format}"


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or args_list == ["--help"] or args_list == ["-h"]:
        print(format_main_help())
        return 0

    if args_list == ["--version"]:
        print(__version__)
        return 0

    unknown_command = _unknown_command(args_list)
    if unknown_command is not None:
        _print_unknown_command(unknown_command)
        return 2

    parser = build_parser()
    args = parser.parse_args(args_list)
    config = load_config(args.config).with_cli_overrides(verbose=args.verbose)

    if args.command == "info":
        as_json = bool(getattr(args, "json", False))
        return _handle_info(config=config, config_path=args.config, as_json=as_json)

    try:
        if args.command == "login":
            return _handle_login(config, use_browser=bool(args.browser))
        if args.command == "logout":
            return _handle_logout(config)
        if args.command == "issues":
            return _print_links(discover_issues(config), as_json=bool(args.json))
        if args.command == "articles":
            return _print_links(
                discover_articles(str(args.issue_url), config),
                as_json=bool(args.json),
            )
        if args.command in {"feed", "weekly"}:
            return _print_links(
                discover_feed_articles(config, max_pages=int(args.pages)),
                as_json=bool(args.json),
            )
        if args.command == "catalog":
            return _handle_catalog(
                config,
                all_issues=bool(args.all),
                issue_selector=args.issue,
                include_feed=bool(args.feed),
                feed_pages=int(args.pages),
                as_json=bool(args.json),
            )
        if args.command == "download":
            root_output_dir = args.output_dir or config.output_dir
            config = replace(config, output_dir=root_output_dir)
            if args.all:
                if not args.issue:
                    raise ValueError("Use --all with --issue.")
                if args.article:
                    raise ValueError("Use either --article or --all with --issue, not both.")
                if args.article_urls:
                    raise ValueError("Do not pass article URLs with --issue/--all.")
            if (args.issue or args.article) and not args.all:
                if not args.issue or not args.article:
                    raise ValueError("Use --issue and --article together.")
                if args.article_urls:
                    raise ValueError("Do not pass article URLs with --issue/--article.")
            _ensure_storage_layout(root_output_dir, config)
            output_dir = _magazine_output_dir(root_output_dir)
            audio_options = _audio_options(args, config)
            create_audiobook = _audiobook_requested(args, config)
            audiobook_filename_settings = _audiobook_filename_settings(args, config)
            if create_audiobook:
                if not bool(getattr(args, "no_audio", False)):
                    audio_options = replace(audio_options, create=True)
                if audio_options.audio_format != "m4a":
                    raise ValueError("Issue audiobook packaging requires --audio-format m4a.")
            speech_options = _speech_normalize_options(args, config)
            export_formats = _export_format_options(args, config)
            if args.all:
                return _download_issue_articles(
                    issue_selector=str(args.issue),
                    config=config,
                    root_output_dir=root_output_dir,
                    create_audio=audio_options.create,
                    create_audiobook=create_audiobook,
                    audio_format=audio_options.audio_format,
                    audio_timeout=audio_options.timeout,
                    audio_chunked=audio_options.chunked,
                    audio_chunk_chars=audio_options.chunk_chars,
                    audio_concurrency=audio_options.concurrency,
                    audio_retries=audio_options.retries,
                    audio_stall_timeout=audio_options.stall_timeout,
                    speech_options=speech_options,
                    export_formats=export_formats,
                    audiobook_filename_settings=audiobook_filename_settings,
                    force=bool(args.force),
                )
            if args.issue or args.article:
                return _download_issue_article(
                    issue_selector=str(args.issue),
                    article_selector=str(args.article),
                    config=config,
                    root_output_dir=root_output_dir,
                    create_audio=audio_options.create,
                    audio_format=audio_options.audio_format,
                    audio_timeout=audio_options.timeout,
                    audio_chunked=audio_options.chunked,
                    audio_chunk_chars=audio_options.chunk_chars,
                    audio_concurrency=audio_options.concurrency,
                    audio_retries=audio_options.retries,
                    audio_stall_timeout=audio_options.stall_timeout,
                    speech_options=speech_options,
                    export_formats=export_formats,
                    force=bool(args.force),
                )
            if not args.article_urls:
                raise ValueError("Pass one or more article URLs, or use --issue/--article.")
            return _download_articles(
                list(args.article_urls),
                config,
                output_dir,
                create_audio=audio_options.create,
                audio_format=audio_options.audio_format,
                audio_timeout=audio_options.timeout,
                audio_chunked=audio_options.chunked,
                audio_chunk_chars=audio_options.chunk_chars,
                audio_concurrency=audio_options.concurrency,
                audio_retries=audio_options.retries,
                audio_stall_timeout=audio_options.stall_timeout,
                speech_options=speech_options,
                export_formats=export_formats,
                force=bool(args.force),
            )
        if args.command in {"sync-magazine", "sync"}:
            _ensure_storage_layout(config.output_dir, config)
            audio_options = _audio_options(args, config)
            create_audiobook = _audiobook_requested(args, config)
            audiobook_filename_settings = _audiobook_filename_settings(args, config)
            if create_audiobook:
                if not bool(getattr(args, "no_audio", False)):
                    audio_options = replace(audio_options, create=True)
                if audio_options.audio_format != "m4a":
                    raise ValueError("Issue audiobook packaging requires --audio-format m4a.")
            speech_options = _speech_normalize_options(args, config)
            export_formats = _export_format_options(args, config)
            return _handle_sync(
                config,
                create_audio=audio_options.create,
                create_audiobook=create_audiobook,
                audiobook_filename_settings=audiobook_filename_settings,
                audio_format=audio_options.audio_format,
                audio_timeout=audio_options.timeout,
                audio_chunked=audio_options.chunked,
                audio_chunk_chars=audio_options.chunk_chars,
                audio_concurrency=audio_options.concurrency,
                audio_retries=audio_options.retries,
                audio_stall_timeout=audio_options.stall_timeout,
                speech_options=speech_options,
                export_formats=export_formats,
                max_articles=args.max_articles,
                force=bool(args.force),
            )
        if args.command in {"sync-feed", "sync-weekly"}:
            _ensure_storage_layout(config.output_dir, config)
            audio_options = _audio_options(args, config)
            speech_options = _speech_normalize_options(args, config)
            export_formats = _export_format_options(args, config)
            return _handle_sync_feed(
                config,
                create_audio=audio_options.create,
                audio_format=audio_options.audio_format,
                audio_timeout=audio_options.timeout,
                audio_chunked=audio_options.chunked,
                audio_chunk_chars=audio_options.chunk_chars,
                audio_concurrency=audio_options.concurrency,
                audio_retries=audio_options.retries,
                audio_stall_timeout=audio_options.stall_timeout,
                speech_options=speech_options,
                export_formats=export_formats,
                max_articles=args.max_articles,
                pages=int(args.pages),
                force=bool(args.force),
            )
        if args.command == "refresh-issue-metadata":
            root_output_dir = args.output_dir or config.output_dir
            config = replace(config, output_dir=root_output_dir)
            _ensure_storage_layout(root_output_dir, config)
            return _handle_refresh_issue_metadata(
                config,
                root_output_dir=root_output_dir,
                all_issues=bool(args.all),
                issue_selector=args.issue,
            )
        if args.command == "repackage-audiobook":
            root_output_dir = args.output_dir or config.output_dir
            config = replace(config, output_dir=root_output_dir)
            _ensure_storage_layout(root_output_dir, config)
            return _handle_repackage_audiobook(
                config,
                root_output_dir=root_output_dir,
                all_issues=bool(args.all),
                issue_selector=args.issue,
                filename_settings=_audiobook_filename_settings(args, config),
            )
        if args.command == "rename-audiobooks":
            _ensure_storage_layout(config.output_dir, config)
            return _handle_rename_audiobooks(
                config,
                paths=list(args.paths),
                library_dir=args.library_dir,
                output_dir=config.audiobooks_dir,
                filename_settings=_audiobook_filename_settings(args, config),
                dry_run=bool(args.dry_run),
            )
        if args.command == "speak":
            voice = args.voice if args.voice is not None else config.siri_voice
            audio_options = _audio_options(args, replace(config, audio_auto=True))
            speech_options = _speech_normalize_options(args, config)
            return _speak_paths(
                _resolve_text_paths(config.output_dir, list(args.paths)),
                output_dir=config.output_dir,
                voice=voice,
                audio_format=audio_options.audio_format,
                timeout=audio_options.timeout,
                chunked=audio_options.chunked,
                chunk_chars=audio_options.chunk_chars,
                concurrency=audio_options.concurrency,
                retries=audio_options.retries,
                stall_timeout=audio_options.stall_timeout,
                speech_options=speech_options,
            )
        if args.command == "speech-normalize":
            speech_options = _speech_normalize_options(args, config, diff=bool(args.diff))
            if not speech_options.enabled:
                speech_options = replace(speech_options, enabled=True)
            return _handle_speech_normalize(
                _resolve_text_paths(config.output_dir, list(args.paths)),
                speech_options=speech_options,
            )
        if args.command == "voices":
            return _handle_voices(all_voices=bool(args.all))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except (
        AudioError,
        BrowserAuthError,
        FetchError,
        OSError,
        SpeechNormalizeError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(format_main_help())
    return 0


def _unknown_command(args_list: list[str]) -> str | None:
    index = 0
    while index < len(args_list):
        arg = args_list[index]
        if arg in {"--config"}:
            index += 2
            continue
        if arg.startswith("--config="):
            index += 1
            continue
        if arg in {"--verbose"}:
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        if arg not in COMMAND_NAMES:
            return arg
        return None
    return None


def _print_unknown_command(command: str) -> None:
    print(f"error: Unknown command: {command}", file=sys.stderr)
    matches = difflib.get_close_matches(command, COMMAND_NAMES, n=1, cutoff=0.6)
    if matches:
        print(f"Did you mean: {matches[0]}?", file=sys.stderr)
    print("Run `get-my-domino --help` to see available commands.", file=sys.stderr)
