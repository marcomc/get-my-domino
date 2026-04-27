"""Configuration support for get-my-domino."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .audio import normalize_audio_format

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "get-my-domino" / "config.toml"
DEFAULT_SESSION_PATH = Path.home() / ".config" / "get-my-domino" / "session.json"
DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / "rivistadomino"


@dataclass(frozen=True)
class AppConfig:
    app_name: str = "get-my-domino"
    default_output: str = "text"
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
    output_dir: Path = DEFAULT_OUTPUT_DIR
    feed_index_url: str = "https://www.rivistadomino.it/blog/category/la-settimana-di-domino/"
    feed_folder_name: str = "la-settimana-di-domino"
    request_timeout: float = 30.0
    user_agent: str = "get-my-domino/0.1.0"
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
    audio_format: str = "m4a"

    @property
    def feed_output_dir(self) -> Path:
        return self.output_dir / self.feed_folder_name

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
    return AppConfig(
        app_name=str(data.get("app_name", "get-my-domino")),
        default_output=str(data.get("default_output", "text")),
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
        output_dir=Path(str(data.get("output_dir", DEFAULT_OUTPUT_DIR))).expanduser(),
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
        user_agent=str(data.get("user_agent", "get-my-domino/0.1.0")),
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
        audio_format=normalize_audio_format(str(data.get("audio_format", "m4a"))),
    )


def _folder_name_from_legacy_weekly_output(data: dict[str, Any]) -> str:
    if "weekly_output_dir" not in data:
        return "la-settimana-di-domino"
    return Path(str(data["weekly_output_dir"])).expanduser().name or "la-settimana-di-domino"
