"""Configuration support for get-my-domino."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import __version__
from .audio import normalize_audio_format
from .audiobook_naming import (
    DEFAULT_AUDIOBOOK_FILENAME_FORMAT,
    DEFAULT_AUDIOBOOK_FILENAME_SEPARATOR,
    DEFAULT_AUDIOBOOK_MAGAZINE_TITLE,
    validate_audiobook_format,
    validate_audiobook_separator,
)

SUPPORTED_EXPORT_FORMATS = ("html", "txt", "rtf")

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "get-my-domino" / "config.toml"
DEFAULT_SESSION_PATH = Path.home() / ".config" / "get-my-domino" / "session.json"
DEFAULT_SPEECH_NORMALIZE_PROMPT_PATH = (
    Path.home() / ".config" / "get-my-domino" / "speech-normalize-codex.txt"
)
DEFAULT_OUTPUT_PARENT_DIR = Path.home() / "Documents"
DEFAULT_LIBRARY_FOLDER_NAME = "library"
DEFAULT_MAGAZINE_FOLDER_NAME = "rivista"
DEFAULT_USER_AGENT = f"get-my-domino/{__version__}"


@dataclass(frozen=True)
class AppConfig:
    verbose: bool = False
    base_url: str = "https://www.rivistadomino.it/"
    magazine_index_url: str = "https://www.rivistadomino.it/mio-account/my_domino/"
    auth_login_url: str = "https://www.rivistadomino.it/mio-account/"
    auth_username: str = ""
    auth_password: str = ""
    auth_username_field: str = "username"
    auth_password_field: str = "password"
    auth_submit_field: str = "login"
    auth_submit_value: str = "Accedi"
    auth_session_path: Path = DEFAULT_SESSION_PATH
    auth_browser_timeout: float = 300.0
    output_parent_dir: Path = DEFAULT_OUTPUT_PARENT_DIR
    collection_dir_name: str = "domino"
    output_dir: Path = DEFAULT_OUTPUT_PARENT_DIR / "domino"
    audiobook_output_dir: Path | None = None
    feed_index_url: str = "https://www.rivistadomino.it/blog/category/la-settimana-di-domino/"
    feed_folder_name: str = "la-settimana-di-domino"
    request_timeout: float = 30.0
    user_agent: str = DEFAULT_USER_AGENT
    issue_link_patterns: tuple[str, ...] = ("?sfoglia=1",)
    article_link_patterns: tuple[str, ...] = ("/blog/20",)
    feed_article_link_patterns: tuple[str, ...] = ("/blog/20",)
    skip_link_patterns: tuple[str, ...] = (
        "#",
        "mailto:",
        "tel:",
        "wp-login",
        "privacy",
        "cookie",
        "/blog/author/",
    )
    content_selectors: tuple[str, ...] = (
        "article",
        "main article",
        "main",
        ".entry-content",
        ".post-content",
        ".article-content",
        "#content",
    )
    siri_voice: str | None = None
    audio_auto: bool = False
    audiobook_auto: bool = False
    audio_format: str = "m4a"
    audio_timeout: float = 900.0
    audio_chunked: bool = True
    audio_chunk_chars: int = 2500
    audio_chunk_concurrency: int = 3
    audio_chunk_retries: int = 2
    audio_stall_timeout: float = 45.0
    speech_normalize_auto: bool = False
    speech_normalize_agent: str = "codex"
    speech_normalize_command: str = "codex"
    speech_normalize_model: str = ""
    speech_normalize_timeout: float = 900.0
    speech_normalize_force: bool = False
    speech_normalize_fallback: bool = False
    speech_normalize_prompt_path: Path = DEFAULT_SPEECH_NORMALIZE_PROMPT_PATH
    export_formats: tuple[str, ...] = ("html", "txt")
    magazine_title: str = DEFAULT_AUDIOBOOK_MAGAZINE_TITLE
    filename_separator: str = DEFAULT_AUDIOBOOK_FILENAME_SEPARATOR
    audiobook_name_format: str = DEFAULT_AUDIOBOOK_FILENAME_FORMAT

    @property
    def library_dir(self) -> Path:
        return self.output_dir / DEFAULT_LIBRARY_FOLDER_NAME

    @property
    def magazine_output_dir(self) -> Path:
        return self.library_dir / DEFAULT_MAGAZINE_FOLDER_NAME

    @property
    def feed_output_dir(self) -> Path:
        return self.library_dir / self.feed_folder_name

    @property
    def audiobooks_dir(self) -> Path:
        return self.audiobook_output_dir or (self.output_dir / "audiobooks")

    def with_cli_overrides(self, *, verbose: bool) -> "AppConfig":
        if not verbose:
            return self
        return replace(self, verbose=True)


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a TOML table at the root.")
    return data


def _string_tuple(data: dict[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw_value = data.get(key, default)
    if isinstance(raw_value, str):
        return (raw_value,)
    if isinstance(raw_value, list | tuple):
        return tuple(str(item) for item in raw_value)
    raise ValueError(f"Config key {key!r} must be a string or list of strings.")


def load_config(path: Path) -> AppConfig:
    data = _read_toml(path)
    magazine_title = str(
        _config_with_legacy_alias(
            data,
            key="magazine_title",
            legacy_key="audiobook_filename_magazine_title",
            default=DEFAULT_AUDIOBOOK_MAGAZINE_TITLE,
        )
    )
    output_parent_dir = Path(
        str(data.get("output_parent_dir", DEFAULT_OUTPUT_PARENT_DIR))
    ).expanduser()
    collection_dir_name = str(data.get("collection_dir_name", _slug_output_name(magazine_title)))
    output_dir_value = data.get("output_dir")
    output_dir = (
        Path(str(output_dir_value)).expanduser()
        if output_dir_value is not None
        else output_parent_dir / collection_dir_name
    )
    return AppConfig(
        verbose=bool(data.get("verbose", False)),
        base_url=str(data.get("base_url", "https://www.rivistadomino.it/")),
        magazine_index_url=str(
            data.get("magazine_index_url", "https://www.rivistadomino.it/mio-account/my_domino/")
        ),
        auth_login_url=str(data.get("auth_login_url", "https://www.rivistadomino.it/mio-account/")),
        auth_username=str(data.get("auth_username", "")),
        auth_password=str(data.get("auth_password", "")),
        auth_username_field=str(data.get("auth_username_field", "username")),
        auth_password_field=str(data.get("auth_password_field", "password")),
        auth_submit_field=str(data.get("auth_submit_field", "login")),
        auth_submit_value=str(data.get("auth_submit_value", "Accedi")),
        auth_session_path=Path(
            str(data.get("auth_session_path", DEFAULT_SESSION_PATH))
        ).expanduser(),
        auth_browser_timeout=float(data.get("auth_browser_timeout", 300.0)),
        output_parent_dir=output_parent_dir,
        collection_dir_name=collection_dir_name,
        output_dir=output_dir,
        audiobook_output_dir=(
            Path(str(data["audiobook_output_dir"])).expanduser()
            if data.get("audiobook_output_dir") is not None
            else None
        ),
        feed_index_url=str(
            data.get(
                "feed_index_url",
                data.get(
                    "weekly_index_url",
                    "https://www.rivistadomino.it/blog/category/la-settimana-di-domino/",
                ),
            )
        ),
        feed_folder_name=str(
            data.get(
                "feed_folder_name",
                data.get("weekly_folder_name", _folder_name_from_legacy_weekly_output(data)),
            )
        ),
        request_timeout=float(data.get("request_timeout", 30.0)),
        user_agent=str(data.get("user_agent", DEFAULT_USER_AGENT)),
        issue_link_patterns=_string_tuple(data, "issue_link_patterns", ("?sfoglia=1",)),
        article_link_patterns=_string_tuple(data, "article_link_patterns", ("/blog/20",)),
        feed_article_link_patterns=_string_tuple(
            data,
            "feed_article_link_patterns",
            _string_tuple(data, "weekly_article_link_patterns", ("/blog/20",)),
        ),
        skip_link_patterns=_string_tuple(
            data,
            "skip_link_patterns",
            ("#", "mailto:", "tel:", "wp-login", "privacy", "cookie", "/blog/author/"),
        ),
        content_selectors=_string_tuple(
            data,
            "content_selectors",
            (
                "article",
                "main article",
                "main",
                ".entry-content",
                ".post-content",
                ".article-content",
                "#content",
            ),
        ),
        siri_voice=str(data["siri_voice"]) if data.get("siri_voice") else None,
        audio_auto=bool(data.get("audio_auto", False)),
        audiobook_auto=bool(data.get("audiobook_auto", False)),
        audio_format=normalize_audio_format(str(data.get("audio_format", "m4a"))),
        audio_timeout=normalize_audio_timeout(data.get("audio_timeout", 900.0)),
        audio_chunked=bool(data.get("audio_chunked", True)),
        audio_chunk_chars=normalize_positive_int(
            data.get("audio_chunk_chars", 2500),
            key="audio_chunk_chars",
        ),
        audio_chunk_concurrency=normalize_positive_int(
            data.get("audio_chunk_concurrency", 3),
            key="audio_chunk_concurrency",
        ),
        audio_chunk_retries=normalize_non_negative_int(
            data.get("audio_chunk_retries", 2),
            key="audio_chunk_retries",
        ),
        audio_stall_timeout=normalize_audio_timeout(data.get("audio_stall_timeout", 45.0)),
        speech_normalize_auto=bool(data.get("speech_normalize_auto", False)),
        speech_normalize_agent=str(data.get("speech_normalize_agent", "codex")),
        speech_normalize_command=str(data.get("speech_normalize_command", "codex")),
        speech_normalize_model=str(data.get("speech_normalize_model", "")),
        speech_normalize_timeout=normalize_audio_timeout(
            data.get("speech_normalize_timeout", 900.0)
        ),
        speech_normalize_force=bool(data.get("speech_normalize_force", False)),
        speech_normalize_fallback=bool(data.get("speech_normalize_fallback", False)),
        speech_normalize_prompt_path=Path(
            str(data.get("speech_normalize_prompt_path", DEFAULT_SPEECH_NORMALIZE_PROMPT_PATH))
        ).expanduser(),
        export_formats=normalize_export_formats(data.get("export_formats", ("html", "txt"))),
        magazine_title=magazine_title,
        filename_separator=validate_audiobook_separator(
            str(
                _config_with_legacy_alias(
                    data,
                    key="filename_separator",
                    legacy_key="audiobook_filename_separator",
                    default=DEFAULT_AUDIOBOOK_FILENAME_SEPARATOR,
                )
            )
        ),
        audiobook_name_format=validate_audiobook_format(
            str(
                _config_with_legacy_alias(
                    data,
                    key="audiobook_name_format",
                    legacy_key="audiobook_filename_format",
                    default=DEFAULT_AUDIOBOOK_FILENAME_FORMAT,
                )
            )
        ),
    )


def _folder_name_from_legacy_weekly_output(data: dict[str, Any]) -> str:
    if "weekly_output_dir" not in data:
        return "la-settimana-di-domino"
    return Path(str(data["weekly_output_dir"])).expanduser().name or "la-settimana-di-domino"


def _config_with_legacy_alias(
    data: dict[str, Any],
    *,
    key: str,
    legacy_key: str,
    default: object,
) -> object:
    if key in data:
        return data[key]
    if legacy_key in data:
        return data[legacy_key]
    return default


def _slug_output_name(value: str) -> str:
    lowered = value.strip().lower()
    lowered = "".join(char if char.isalnum() else "_" for char in lowered)
    lowered = lowered.strip("_")
    while "__" in lowered:
        lowered = lowered.replace("__", "_")
    return lowered or "domino"


def normalize_export_formats(value: object) -> tuple[str, ...]:
    raw_formats: tuple[str, ...]
    if isinstance(value, str):
        raw_formats = (value,)
    elif isinstance(value, list | tuple):
        raw_formats = tuple(str(item) for item in value)
    else:
        raise ValueError("export_formats must be a string or list of strings.")

    formats: list[str] = []
    for item in raw_formats:
        normalized = item.strip().lower().removeprefix(".")
        if normalized == "text":
            normalized = "txt"
        if normalized not in SUPPORTED_EXPORT_FORMATS:
            supported = ", ".join(SUPPORTED_EXPORT_FORMATS)
            raise ValueError(f"export_formats must contain only: {supported}.")
        if normalized not in formats:
            formats.append(normalized)
    if not formats:
        raise ValueError("export_formats must contain at least one format.")
    return tuple(formats)


def normalize_audio_timeout(value: object) -> float:
    if not isinstance(value, str | int | float):
        raise ValueError("audio_timeout must be a number of seconds.")
    timeout = float(value)
    if timeout <= 0:
        raise ValueError("audio_timeout must be greater than 0 seconds.")
    return timeout


def normalize_positive_int(value: object, *, key: str) -> int:
    if not isinstance(value, str | int):
        raise ValueError(f"{key} must be a positive integer.")
    normalized = int(value)
    if normalized <= 0:
        raise ValueError(f"{key} must be greater than 0.")
    return normalized


def normalize_non_negative_int(value: object, *, key: str) -> int:
    if not isinstance(value, str | int):
        raise ValueError(f"{key} must be a non-negative integer.")
    normalized = int(value)
    if normalized < 0:
        raise ValueError(f"{key} must be greater than or equal to 0.")
    return normalized
