from __future__ import annotations

import errno
import fcntl
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import requests
from pytest import CaptureFixture, MonkeyPatch
from requests import Response
from requests.cookies import RequestsCookieJar

from get_my_domino import __version__, cli, speech_normalize
from get_my_domino import audio as audio_module
from get_my_domino.audiobook_naming import AudiobookFilenameSettings
from get_my_domino.config import AppConfig, load_config
from get_my_domino.extract import extract_article, extract_links
from get_my_domino.models import Article, Issue, Link
from get_my_domino.session_store import load_cookies, save_cookies
from get_my_domino.storage import (
    article_text_document,
    read_manifest,
    write_article,
    write_manifest,
)
from get_my_domino.web import WebClient


@pytest.fixture(autouse=True)
def isolate_audio_lock(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GET_MY_DOMINO_AUDIO_LOCK_PATH", str(tmp_path / "audio.lock"))


class FakeResponse(Response):
    def __init__(self, text: str, *, url: str = "https://example.test/", status_code: int = 200):
        super().__init__()
        self.status_code = status_code
        self.url = url
        self.encoding = "utf-8"
        self._content = text.encode("utf-8")


class FakeSession:
    def __init__(self, pages: dict[str, str]) -> None:
        self.headers: dict[str, str] = {}
        self.cookies = RequestsCookieJar()
        self.pages = pages
        self.posts: list[tuple[str, dict[str, str]]] = []
        self.gets: list[str] = []

    def get(self, url: str, *, timeout: float) -> Response:
        del timeout
        self.gets.append(url)
        return FakeResponse(self.pages[url], url=url)

    def post(
        self,
        url: str,
        *,
        data: dict[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> Response:
        del timeout, allow_redirects
        self.posts.append((url, data))
        return FakeResponse(self.pages["POST " + url], url=url)


class FlakyGetSession(FakeSession):
    def __init__(self, pages: dict[str, str]) -> None:
        super().__init__(pages)
        self.failures_remaining = 1

    def get(self, url: str, *, timeout: float) -> Response:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise requests.ConnectionError("remote disconnected")
        return super().get(url, timeout=timeout)


def test_main_without_args_prints_focused_help(capsys: CaptureFixture[str]) -> None:
    expected_usage = "usage: get-my-domino [--version] [--config PATH] [--verbose] <command>"
    result = cli.main([])

    captured = capsys.readouterr()

    assert result == 0
    assert expected_usage in captured.out
    assert "Commands:" in captured.out
    assert "info" in captured.out


def test_version_flag_prints_version(capsys: CaptureFixture[str]) -> None:
    result = cli.main(["--version"])

    captured = capsys.readouterr()

    assert result == 0
    assert captured.out.strip() == __version__


def test_unknown_command_prints_friendly_suggestion(capsys: CaptureFixture[str]) -> None:
    result = cli.main(["dowload"])

    captured = capsys.readouterr()

    assert result == 2
    assert "Unknown command: dowload" in captured.err
    assert "Did you mean: download?" in captured.err
    assert "invalid choice" not in captured.err
    assert "choose from" not in captured.err


def test_main_help_mentions_refresh_and_repackage_commands(capsys: CaptureFixture[str]) -> None:
    result = cli.main([])

    captured = capsys.readouterr()

    assert result == 0
    assert "refresh-issue-metadata" in captured.out
    assert "repackage-audiobook" in captured.out


def test_console_main_reads_sys_argv(monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.argv", ["get-my-domino", "--version"])

    result = cli.main()

    captured = capsys.readouterr()

    assert result == 0
    assert captured.out.strip() == __version__


def test_info_command_reads_config_file(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "verbose = false",
                'auth_username = "reader@example.test"',
                'auth_password = "secret"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = cli.main(["--config", str(config_path), "info"])

    captured = capsys.readouterr()

    assert result == 0
    assert f"config_path: {config_path}" in captured.out
    assert "auth_username: reader@example.test" in captured.out
    assert "auth_password: configured" in captured.out
    assert "secret" not in captured.out


def test_info_command_reports_missing_auth_username(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('auth_password = "secret"\n', encoding="utf-8")

    result = cli.main(["--config", str(config_path), "info"])

    captured = capsys.readouterr()

    assert result == 0
    assert (
        "auth_username: missing (set auth_username in config.toml or use login --browser)"
        in captured.out
    )


def test_info_command_can_emit_json(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("verbose = true\n", encoding="utf-8")

    result = cli.main(["--config", str(config_path), "info", "--json"])

    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["project_name"] == "get-my-domino"
    assert payload["cli_name"] == "get-my-domino"
    assert payload["config"]["verbose"] is True


def test_info_json_redacts_auth_password(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('auth_password = "secret"\n', encoding="utf-8")

    result = cli.main(["--config", str(config_path), "info", "--json"])

    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["config"]["auth_password"] == "configured"
    assert "secret" not in captured.out


def test_config_reads_woocommerce_auth_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                'auth_login_url = "https://www.rivistadomino.it/mio-account/"',
                'auth_username = "reader@example.test"',
                'auth_password = "secret"',
                'auth_username_field = "username"',
                'auth_password_field = "password"',
                'auth_submit_field = "login"',
                'auth_submit_value = "Accedi"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.auth_login_url == "https://www.rivistadomino.it/mio-account/"
    assert config.auth_username == "reader@example.test"
    assert config.auth_password == "secret"
    assert config.auth_username_field == "username"
    assert config.auth_password_field == "password"
    assert config.auth_submit_field == "login"
    assert config.auth_submit_value == "Accedi"


def test_config_reads_audio_defaults_and_normalizes_mp4a_alias(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "audio_auto = true",
                "audiobook_auto = true",
                'audiobook_output_dir = "~/Audiobooks/Domino"',
                'audio_format = "mp4a"',
                "audio_timeout = 123",
                "audio_chunked = false",
                "audio_chunk_chars = 3456",
                "audio_chunk_concurrency = 4",
                "audio_chunk_retries = 3",
                "audio_stall_timeout = 67",
                "speech_normalize_auto = true",
                'speech_normalize_agent = "codex"',
                'speech_normalize_command = "codex"',
                'speech_normalize_model = "gpt-5.2"',
                "speech_normalize_timeout = 456",
                "speech_normalize_force = true",
                "speech_normalize_fallback = true",
                'speech_normalize_prompt_path = "~/custom-prompt.md"',
                'siri_voice = "Siri Voice 2"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.audio_auto is True
    assert config.audiobook_auto is True
    assert config.audiobook_output_dir == Path.home() / "Audiobooks" / "Domino"
    assert config.audiobooks_dir == Path.home() / "Audiobooks" / "Domino"
    assert config.audio_format == "m4a"
    assert config.audio_timeout == 123.0
    assert config.audio_chunked is False
    assert config.audio_chunk_chars == 3456
    assert config.audio_chunk_concurrency == 4
    assert config.audio_chunk_retries == 3
    assert config.audio_stall_timeout == 67.0
    assert config.speech_normalize_auto is True
    assert config.speech_normalize_agent == "codex"
    assert config.speech_normalize_command == "codex"
    assert config.speech_normalize_model == "gpt-5.2"
    assert config.speech_normalize_timeout == 456.0
    assert config.speech_normalize_force is True
    assert config.speech_normalize_fallback is True
    assert config.speech_normalize_prompt_path == Path.home() / "custom-prompt.md"
    assert config.siri_voice == "Siri Voice 2"


def test_config_reads_preferred_audiobook_naming_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                'magazine_title = "Rivista Domino"',
                'filename_separator = "."',
                'audiobook_name_format = "{magazine}{sep}{year}{sep}{number}{sep}{title_slug}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.magazine_title == "Rivista Domino"
    assert config.filename_separator == "."
    assert config.audiobook_name_format == "{magazine}{sep}{year}{sep}{number}{sep}{title_slug}"


def test_config_derives_default_output_dir_from_collection_dir_name(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                f'output_parent_dir = "{tmp_path}"',
                'magazine_title = "Rivista Domino"',
                'collection_dir_name = "rivista_domino"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.output_parent_dir == tmp_path
    assert config.collection_dir_name == "rivista_domino"
    assert config.output_dir == tmp_path / "rivista_domino"
    assert config.audiobooks_dir == tmp_path / "rivista_domino" / "audiobooks"


def test_config_supports_external_audiobook_output_dir(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                f'output_parent_dir = "{tmp_path}"',
                'collection_dir_name = "rivista_domino"',
                'audiobook_output_dir = "~/Audiobooks/Rivista Domino"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.output_dir == tmp_path / "rivista_domino"
    assert config.audiobook_output_dir == Path.home() / "Audiobooks" / "Rivista Domino"
    assert config.audiobooks_dir == Path.home() / "Audiobooks" / "Rivista Domino"


def test_config_reads_legacy_audiobook_naming_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                'audiobook_filename_magazine_title = "Rivista Domino"',
                'audiobook_filename_separator = "_"',
                'audiobook_filename_format = "{magazine_slug}{sep}{issue}{sep}{title_slug}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.magazine_title == "Rivista Domino"
    assert config.filename_separator == "_"
    assert config.audiobook_name_format == "{magazine_slug}{sep}{issue}{sep}{title_slug}"


def test_config_rejects_unknown_audio_format(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('audio_format = "wav"\n', encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "audio_format" in str(exc)
    else:
        raise AssertionError("Expected audio_format validation failure.")


def test_config_reads_export_formats(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('export_formats = ["txt", "rtf"]\n', encoding="utf-8")

    config = load_config(config_path)

    assert config.export_formats == ("txt", "rtf")


def test_config_rejects_unknown_export_format(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('export_formats = ["txt", "pdf"]\n', encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "export_formats" in str(exc)
    else:
        raise AssertionError("Expected export_formats validation failure.")


def test_web_client_logs_in_with_hidden_fields_and_reuses_session(tmp_path: Path) -> None:
    login_url = "https://www.rivistadomino.it/mio-account/"
    issue_url = "https://www.rivistadomino.it/numero-1/"
    session = FakeSession(
        {
            login_url: """
            <form class="woocommerce-form-login" method="post">
              <input name="username">
              <input name="password" type="password">
              <input name="woocommerce-login-nonce" type="hidden" value="nonce-value">
              <input name="_wp_http_referer" type="hidden" value="/mio-account/">
              <button name="login" value="Accedi">Accedi</button>
            </form>
            """,
            "POST " + login_url: "<main><a href='/mio-account/customer-logout/'>Logout</a></main>",
            issue_url: "<a href='/articolo/example/'>Articolo</a>",
        }
    )
    config = AppConfig(
        auth_login_url=login_url,
        auth_username="reader@example.test",
        auth_password="secret",
        article_link_patterns=("articolo",),
        auth_session_path=tmp_path / "session.json",
    )

    links = WebClient(config, session=session).discover_articles(issue_url)

    assert [link.url for link in links] == ["https://www.rivistadomino.it/articolo/example"]
    assert session.gets == [login_url, issue_url]
    assert session.posts == [
        (
            login_url,
            {
                "username": "reader@example.test",
                "password": "secret",
                "woocommerce-login-nonce": "nonce-value",
                "_wp_http_referer": "/mio-account/",
                "login": "Accedi",
            },
        )
    ]
    assert config.auth_session_path.exists()


def test_web_client_downloads_article_after_authentication(tmp_path: Path) -> None:
    login_url = "https://www.rivistadomino.it/mio-account/"
    article_url = "https://www.rivistadomino.it/articolo/example/"
    session = FakeSession(
        {
            login_url: """
            <form method="post">
              <input name="username">
              <input name="password" type="password">
              <button name="login" value="Accedi">Accedi</button>
            </form>
            """,
            "POST " + login_url: "<main>Area account</main>",
            article_url: "<article><h1>Titolo privato</h1><p>Testo riservato.</p></article>",
        }
    )
    config = AppConfig(
        auth_login_url=login_url,
        auth_username="reader@example.test",
        auth_password="secret",
        content_selectors=("article",),
        auth_session_path=tmp_path / "session.json",
    )

    article = WebClient(config, session=session).download_article(article_url)

    assert article.title == "Titolo privato"
    assert article.url == "https://www.rivistadomino.it/articolo/example"
    assert "Testo riservato." in article.text
    assert len(session.posts) == 1


def test_web_client_reuses_saved_session_without_credentials(tmp_path: Path) -> None:
    login_url = "https://www.rivistadomino.it/mio-account/"
    issue_url = "https://www.rivistadomino.it/numero-1/"
    session_path = tmp_path / "session.json"
    cookie_jar = RequestsCookieJar()
    cookie_jar.set("wordpress_logged_in_example", "cookie-value", domain="www.rivistadomino.it")
    save_cookies(session_path, cookie_jar)

    session = FakeSession(
        {
            login_url: "<main><a href='/mio-account/customer-logout/'>Esci</a></main>",
            issue_url: "<a href='/articolo/example/'>Articolo</a>",
        }
    )
    config = AppConfig(
        auth_login_url=login_url,
        auth_session_path=session_path,
        article_link_patterns=("articolo",),
    )

    links = WebClient(config, session=session).discover_articles(issue_url)

    assert [link.title for link in links] == ["Articolo"]
    assert session.posts == []
    assert session.gets == [login_url, issue_url]


def test_web_client_retries_transient_get_disconnect(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.setattr("get_my_domino.web.time.sleep", lambda seconds: None)
    issue_url = "https://www.rivistadomino.it/numero-1/"
    session = FlakyGetSession(
        {
            issue_url: "<a href='/articolo/example/'>Articolo</a>",
        }
    )
    config = AppConfig(
        auth_session_path=tmp_path / "missing-session.json",
        article_link_patterns=("articolo",),
    )

    links = WebClient(config, session=session).discover_articles(issue_url)

    captured = capsys.readouterr()
    assert [link.title for link in links] == ["Articolo"]
    assert "↻ Retrying request 2/3: GET" in captured.err
    assert session.gets == [issue_url]


def test_session_store_round_trips_private_cookie_file(tmp_path: Path) -> None:
    session_path = tmp_path / "session.json"
    cookie_jar = RequestsCookieJar()
    cookie_jar.set("wordpress_logged_in_example", "cookie-value", domain=".rivistadomino.it")

    save_cookies(session_path, cookie_jar)
    loaded = load_cookies(session_path)

    assert session_path.stat().st_mode & 0o777 == 0o600
    assert loaded.get("wordpress_logged_in_example", domain=".rivistadomino.it") == "cookie-value"


def test_feed_articles_follow_pagination_and_deduplicate() -> None:
    first_page = "https://www.rivistadomino.it/blog/category/la-settimana-di-domino/"
    second_page = "https://www.rivistadomino.it/blog/category/la-settimana-di-domino/page/2/"
    session = FakeSession(
        {
            first_page: """
            <link rel="next" href="/blog/category/la-settimana-di-domino/page/2/">
            <a class="article_title" href="/blog/2026/04/24/primo/">Primo</a>
            <a class="article_title" href="/blog/2026/04/17/secondo/">Secondo</a>
            """,
            second_page: """
            <a class="article_title" href="/blog/2026/04/17/secondo/">Secondo</a>
            <a class="article_title" href="/blog/2026/04/10/terzo/">Terzo</a>
            """,
        }
    )
    config = AppConfig(
        feed_index_url=first_page,
        feed_article_link_patterns=("/blog/20",),
    )

    links = WebClient(config, session=session).discover_feed_articles(max_pages=2)

    assert [link.title for link in links] == ["Primo", "Secondo", "Terzo"]


def test_issue_articles_keep_month_groups_dates_and_order(tmp_path: Path) -> None:
    issue_url = "https://www.rivistadomino.it/prodotto/guaio-persiano/"
    session = FakeSession(
        {
            issue_url: """
            <article class="product">
              <meta property="og:image" content="https://cdn.example.test/guaio.jpg" />
              <div class="summary">
                <h1 class="product_title">Guaio persiano</h1>
                <p>4/2026 Guaio persiano La crisi spiegata</p>
              </div>
              <div id="tab-articles">
                <h3>L'Editoriale</h3>
                <a class="article_title" href="/blog/2026/04/21/editoriale/">Editoriale</a>
                <h3>La guerra va male</h3>
                <a class="article_title" href="/blog/2026/04/21/casa-bianca/">Casa Bianca</a>
                <a href="/blog/author/example/">Autore</a>
                <a class="article_title" href="/blog/2026/04/22/israele/">Israele</a>
              </div>
            </article>
            """,
        }
    )

    issue = WebClient(
        AppConfig(auth_session_path=tmp_path / "missing-session.json"),
        session=session,
    ).discover_issue(issue_url)

    assert issue.title == "Guaio persiano"
    assert issue.issue_code == "2026-04"
    assert issue.cover_image_url == "https://cdn.example.test/guaio.jpg"
    assert issue.summary == "4/2026 La crisi spiegata"
    assert [
        (link.title, link.group, link.published_date, link.order) for link in issue.articles
    ] == [
        ("Editoriale", "L'Editoriale", "2026-04-21", 1),
        ("Casa Bianca", "La guerra va male", "2026-04-21", 2),
        ("Israele", "La guerra va male", "2026-04-22", 3),
    ]


def test_catalog_lists_issues_with_indexes(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    first_issue = "https://www.rivistadomino.it/prodotto/primo/?sfoglia=1"
    second_issue = "https://www.rivistadomino.it/prodotto/secondo/?sfoglia=1"
    session = FakeSession(
        {
            "https://www.rivistadomino.it/mio-account/my_domino/": f"""
            <a href="{first_issue}">
              5/2024 Primo numero
              7,50 € - 10,00 € Fascia di prezzo: da 7,50 € a 10,00 €
              Una breve sinossi del primo numero.
            </a>
            <a href="{second_issue}">
              6/2024 Secondo numero 29,00 €
              Una breve sinossi del secondo numero.
            </a>
            """,
        }
    )
    config = AppConfig(auth_session_path=tmp_path / "missing-session.json")

    result = cli._handle_catalog(
        config,
        client=WebClient(config, session=session),
        all_issues=False,
        issue_selector=None,
        include_feed=False,
        feed_pages=1,
        as_json=False,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Available issues" in captured.out
    assert "2024-06  Secondo numero" in captured.out
    assert "2024-05  Primo numero" in captured.out
    assert "[1]" not in captured.out
    assert "[2]" not in captured.out
    assert "    Una breve sinossi del primo numero." in captured.out
    assert "    Una breve sinossi del secondo numero." in captured.out
    assert "€12,00" not in captured.out
    assert "7,50" not in captured.out
    assert "10,00" not in captured.out
    assert "Fascia di prezzo" not in captured.out
    assert "29,00" not in captured.out
    assert "    https://www.rivistadomino.it/prodotto/primo?sfoglia=1" in captured.out


def test_catalog_expands_selected_issue_grouped_by_section(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    issue_url = "https://www.rivistadomino.it/prodotto/guaio-persiano/?sfoglia=1"
    session = FakeSession(
        {
            "https://www.rivistadomino.it/mio-account/my_domino/": f"""
            <a href="{issue_url}">4/2026 Guaio persiano</a>
            """,
            "https://www.rivistadomino.it/prodotto/guaio-persiano?sfoglia=1": """
            <article class="product">
              <div class="summary">
                <h1 class="product_title">Guaio persiano</h1>
                <p>4/2026 Guaio persiano</p>
              </div>
              <div id="tab-articles">
                <h3>L'Editoriale</h3>
                <a class="article_title" href="/blog/2026/04/21/editoriale/">Editoriale</a>
                <h3>La guerra va male</h3>
                <a class="article_title" href="/blog/2026/04/21/casa-bianca/">Casa Bianca</a>
              </div>
            </article>
            """,
        }
    )
    config = AppConfig(auth_session_path=tmp_path / "missing-session.json")

    result = cli._handle_catalog(
        config,
        client=WebClient(config, session=session),
        all_issues=False,
        issue_selector="2026-04",
        include_feed=False,
        feed_pages=1,
        as_json=False,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Guaio persiano" in captured.out
    assert "issue: 2026-04" in captured.out
    assert "published: 2026-04-21" in captured.out
    assert "├── 01  L'Editoriale" in captured.out
    assert "│   └── 01  Editoriale" in captured.out
    assert "└── 02  La guerra va male" in captured.out
    assert "    └── 02  Casa Bianca" in captured.out
    assert "  2026-04-21  Editoriale" not in captured.out
    assert "url: https://www.rivistadomino.it/blog/2026/04/21/editoriale" in captured.out


def test_catalog_all_expands_every_issue(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    first_issue = "https://www.rivistadomino.it/prodotto/primo/?sfoglia=1"
    second_issue = "https://www.rivistadomino.it/prodotto/secondo/?sfoglia=1"
    session = FakeSession(
        {
            "https://www.rivistadomino.it/mio-account/my_domino/": f"""
            <a href="{first_issue}">Primo numero</a>
            <a href="{second_issue}">Secondo numero</a>
            """,
            "https://www.rivistadomino.it/prodotto/primo?sfoglia=1": """
            <article class="product">
              <h1 class="product_title">Primo numero</h1>
              <div id="tab-articles">
                <h3>Sezione A</h3>
                <a class="article_title" href="/blog/2026/04/21/primo/">Primo articolo</a>
              </div>
            </article>
            """,
            "https://www.rivistadomino.it/prodotto/secondo?sfoglia=1": """
            <article class="product">
              <h1 class="product_title">Secondo numero</h1>
              <div id="tab-articles">
                <h3>Sezione B</h3>
                <a class="article_title" href="/blog/2026/04/22/secondo/">Secondo articolo</a>
              </div>
            </article>
            """,
        }
    )
    config = AppConfig(auth_session_path=tmp_path / "missing-session.json")

    result = cli._handle_catalog(
        config,
        client=WebClient(config, session=session),
        all_issues=True,
        issue_selector=None,
        include_feed=False,
        feed_pages=1,
        as_json=False,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Primo numero" in captured.out
    assert "Primo articolo" in captured.out
    assert "Secondo numero" in captured.out
    assert "Secondo articolo" in captured.out


def test_catalog_rejects_numeric_issue_selector(tmp_path: Path) -> None:
    session = FakeSession(
        {
            "https://www.rivistadomino.it/mio-account/my_domino/": """
            <a href="https://www.rivistadomino.it/prodotto/primo/?sfoglia=1">
              5/2024 Primo numero
            </a>
            """,
        }
    )
    config = AppConfig(auth_session_path=tmp_path / "missing-session.json")

    try:
        cli._handle_catalog(
            config,
            client=WebClient(config, session=session),
            all_issues=False,
            issue_selector="1",
            include_feed=False,
            feed_pages=1,
            as_json=False,
        )
    except ValueError as exc:
        assert "Use a YYYY-NN issue code" in str(exc)
    else:
        raise AssertionError("Expected numeric catalog selector to fail.")


def test_download_resolves_issue_article_selector(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    issue_url = "https://www.rivistadomino.it/prodotto/guaio-persiano/?sfoglia=1"
    article_url = "https://www.rivistadomino.it/blog/2026/04/21/editoriale"
    session = FakeSession(
        {
            "https://www.rivistadomino.it/mio-account/my_domino/": f"""
            <a href="{issue_url}">4/2026 Guaio persiano</a>
            """,
            "https://www.rivistadomino.it/prodotto/guaio-persiano?sfoglia=1": f"""
            <article class="product">
              <div class="summary">
                <h1 class="product_title">Guaio persiano</h1>
                <p>4/2026 Guaio persiano</p>
              </div>
              <div id="tab-articles">
                <h3>L'Editoriale</h3>
                <a class="article_title" href="{article_url}">Editoriale</a>
              </div>
            </article>
            """,
            article_url: "<article><h1>Editoriale</h1><p>Corpo.</p></article>",
        }
    )
    config = AppConfig(
        output_dir=tmp_path / "exports",
        auth_session_path=tmp_path / "missing-session.json",
    )
    monkeypatch.setattr(
        cli, "WebClient", lambda loaded_config: WebClient(loaded_config, session=session)
    )

    result = cli._download_issue_article(
        issue_selector="2026-04",
        article_selector="1",
        config=config,
        root_output_dir=config.output_dir,
        create_audio=False,
        audio_format="m4a",
        audio_timeout=900.0,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "→ Finding issue 2026-04..." in captured.err
    assert "✓ Finding issue 2026-04" in captured.err
    assert "→ Selecting article 1..." in captured.err
    assert "→ Downloading article" in captured.err
    assert "→ Writing files in 01-editoriale..." in captured.err
    assert "article" in captured.out
    assert "export" in captured.out
    assert "audio" in captured.out
    assert "✓ Editoriale" in captured.out
    assert "written" in captured.out
    assert "off" in captured.out
    assert (
        tmp_path
        / "exports"
        / "library"
        / "rivista"
        / "2026-04-guaio-persiano"
        / "01-l-editoriale"
        / "01-editoriale"
        / "01-editoriale.html"
    ).exists()


def test_download_issue_all_downloads_every_article(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    issue_url = "https://www.rivistadomino.it/prodotto/guaio-persiano/?sfoglia=1"
    first_article = "https://www.rivistadomino.it/blog/2026/04/21/editoriale"
    second_article = "https://www.rivistadomino.it/blog/2026/04/21/analisi"
    session = FakeSession(
        {
            "https://www.rivistadomino.it/mio-account/my_domino/": f"""
            <a href="{issue_url}">4/2026 Guaio persiano</a>
            """,
            "https://www.rivistadomino.it/prodotto/guaio-persiano?sfoglia=1": f"""
            <article class="product">
              <div class="summary">
                <h1 class="product_title">Guaio persiano</h1>
                <p>4/2026 Guaio persiano</p>
              </div>
              <div id="tab-articles">
                <h3>L'Editoriale</h3>
                <a class="article_title" href="{first_article}">Editoriale</a>
                <h3>Analisi</h3>
                <a class="article_title" href="{second_article}">Analisi</a>
              </div>
            </article>
            """,
            first_article: "<article><h1>Editoriale</h1><p>Corpo 1.</p></article>",
            second_article: "<article><h1>Analisi</h1><p>Corpo 2.</p></article>",
        }
    )
    config = AppConfig(
        output_dir=tmp_path / "exports",
        auth_session_path=tmp_path / "missing-session.json",
    )
    monkeypatch.setattr(
        cli, "WebClient", lambda loaded_config: WebClient(loaded_config, session=session)
    )

    result = cli._download_issue_articles(
        issue_selector="2026-04",
        config=config,
        root_output_dir=config.output_dir,
        create_audio=False,
        create_audiobook=False,
        audio_format="m4a",
        audio_timeout=900.0,
        export_formats=("html", "txt"),
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "✓ Editoriale" in captured.out
    assert "✓ Analisi" in captured.out
    assert captured.out.count("written") == 2
    assert (
        tmp_path
        / "exports"
        / "library"
        / "rivista"
        / "2026-04-guaio-persiano"
        / "01-l-editoriale"
        / "01-editoriale"
        / "01-editoriale.html"
    ).exists()
    assert (
        tmp_path
        / "exports"
        / "library"
        / "rivista"
        / "2026-04-guaio-persiano"
        / "02-analisi"
        / "02-analisi"
        / "02-analisi.html"
    ).exists()


def test_download_issue_all_can_package_issue_audiobook(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    issue_url = "https://www.rivistadomino.it/prodotto/guaio-persiano/?sfoglia=1"
    first_article = "https://www.rivistadomino.it/blog/2026/04/21/editoriale"
    second_article = "https://www.rivistadomino.it/blog/2026/04/21/analisi"
    cover_url = "https://cdn.example.test/guaio.jpg"
    session = FakeSession(
        {
            "https://www.rivistadomino.it/mio-account/my_domino/": f"""
            <a href="{issue_url}">4/2026 Guaio persiano</a>
            """,
            "https://www.rivistadomino.it/prodotto/guaio-persiano?sfoglia=1": f"""
            <article class="product">
              <meta property="og:image" content="{cover_url}" />
              <div class="summary">
                <h1 class="product_title">Guaio persiano</h1>
                <p>4/2026 Guaio persiano La crisi spiegata</p>
              </div>
              <div id="tab-articles">
                <h3>L'Editoriale</h3>
                <a class="article_title" href="{first_article}">Editoriale</a>
                <h3>Analisi</h3>
                <a class="article_title" href="{second_article}">Analisi</a>
              </div>
            </article>
            """,
            first_article: (
                "<article><h1>Editoriale</h1><p class='byline'>di Dario Fabbri</p>"
                "<p>Corpo 1.</p></article>"
            ),
            second_article: (
                "<article><h1>Analisi</h1><p class='byline'>di Federico Petroni</p>"
                "<p>Corpo 2.</p></article>"
            ),
            cover_url: "jpeg-bytes",
        }
    )
    config = AppConfig(
        output_dir=tmp_path / "exports",
        auth_session_path=tmp_path / "missing-session.json",
    )
    packaged: list[dict[str, Any]] = []

    monkeypatch.setattr(
        cli, "WebClient", lambda loaded_config: WebClient(loaded_config, session=session)
    )

    def fake_ensure_audio_for_download(
        raw_path: Path,
        *,
        output_dir: Path,
        audio_format: str,
        **kwargs: object,
    ) -> str:
        del kwargs
        audio_path = cli._audio_output_path(
            raw_path,
            output_dir=output_dir,
            audio_format=audio_format,
        )
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"audio")
        return "generated"

    monkeypatch.setattr(cli, "_ensure_audio_for_download", fake_ensure_audio_for_download)

    def fake_build_m4b(
        output_path: Path,
        *,
        title: str,
        chapters: list[object],
        cover_image_path: Path | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Path:
        packaged.append(
            {
                "output_path": output_path,
                "title": title,
                "chapters": chapters,
                "cover_image_path": cover_image_path,
                "metadata": metadata,
            }
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("book", encoding="utf-8")
        return output_path

    monkeypatch.setattr(cli, "build_m4b", fake_build_m4b)

    result = cli._download_issue_articles(
        issue_selector="2026-04",
        config=config,
        root_output_dir=config.output_dir,
        create_audio=True,
        create_audiobook=True,
        audio_format="m4a",
        audio_timeout=900.0,
        export_formats=("txt",),
    )

    assert result == 0
    assert len(packaged) == 1
    assert packaged[0]["title"] == "Guaio persiano"
    assert packaged[0]["cover_image_path"] == (
        tmp_path / "exports" / "library" / "rivista" / "2026-04-guaio-persiano" / "cover.jpg"
    )
    assert packaged[0]["chapters"][0].title == "01. Editoriale (di Dario Fabbri)"
    assert packaged[0]["chapters"][0].contributor == "Dario Fabbri"
    metadata = packaged[0]["metadata"]
    assert metadata is not None
    assert metadata["artist"] == "Dario Fabbri, Federico Petroni"
    assert metadata["album_artist"] == "Dario Fabbri, Federico Petroni"
    assert metadata["date"] == "2026-04-21"
    assert metadata["publisher"] == "Rivista Domino"
    assert metadata["composer"] == "Dario Fabbri, Federico Petroni"
    assert metadata["contributors"] == "Dario Fabbri, Federico Petroni"
    assert metadata["description"] == "4/2026 La crisi spiegata"
    issue_json = (
        tmp_path / "exports" / "library" / "rivista" / "2026-04-guaio-persiano" / "issue.json"
    )
    payload = json.loads(issue_json.read_text(encoding="utf-8"))
    assert payload["cover_image_path"] == "cover.jpg"
    assert payload["contributors"] == ["Dario Fabbri", "Federico Petroni"]
    assert payload["articles"][0]["author"] == "Dario Fabbri"
    assert payload["published_date"] == "2026-04-21"


def test_download_issue_all_skips_audiobook_for_empty_issue(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    issue_url = "https://www.rivistadomino.it/prodotto/numero-vuoto/?sfoglia=1"
    session = FakeSession(
        {
            "https://www.rivistadomino.it/mio-account/my_domino/": f"""
            <a href="{issue_url}">8/2022 Numero vuoto</a>
            """,
            "https://www.rivistadomino.it/prodotto/numero-vuoto?sfoglia=1": """
            <article class="product">
              <div class="summary">
                <h1 class="product_title">Numero vuoto</h1>
                <p>8/2022 Numero vuoto Nessun articolo disponibile</p>
              </div>
              <div id="tab-articles"></div>
            </article>
            """,
        }
    )
    config = AppConfig(
        output_dir=tmp_path / "exports",
        auth_session_path=tmp_path / "missing-session.json",
    )

    monkeypatch.setattr(
        cli, "WebClient", lambda loaded_config: WebClient(loaded_config, session=session)
    )

    def fake_build_m4b(*args: object, **kwargs: object) -> Path:
        del args, kwargs
        raise AssertionError("audiobook packaging should be skipped for empty issues")

    monkeypatch.setattr(cli, "build_m4b", fake_build_m4b)

    result = cli._download_issue_articles(
        issue_selector="2022-08",
        config=config,
        root_output_dir=config.output_dir,
        create_audio=False,
        create_audiobook=True,
        audio_format="m4a",
        audio_timeout=900.0,
        export_formats=("txt",),
    )

    assert result == 0
    issue_json = (
        tmp_path / "exports" / "library" / "rivista" / "2022-08-numero-vuoto" / "issue.json"
    )
    payload = json.loads(issue_json.read_text(encoding="utf-8"))
    assert payload["article_count"] == 0
    assert payload["articles"] == []
    assert payload["chapters"] == []


def test_issue_audiobook_falls_back_to_legacy_audio_names(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    output_dir = tmp_path / "exports"
    issue_dir = output_dir / "library" / "rivista" / "2026-04-guaio-persiano"
    article_dir = issue_dir / "01-l-editoriale" / "01-editoriale"
    legacy_audio = output_dir / "audio" / "2026-04-guaio-persiano" / "01-2026-04-21-editoriale.m4a"
    article_dir.mkdir(parents=True)
    legacy_audio.parent.mkdir(parents=True)
    legacy_audio.write_bytes(b"audio")
    packaged: list[dict[str, Any]] = []

    def fake_build_m4b(
        output_path: Path,
        *,
        title: str,
        chapters: list[object],
        cover_image_path: Path | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Path:
        del title, cover_image_path, metadata
        packaged.append({"output_path": output_path, "chapters": chapters})
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("book", encoding="utf-8")
        return output_path

    monkeypatch.setattr(cli, "build_m4b", fake_build_m4b)

    cli._build_issue_audiobook(
        cli.IssueBundlePlan(
            issue=Issue(
                title="Guaio persiano",
                url="https://example.test/issue",
                issue_code="2026-04",
                articles=[
                    Link(
                        title="Editoriale",
                        url="https://example.test/article",
                        order=1,
                    )
                ],
            ),
            issue_dir=issue_dir,
            article_dirs=[article_dir],
        ),
        root_output_dir=output_dir,
        config=AppConfig(output_dir=output_dir),
        cover_image_path=None,
        filename_settings=AudiobookFilenameSettings(),
    )

    assert packaged[0]["chapters"][0].audio_path == article_dir / "01-editoriale.m4a"


def test_issue_audiobook_uses_external_audiobook_output_dir(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    output_dir = tmp_path / "exports"
    external_audiobook_dir = tmp_path / "books"
    issue_dir = output_dir / "library" / "rivista" / "2026-04-guaio-persiano"
    article_dir = issue_dir / "01-l-editoriale" / "01-editoriale"
    article_dir.mkdir(parents=True)
    (article_dir / "01-editoriale.m4a").write_bytes(b"audio")
    built_paths: list[Path] = []

    def fake_build_m4b(
        output_path: Path,
        *,
        title: str,
        chapters: list[object],
        cover_image_path: Path | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Path:
        del title, chapters, cover_image_path, metadata
        built_paths.append(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("book", encoding="utf-8")
        return output_path

    monkeypatch.setattr(cli, "build_m4b", fake_build_m4b)

    cli._build_issue_audiobook(
        cli.IssueBundlePlan(
            issue=Issue(
                title="Guaio persiano",
                url="https://example.test/issue",
                issue_code="2026-04",
                articles=[
                    Link(
                        title="Editoriale",
                        url="https://example.test/article",
                        order=1,
                    )
                ],
            ),
            issue_dir=issue_dir,
            article_dirs=[article_dir],
        ),
        root_output_dir=output_dir,
        config=AppConfig(output_dir=output_dir, audiobook_output_dir=external_audiobook_dir),
        cover_image_path=None,
        filename_settings=AudiobookFilenameSettings(),
    )

    assert built_paths == [external_audiobook_dir / "domino-2026-04-guaio-persiano.m4b"]


def test_legacy_audio_output_path_uses_root_audio_for_feed_articles(tmp_path: Path) -> None:
    output_dir = tmp_path / "exports"
    article_dir = (
        output_dir / "library" / "la-settimana-di-domino" / "2026-04-21-e-la-casa-bianca-rest-sola"
    )

    legacy_path = cli._legacy_audio_output_path(
        article_dir,
        root_output_dir=output_dir,
        audio_format="m4a",
    )

    assert legacy_path == output_dir / "audio" / "2026-04-21-e-la-casa-bianca-rest-sola.m4a"


def test_refresh_issue_metadata_updates_article_and_issue_sidecars(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    issue_url = "https://www.rivistadomino.it/prodotto/guaio-persiano/?sfoglia=1"
    article_url = "https://www.rivistadomino.it/blog/2026/04/21/e-la-casa-bianca-resto-sola"
    issue_dir = tmp_path / "exports" / "library" / "rivista" / "2026-04-guaio-persiano"
    article_dir = issue_dir / "01-la-guerra-va-male" / "01-e-la-casa-bianca-rest-sola"
    article_dir.mkdir(parents=True)
    write_manifest(tmp_path / "exports" / "library" / "rivista", {article_url: str(article_dir)})
    session = FakeSession(
        {
            "https://www.rivistadomino.it/mio-account/my_domino/": f"""
            <a href="{issue_url}">4/2026 Guaio persiano</a>
            """,
            "https://www.rivistadomino.it/prodotto/guaio-persiano?sfoglia=1": f"""
            <article class="product">
              <div class="summary">
                <h1 class="product_title">Guaio persiano</h1>
                <p>4/2026 Guaio persiano La crisi spiegata</p>
              </div>
              <div id="tab-articles">
                <h3>La guerra va male</h3>
                <a class="article_title" href="{article_url}">E la Casa Bianca restò sola</a>
                <div class="article_byline">Lorenzo Maria Ricci</div>
              </div>
            </article>
            """,
            article_url: """
            <html>
              <head><meta name="author" content="Lorenzo Maria Ricci"></head>
              <body>
                <article>
                  <h1>E la Casa Bianca restò sola</h1>
                  <div class="article_byline">
                    <a href="/blog/author/l-m-ricci/">Lorenzo Maria Ricci</a>
                  </div>
                  <p>Corpo.</p>
                </article>
              </body>
            </html>
            """,
        }
    )
    config = AppConfig(
        output_dir=tmp_path / "exports",
        auth_session_path=tmp_path / "missing-session.json",
    )
    monkeypatch.setattr(
        cli, "WebClient", lambda loaded_config: WebClient(loaded_config, session=session)
    )

    result = cli._handle_refresh_issue_metadata(
        config,
        root_output_dir=config.output_dir,
        all_issues=False,
        issue_selector="2026-04",
    )

    assert result == 0
    manifest = read_manifest(tmp_path / "exports" / "library" / "rivista")
    metadata_path = Path(manifest[article_url]) / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["author"] == "Lorenzo Maria Ricci"
    issue_sidecar = json.loads((issue_dir / "issue.json").read_text(encoding="utf-8"))
    assert issue_sidecar["contributors"] == ["Lorenzo Maria Ricci"]
    assert issue_sidecar["articles"][0]["author"] == "Lorenzo Maria Ricci"
    assert issue_sidecar["chapters"][0]["section"] == "La guerra va male"
    assert issue_sidecar["chapters"][0]["author"] == "Lorenzo Maria Ricci"
    issue_metadata = json.loads((issue_dir / "metadata.json").read_text(encoding="utf-8"))
    assert issue_metadata["articles"][0]["author"] == "Lorenzo Maria Ricci"
    assert issue_metadata["chapters"][0]["section"] == "La guerra va male"
    assert issue_metadata["chapters"][0]["title"] == "E la Casa Bianca restò sola"


def test_refresh_issue_metadata_falls_back_to_issue_index_author(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    issue_url = "https://www.rivistadomino.it/prodotto/guaio-persiano/?sfoglia=1"
    article_url = "https://www.rivistadomino.it/blog/2026/04/21/e-la-casa-bianca-resto-sola"
    issue_dir = tmp_path / "exports" / "library" / "rivista" / "2026-04-guaio-persiano"
    article_dir = issue_dir / "02-la-guerra-va-male" / "02-2026-04-21-e-la-casa-bianca-rest-sola"
    article_dir.mkdir(parents=True)
    write_manifest(tmp_path / "exports" / "library" / "rivista", {article_url: str(article_dir)})
    session = FakeSession(
        {
            "https://www.rivistadomino.it/mio-account/my_domino/": f"""
            <a href="{issue_url}">4/2026 Guaio persiano</a>
            """,
            "https://www.rivistadomino.it/prodotto/guaio-persiano?sfoglia=1": f"""
            <article class="product">
              <div class="summary">
                <h1 class="product_title">Guaio persiano</h1>
              </div>
              <div id="tab-articles">
                <h3>La guerra va male</h3>
                <a class="article_title" href="{article_url}">E la Casa Bianca restò sola</a>
                <div class="article_byline">Lorenzo Maria Ricci</div>
              </div>
            </article>
            """,
            article_url: """
            <html>
              <body>
                <article>
                  <h1>E la Casa Bianca restò sola</h1>
                  <p>Corpo.</p>
                </article>
              </body>
            </html>
            """,
        }
    )
    config = AppConfig(
        output_dir=tmp_path / "exports",
        auth_session_path=tmp_path / "missing-session.json",
    )
    monkeypatch.setattr(
        cli, "WebClient", lambda loaded_config: WebClient(loaded_config, session=session)
    )

    result = cli._handle_refresh_issue_metadata(
        config,
        root_output_dir=config.output_dir,
        all_issues=False,
        issue_selector="2026-04",
    )

    assert result == 0
    manifest = read_manifest(tmp_path / "exports" / "library" / "rivista")
    metadata_path = Path(manifest[article_url]) / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["author"] == "Lorenzo Maria Ricci"


def test_repackage_audiobook_refreshes_metadata_before_packaging(tmp_path: Path) -> None:
    issue = Issue(
        title="Guaio persiano",
        url="https://example.test/issue",
        issue_code="2026-04",
        articles=[],
    )
    plan = cli.IssueBundlePlan(
        issue=issue,
        issue_dir=tmp_path / "exports" / "library" / "rivista" / "2026-04-guaio-persiano",
        article_dirs=[],
    )
    events: list[str] = []

    class DummyClient:
        pass

    def fake_selected_issue_details_or_error(
        client: object,
        *,
        all_issues: bool,
        issue_selector: str | None,
        output_dir: Path | None = None,
    ) -> list[Issue]:
        del client, all_issues, issue_selector, output_dir
        return [issue]

    def fake_refresh_downloaded_issue_metadata(
        client: object,
        refreshed_issue: Issue,
        *,
        output_dir: Path,
    ) -> tuple[cli.IssueBundlePlan, Path | None]:
        del client, refreshed_issue, output_dir
        events.append("refresh")
        return plan, tmp_path / "cover.png"

    def fake_build_issue_audiobook(
        packaged_plan: cli.IssueBundlePlan,
        *,
        root_output_dir: Path,
        config: AppConfig,
        cover_image_path: Path | None,
        filename_settings: AudiobookFilenameSettings,
    ) -> Path:
        del packaged_plan, root_output_dir, config, cover_image_path, filename_settings
        events.append("package")
        return tmp_path / "exports" / "audiobooks" / "2026-04-guaio-persiano.m4b"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cli, "WebClient", lambda config: DummyClient())
    monkeypatch.setattr(
        cli, "_selected_issue_details_or_error", fake_selected_issue_details_or_error
    )
    monkeypatch.setattr(
        cli, "_refresh_downloaded_issue_metadata", fake_refresh_downloaded_issue_metadata
    )
    monkeypatch.setattr(cli, "_build_issue_audiobook", fake_build_issue_audiobook)
    try:
        result = cli._handle_repackage_audiobook(
            AppConfig(output_dir=tmp_path / "exports"),
            root_output_dir=tmp_path / "exports",
            all_issues=False,
            issue_selector="2026-04",
            filename_settings=AudiobookFilenameSettings(),
        )
    finally:
        monkeypatch.undo()

    assert result == 0
    assert events == ["refresh", "package"]


def test_refresh_issue_metadata_all_uses_only_downloaded_issues(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    downloaded_issue = Issue(
        title="Guaio persiano",
        url="https://example.test/issues/2026-04",
        issue_code="2026-04",
        articles=[],
    )
    missing_issue = Issue(
        title="Assalto all'Iran",
        url="https://example.test/issues/2026-03",
        issue_code="2026-03",
        articles=[],
    )
    output_dir = tmp_path / "exports" / "library" / "rivista"
    (output_dir / "2026-04-guaio-persiano").mkdir(parents=True)
    refreshed: list[str] = []

    class FakeWebClient:
        def __init__(self, config: AppConfig) -> None:
            del config

        def discover_issues(self) -> list[Link]:
            return [
                Link(title="4/2026 Guaio persiano", url=downloaded_issue.url),
                Link(title="3/2026 Assalto all'Iran", url=missing_issue.url),
            ]

        def discover_issue(self, url: str) -> Issue:
            if url == downloaded_issue.url:
                return downloaded_issue
            if url == missing_issue.url:
                return missing_issue
            raise AssertionError(f"unexpected url {url}")

    def fake_refresh(client: WebClient, issue: Issue, *, output_dir: Path) -> tuple[object, object]:
        del client, output_dir
        refreshed.append(issue.issue_code or "")
        return object(), None

    monkeypatch.setattr(cli, "WebClient", FakeWebClient)
    monkeypatch.setattr(cli, "_refresh_downloaded_issue_metadata", fake_refresh)

    result = cli._handle_refresh_issue_metadata(
        AppConfig(output_dir=tmp_path / "exports"),
        root_output_dir=tmp_path / "exports",
        all_issues=True,
        issue_selector=None,
    )

    assert result == 0
    assert refreshed == ["2026-04"]


def test_repackage_audiobook_all_uses_only_downloaded_issues(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    downloaded_issue = Issue(
        title="Guaio persiano",
        url="https://example.test/issues/2026-04",
        issue_code="2026-04",
        articles=[],
    )
    missing_issue = Issue(
        title="Assalto all'Iran",
        url="https://example.test/issues/2026-03",
        issue_code="2026-03",
        articles=[],
    )
    output_dir = tmp_path / "exports" / "library" / "rivista"
    issue_dir = output_dir / "2026-04-guaio-persiano"
    issue_dir.mkdir(parents=True)
    packaged: list[str] = []

    class FakeWebClient:
        def __init__(self, config: AppConfig) -> None:
            del config

        def discover_issues(self) -> list[Link]:
            return [
                Link(title="4/2026 Guaio persiano", url=downloaded_issue.url),
                Link(title="3/2026 Assalto all'Iran", url=missing_issue.url),
            ]

        def discover_issue(self, url: str) -> Issue:
            if url == downloaded_issue.url:
                return downloaded_issue
            if url == missing_issue.url:
                return missing_issue
            raise AssertionError(f"unexpected url {url}")

    def fake_refresh(
        client: WebClient,
        issue: Issue,
        *,
        output_dir: Path,
    ) -> tuple[cli.IssueBundlePlan, Path | None]:
        del client, output_dir
        return cli.IssueBundlePlan(issue=issue, issue_dir=issue_dir, article_dirs=[]), None

    def fake_build_issue_audiobook(
        plan: cli.IssueBundlePlan,
        *,
        root_output_dir: Path,
        config: AppConfig,
        cover_image_path: Path | None,
        filename_settings: AudiobookFilenameSettings,
    ) -> Path:
        del root_output_dir, config, cover_image_path, filename_settings
        packaged.append(plan.issue.issue_code or "")
        return issue_dir / "book.m4b"

    monkeypatch.setattr(cli, "WebClient", FakeWebClient)
    monkeypatch.setattr(cli, "_refresh_downloaded_issue_metadata", fake_refresh)
    monkeypatch.setattr(cli, "_build_issue_audiobook", fake_build_issue_audiobook)

    result = cli._handle_repackage_audiobook(
        AppConfig(output_dir=tmp_path / "exports"),
        root_output_dir=tmp_path / "exports",
        all_issues=True,
        issue_selector=None,
        filename_settings=AudiobookFilenameSettings(),
    )

    assert result == 0
    assert packaged == ["2026-04"]


def test_audiobook_output_path_uses_configurable_filename_template() -> None:
    issue = Issue(
        title="Guaio persiano",
        url="https://example.test/issue",
        issue_code="2026-04",
        articles=[],
    )

    output_path = cli._audiobook_output_path(
        Path("/tmp/audiobooks"),
        issue,
        settings=AudiobookFilenameSettings(
            magazine_title="Rivista Domino",
            separator="-",
            format_template="{magazine_slug}{sep}anno-{year}{sep}numero-{number}{sep}{title_slug}",
        ),
    )

    assert output_path.name == "rivista-domino-anno-2026-numero-04-guaio-persiano.m4b"


def test_rename_audiobooks_uses_embedded_metadata(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    old_path = library_dir / "legacy-name.m4b"
    old_path.write_bytes(b"book")

    monkeypatch.setattr(
        cli,
        "read_audiobook_tags",
        lambda path: {"title": "Guaio persiano", "date": "2026-04-21"} if path == old_path else {},
    )

    result = cli._handle_rename_audiobooks(
        AppConfig(output_dir=tmp_path / "exports"),
        paths=[],
        library_dir=library_dir,
        output_dir=tmp_path / "exports",
        filename_settings=AudiobookFilenameSettings(
            magazine_title="Domino",
            separator="-",
            format_template="{magazine_slug}{sep}{year}{sep}{number}{sep}{title_slug}",
        ),
        dry_run=False,
    )

    assert result == 0
    assert not old_path.exists()
    assert (library_dir / "domino-2026-04-guaio-persiano.m4b").exists()


def test_rename_audiobooks_allows_case_only_rename(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    old_path = library_dir / "domino-2026-03-assalto-all-iran.m4b"
    old_path.write_bytes(b"book")

    monkeypatch.setattr(
        cli,
        "read_audiobook_tags",
        lambda path: (
            {"title": "Assalto all Iran", "date": "2026-03-01"} if path == old_path else {}
        ),
    )

    result = cli._handle_rename_audiobooks(
        AppConfig(output_dir=tmp_path / "exports"),
        paths=[old_path],
        library_dir=None,
        output_dir=tmp_path / "exports",
        filename_settings=AudiobookFilenameSettings(
            magazine_title="Domino",
            separator="-",
            format_template="{magazine}{sep}{year}{sep}{number}{sep}{title_slug}",
        ),
        dry_run=False,
    )

    assert result == 0
    assert (library_dir / "Domino-2026-03-assalto-all-iran.m4b").exists()
    names = sorted(path.name for path in library_dir.iterdir())
    assert names == ["Domino-2026-03-assalto-all-iran.m4b"]


def test_resolved_issue_article_dirs_prefers_manifest_paths_for_legacy_issue_tree(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "exports"
    legacy_dir = (
        output_dir
        / "2026-04-guaio-persiano"
        / "01-l-editoriale"
        / "01-2026-04-21-cosa-fare-a-teheran-quando-sei-morto"
    )
    legacy_dir.mkdir(parents=True)
    issue = Issue(
        title="Guaio persiano",
        url="https://example.test/issue",
        issue_code="2026-04",
        articles=[
            Link(
                title="Cosa fare a Teheran quando sei morto",
                url="https://example.test/article",
                order=1,
            )
        ],
    )
    write_manifest(output_dir, {issue.articles[0].url: str(legacy_dir)})

    resolved = cli._resolved_issue_article_dirs(
        issue,
        output_dir=output_dir,
        planned_dirs={
            issue.articles[0].url: (
                output_dir
                / "2026-04-guaio-persiano"
                / "01-l-editoriale"
                / "01-cosa-fare-a-teheran-quando-sei-morto"
            )
        },
    )

    assert resolved == [legacy_dir]


def test_download_issue_all_rejects_ambiguous_selectors(capsys: CaptureFixture[str]) -> None:
    result = cli.main(["download", "--issue", "2026-04", "--article", "1", "--all"])

    captured = capsys.readouterr()

    assert result == 1
    assert "Use either --article or --all with --issue, not both." in captured.err


def test_keyboard_interrupt_prints_clean_message(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    def fake_login(config: AppConfig, *, use_browser: bool) -> int:
        del config, use_browser
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_handle_login", fake_login)

    result = cli.main(["login"])

    captured = capsys.readouterr()
    assert result == 130
    assert captured.err == "interrupted\n"
    assert "Traceback" not in captured.err


def test_explicit_download_reuses_existing_manifest_dir_and_backfills_audio(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    article_url = "https://www.rivistadomino.it/blog/2026/04/21/editoriale"
    existing_dir = tmp_path / "exports" / "001-editoriale"
    existing_dir.mkdir(parents=True)
    (existing_dir / "article.txt").write_text("old", encoding="utf-8")
    (tmp_path / "exports" / "manifest.json").write_text(
        json.dumps({article_url: str(existing_dir)}),
        encoding="utf-8",
    )
    session = FakeSession(
        {
            article_url: "<article><h1>Editoriale</h1><p>Corpo aggiornato.</p></article>",
        }
    )
    config = AppConfig(
        output_dir=tmp_path / "exports",
        auth_session_path=tmp_path / "missing-session.json",
    )
    audio_calls: list[tuple[Path, Path]] = []

    def fake_synthesize_audio(
        source: Path,
        output: Path,
        *,
        voice: str | None,
        audio_format: str,
        timeout: float | None = None,
        progress: object | None = None,
        **kwargs: object,
    ) -> Path:
        del voice, audio_format, timeout, progress, kwargs
        audio_calls.append((source, output))
        output.write_text("audio", encoding="utf-8")
        return output

    monkeypatch.setattr(
        cli, "WebClient", lambda loaded_config: WebClient(loaded_config, session=session)
    )
    monkeypatch.setattr(cli, "synthesize_audio", fake_synthesize_audio)
    clock = iter([0.0, 65.0])
    monkeypatch.setattr("get_my_domino.cli.time.monotonic", lambda: next(clock))

    result = cli._download_articles(
        [article_url],
        config,
        config.output_dir,
        create_audio=True,
        audio_format="m4a",
        audio_timeout=900.0,
    )

    assert result == 0
    assert not (tmp_path / "exports" / "002-editoriale").exists()
    assert (existing_dir / "001-editoriale.html").exists()
    assert "Corpo aggiornato." in (existing_dir / "001-editoriale.txt").read_text(encoding="utf-8")
    assert not (existing_dir / "001-editoriale.rtf").exists()
    assert audio_calls == [
        (
            existing_dir / "001-editoriale.txt",
            existing_dir / "001-editoriale.m4a",
        )
    ]


def test_explicit_download_uses_existing_exports_when_only_audio_is_missing(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    article_url = "https://www.rivistadomino.it/blog/2026/04/21/editoriale"
    existing_dir = tmp_path / "exports" / "001-editoriale"
    existing_dir.mkdir(parents=True)
    (existing_dir / "001-editoriale.html").write_text("<article>old</article>", encoding="utf-8")
    (existing_dir / "001-editoriale.txt").write_text("Titolo\n\nCorpo 日本語.", encoding="utf-8")
    (existing_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (tmp_path / "exports" / "manifest.json").write_text(
        json.dumps({article_url: str(existing_dir)}),
        encoding="utf-8",
    )
    session = FakeSession({article_url: "<article><h1>Should not fetch</h1></article>"})
    config = AppConfig(
        output_dir=tmp_path / "exports",
        auth_session_path=tmp_path / "missing-session.json",
    )
    audio_calls: list[tuple[Path, Path]] = []

    def fake_synthesize_audio(
        source: Path,
        output: Path,
        *,
        voice: str | None,
        audio_format: str,
        timeout: float | None = None,
        progress: object | None = None,
        **kwargs: object,
    ) -> Path:
        del voice, audio_format, timeout, progress, kwargs
        audio_calls.append((source, output))
        output.write_text("audio", encoding="utf-8")
        return output

    monkeypatch.setattr(
        cli, "WebClient", lambda loaded_config: WebClient(loaded_config, session=session)
    )
    monkeypatch.setattr(cli, "synthesize_audio", fake_synthesize_audio)
    clock = iter([0.0, 65.0])
    monkeypatch.setattr("get_my_domino.cli.time.monotonic", lambda: next(clock))

    result = cli._download_articles(
        [article_url],
        config,
        config.output_dir,
        create_audio=True,
        audio_format="m4a",
        audio_timeout=900.0,
    )

    assert result == 0
    captured = capsys.readouterr()
    assert "→ Generating audio 001-editoriale.m4a..." in captured.err
    assert "✓ Generating audio 001-editoriale.m4a" in captured.err
    assert "001-editoriale" in captured.out
    assert "reused" in captured.out
    assert "generated" in captured.out
    assert "01:05" in captured.out
    assert session.gets == []
    assert (existing_dir / "001-editoriale.html").read_text(encoding="utf-8") == (
        "<article>old</article>"
    )
    assert audio_calls == [
        (
            existing_dir / "001-editoriale.txt",
            existing_dir / "001-editoriale.m4a",
        )
    ]


def test_explicit_download_reuses_existing_audio_without_force(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    article_url = "https://www.rivistadomino.it/blog/2026/04/21/editoriale"
    existing_dir = tmp_path / "exports" / "001-editoriale"
    existing_dir.mkdir(parents=True)
    (existing_dir / "001-editoriale.html").write_text("<article>old</article>", encoding="utf-8")
    (existing_dir / "001-editoriale.txt").write_text("Titolo\n\nCorpo.", encoding="utf-8")
    (existing_dir / "metadata.json").write_text("{}", encoding="utf-8")
    audio_path = tmp_path / "exports" / "audio" / "001-editoriale.m4a"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_text("existing audio", encoding="utf-8")
    (tmp_path / "exports" / "manifest.json").write_text(
        json.dumps({article_url: str(existing_dir)}),
        encoding="utf-8",
    )
    session = FakeSession({article_url: "<article><h1>Should not fetch</h1></article>"})
    config = AppConfig(
        output_dir=tmp_path / "exports",
        auth_session_path=tmp_path / "missing-session.json",
    )
    audio_calls: list[tuple[Path, Path]] = []

    def fake_synthesize_audio(
        source: Path,
        output: Path,
        *,
        voice: str | None,
        audio_format: str,
        timeout: float | None = None,
        progress: object | None = None,
        **kwargs: object,
    ) -> Path:
        del voice, audio_format, timeout, progress, kwargs
        audio_calls.append((source, output))
        output.write_text("new audio", encoding="utf-8")
        return output

    monkeypatch.setattr(
        cli, "WebClient", lambda loaded_config: WebClient(loaded_config, session=session)
    )
    monkeypatch.setattr(cli, "synthesize_audio", fake_synthesize_audio)
    clock = iter([0.0, 65.0])
    monkeypatch.setattr("get_my_domino.cli.time.monotonic", lambda: next(clock))

    result = cli._download_articles(
        [article_url],
        config,
        config.output_dir,
        create_audio=True,
        audio_format="m4a",
        audio_timeout=900.0,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert session.gets == []
    assert audio_calls == []
    assert (existing_dir / "001-editoriale.m4a").read_text(encoding="utf-8") == "existing audio"
    assert "✓ 001-editoriale" in captured.out
    assert captured.out.count("reused") == 2
    assert "if this audio file is corrupt" not in captured.err


def test_explicit_download_force_regenerates_existing_audio(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    article_url = "https://www.rivistadomino.it/blog/2026/04/21/editoriale"
    existing_dir = tmp_path / "exports" / "001-editoriale"
    existing_dir.mkdir(parents=True)
    (existing_dir / "001-editoriale.html").write_text("<article>old</article>", encoding="utf-8")
    (existing_dir / "001-editoriale.txt").write_text("Titolo\n\nCorpo.", encoding="utf-8")
    (existing_dir / "metadata.json").write_text("{}", encoding="utf-8")
    audio_path = tmp_path / "exports" / "audio" / "001-editoriale.m4a"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_text("existing audio", encoding="utf-8")
    (tmp_path / "exports" / "manifest.json").write_text(
        json.dumps({article_url: str(existing_dir)}),
        encoding="utf-8",
    )
    session = FakeSession(
        {article_url: "<article><h1>Editoriale</h1><p>Corpo aggiornato.</p></article>"}
    )
    config = AppConfig(
        output_dir=tmp_path / "exports",
        auth_session_path=tmp_path / "missing-session.json",
    )
    audio_calls: list[tuple[Path, Path]] = []

    def fake_synthesize_audio(
        source: Path,
        output: Path,
        *,
        voice: str | None,
        audio_format: str,
        timeout: float | None = None,
        progress: object | None = None,
        **kwargs: object,
    ) -> Path:
        del voice, audio_format, timeout, progress, kwargs
        audio_calls.append((source, output))
        output.write_text("new audio", encoding="utf-8")
        return output

    monkeypatch.setattr(
        cli, "WebClient", lambda loaded_config: WebClient(loaded_config, session=session)
    )
    monkeypatch.setattr(cli, "synthesize_audio", fake_synthesize_audio)

    result = cli._download_articles(
        [article_url],
        config,
        config.output_dir,
        create_audio=True,
        audio_format="m4a",
        audio_timeout=900.0,
        force=True,
    )

    assert result == 0
    expected_audio_call = (existing_dir / "001-editoriale.txt", existing_dir / "001-editoriale.m4a")
    assert audio_calls == [expected_audio_call]
    assert (existing_dir / "001-editoriale.m4a").read_text(encoding="utf-8") == "new audio"


def test_explicit_download_force_rewrites_existing_export(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    article_url = "https://www.rivistadomino.it/blog/2026/04/21/editoriale"
    existing_dir = tmp_path / "exports" / "001-editoriale"
    existing_dir.mkdir(parents=True)
    (existing_dir / "article.html").write_text("<article>old</article>", encoding="utf-8")
    (existing_dir / "article.txt").write_text("old", encoding="utf-8")
    (existing_dir / "article.rtf").write_text(r"{\rtf1 old}", encoding="ascii")
    (existing_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (tmp_path / "exports" / "manifest.json").write_text(
        json.dumps({article_url: str(existing_dir)}),
        encoding="utf-8",
    )
    session = FakeSession(
        {
            article_url: "<article><h1>Editoriale</h1><p>Corpo forzato.</p></article>",
        }
    )
    config = AppConfig(
        output_dir=tmp_path / "exports",
        auth_session_path=tmp_path / "missing-session.json",
    )
    monkeypatch.setattr(
        cli, "WebClient", lambda loaded_config: WebClient(loaded_config, session=session)
    )

    result = cli._download_articles(
        [article_url],
        config,
        config.output_dir,
        create_audio=False,
        audio_format="m4a",
        audio_timeout=900.0,
        force=True,
    )

    assert result == 0
    assert session.gets == [article_url]
    assert "Corpo forzato." in (existing_dir / "001-editoriale.txt").read_text(encoding="utf-8")
    assert not (existing_dir / "article.txt").exists()
    assert not (existing_dir / "article.html").exists()
    assert not (existing_dir / "article.rtf").exists()


def test_main_help_explains_catalog_without_redundant_aliases() -> None:
    help_text = cli.format_main_help()

    assert "catalog       Browse readable issue and feed indexes" in help_text
    assert "voices        List macOS say voices available for audio" in help_text
    assert "issues    Raw issue URL list" in help_text
    assert "articles  Raw article URL list for one issue" in help_text
    assert "browse" not in help_text
    assert "toc" not in help_text
    assert "contents" not in help_text


def test_catalog_title_cleanup_does_not_strip_europei() -> None:
    issue_code, title, synopsis = cli._issue_summary_parts(
        "8/2025 Europei brava gente 7,50 € - 10,00 € Fascia di prezzo: da 7,50 € a 10,00 € Sinossi."
    )

    assert issue_code == "2025-08"
    assert title == "Europei brava gente"
    assert synopsis == "Sinossi."


def test_article_titles_use_colored_terminal_style(monkeypatch: MonkeyPatch) -> None:
    class FakeStdout:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdout", FakeStdout())
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("CLICOLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    assert cli._style_article_title("Titolo") == "\033[36mTitolo\033[0m"


def test_article_titles_do_not_style_when_color_is_disabled(monkeypatch: MonkeyPatch) -> None:
    class FakeStdout:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdout", FakeStdout())
    monkeypatch.setenv("NO_COLOR", "1")

    assert cli._style_article_title("Titolo") == "Titolo"


def test_audio_progress_step_plain_output_suppresses_aiff_growth(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)

    with cli._audio_progress_step("Generating audio article.m4a") as progress:
        progress("chunking", None, 4)
        progress("aiff_growth", None, 4096)
        progress("retrying", None, 1)
        progress("converting", None, None)

    captured = capsys.readouterr()
    assert "→ Generating audio article.m4a..." in captured.err
    assert "chunks: 4" in captured.err
    assert "retry: attempt 2" in captured.err
    assert "converting: final audio format" in captured.err
    assert "aiff:" not in captured.err
    assert "✓ Generating audio article.m4a" in captured.err


def test_folder_names_match_feed_and_issue_conventions() -> None:
    issue_link = Link(
        title="Cosa fare a Teheran quando sei morto",
        url="https://www.rivistadomino.it/blog/2026/04/21/cosa-fare/",
        group="L'Editoriale",
        published_date="2026-04-21",
        order=1,
    )
    feed_link = Link(
        title="Usa e globalizzazione",
        url="https://www.rivistadomino.it/blog/2026/04/24/usa-e-globalizzazione/",
        published_date="2026-04-24",
    )

    assert cli._issue_folder_name("Guaio persiano", "2026-04") == "2026-04-guaio-persiano"
    assert cli._group_folder_name("L'Editoriale", 1) == "01-l-editoriale"
    assert cli._group_indexes([issue_link, issue_link]) == {"L'Editoriale": 1}
    assert cli._group_indexes([issue_link, feed_link]) == {"L'Editoriale": 1, "Articoli": 2}
    assert (
        cli._article_folder_name(issue_link, fallback_index=99)
        == "01-cosa-fare-a-teheran-quando-sei-morto"
    )
    assert cli._feed_article_folder_name(feed_link) == "2026-04-24-usa-e-globalizzazione"


def test_extract_links_keeps_matching_links_in_order() -> None:
    html = """
    <a href="/numero-1/">Numero 1</a>
    <a href="/prodotto/guaio-persiano/?sfoglia=1">Sfoglia</a>
    <a href="/privacy/">Privacy</a>
    <a href="/numero-2/">Numero 2</a>
    """

    links = extract_links(
        html,
        page_url="https://example.test/",
        include_patterns=("numero", "?sfoglia=1"),
        skip_patterns=("privacy",),
    )

    assert [link.title for link in links] == ["Numero 1", "Sfoglia", "Numero 2"]
    assert links[0].url == "https://example.test/numero-1"
    assert links[1].url == "https://example.test/prodotto/guaio-persiano?sfoglia=1"


def test_extract_article_removes_noise_and_keeps_article_text() -> None:
    html = """
    <html>
      <head><title>Fallback</title></head>
      <body>
        <nav>Menu</nav>
        <article>
          <h1>Titolo articolo</h1>
          <p>Primo paragrafo.</p>
          <aside>Pubblicita</aside>
          <p>Secondo paragrafo.</p>
        </article>
      </body>
    </html>
    """

    article = extract_article(
        html,
        page_url="https://example.test/articolo/",
        content_selectors=("article",),
    )

    assert article.title == "Titolo articolo"
    assert "Primo paragrafo." in article.text
    assert "Secondo paragrafo." in article.text
    assert "Pubblicita" not in article.text


def test_extract_article_removes_domino_header_images() -> None:
    html = """
    <html>
      <head><title>Fallback</title></head>
      <body>
        <article class="post has-post-thumbnail">
          <a class="post-thumbnail" href="/blog/2026/04/24/example/">
            <img class="attachment-post-thumbnail wp-post-image"
                 src="https://www.rivistadomino.it/wp-content/uploads/header.jpeg"
                 alt="Header Domino">
          </a>
          <div class="entry-content">
            <h2>Titolo articolo</h2>
            <p>Primo paragrafo.</p>
            <p>
              <img class="size-full wp-image-612 aligncenter"
                   src="https://www.rivistadomino.it/wp-content/uploads/2023/08/domini-trasparente.png"
                   alt="">
            </p>
            <p>Secondo paragrafo.</p>
          </div>
        </article>
      </body>
    </html>
    """

    article = extract_article(
        html,
        page_url="https://example.test/articolo/",
        content_selectors=("article",),
    )

    assert article.title == "Titolo articolo"
    assert "Primo paragrafo." in article.text
    assert "Secondo paragrafo." in article.text
    assert 'class="post-thumbnail"' not in article.html
    assert "wp-post-image" not in article.html
    assert "domini-trasparente.png" not in article.html
    assert "Header Domino" not in article.text


def test_extract_article_reads_byline_author() -> None:
    article = extract_article(
        "<article><h1>Titolo</h1><p class='byline'>di Dario Fabbri</p><p>Corpo.</p></article>",
        page_url="https://example.test/articolo/",
        content_selectors=("article",),
    )

    assert article.author == "Dario Fabbri"


def test_extract_article_prefers_meta_author_over_site_header_text() -> None:
    article = extract_article(
        """
        <html>
          <head>
            <meta name="author" content="Lorenzo Maria Ricci">
            <title>Titolo - Rivista Domino</title>
          </head>
          <body>
            <header><span>diretta da Dario Fabbri</span></header>
            <article>
              <h1>Titolo</h1>
              <div class="article_byline">
                <a href="/blog/author/l-m-ricci/">Lorenzo Maria Ricci</a>
              </div>
              <p>Corpo.</p>
            </article>
          </body>
        </html>
        """,
        page_url="https://example.test/articolo/",
        content_selectors=("article",),
    )

    assert article.author == "Lorenzo Maria Ricci"


def test_extract_article_reads_adjacent_article_byline() -> None:
    article = extract_article(
        """
        <article>
          <h1>E la Casa Bianca resto sola</h1>
          <div class="article_byline">
            <a href="/blog/author/l-m-ricci/">Lorenzo Maria Ricci</a>
          </div>
          <p>Corpo.</p>
        </article>
        """,
        page_url="https://example.test/articolo/",
        content_selectors=("article",),
    )

    assert article.author == "Lorenzo Maria Ricci"


def test_extract_article_accepts_initials_in_explicit_author_fields() -> None:
    article = extract_article(
        """
        <html>
          <head>
            <meta name="author" content="Z. Goggi">
          </head>
          <body>
            <article>
              <h1>Iran, ennesimo equivoco</h1>
              <div class="article_byline"><a href="/blog/author/z-goggi/">Z. Goggi</a></div>
              <p><em>Libro dei mutamenti</em> non e autore.</p>
            </article>
          </body>
        </html>
        """,
        page_url="https://example.test/articolo/",
        content_selectors=("article",),
    )

    assert article.author == "Z. Goggi"


def test_extract_article_normalizes_domino_directed_by_author() -> None:
    article = extract_article(
        "<article><h1>Titolo</h1><p>diretta da Dario Fabbri</p><p>Corpo.</p></article>",
        page_url="https://example.test/articolo/",
        content_selectors=("article",),
    )

    assert article.author == "Dario Fabbri"


def test_extract_article_removes_domino_site_suffix_from_title() -> None:
    article = extract_article(
        (
            "<html><head><title>Dove Mosca regna - Rivista Domino</title></head>"
            "<body><main><p>Corpo.</p></main></body></html>"
        ),
        page_url="https://example.test/articolo/",
        content_selectors=("article", "main"),
    )

    assert article.title == "Dove Mosca regna"


def test_article_text_document_omits_url_and_formats_spoken_header() -> None:
    article = Article(
        title="Cosa fare a Teheran quando sei morto - Rivista Domino",
        url="https://example.test/articolo/",
        html="<article></article>",
        text="Corpo con Москва e 日本語.",
        issue_title="Guaio persiano",
        author="Dario Fabbri",
    )

    text = article_text_document(article)

    assert text.startswith(
        "Guaio persiano\nCosa fare a Teheran quando sei morto\ndi Dario Fabbri\n\n"
    )
    assert "https://example.test/articolo/" not in text


def test_write_article_creates_html_text_and_metadata(tmp_path: Path) -> None:
    article = Article(
        title="Titolo articolo 日本語",
        url="https://example.test/articolo/",
        html="<article><h1>Titolo articolo</h1></article>",
        text="Titolo articolo\n\nCorpo con Москва e 日本語.",
    )

    target_dir = write_article(tmp_path, article, index=1, export_formats=("html", "txt"))

    assert target_dir.name == "001-titolo-articolo"
    assert (target_dir / "001-titolo-articolo.html").exists()
    article_text = (target_dir / "001-titolo-articolo.txt").read_text(encoding="utf-8")
    assert article_text.startswith("Titolo articolo 日本語\n\n")
    assert "https://example.test/articolo/" not in article_text
    assert not (target_dir / "001-titolo-articolo.rtf").exists()
    metadata = json.loads((target_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["title"] == "Titolo articolo 日本語"
    assert metadata["url"] == "https://example.test/articolo/"
    assert "text" not in metadata
    assert "html" not in metadata


def test_write_article_can_create_rtf_when_requested(tmp_path: Path) -> None:
    article = Article(
        title="Titolo articolo 日本語",
        url="https://example.test/articolo/",
        html="<article><h1>Titolo articolo</h1></article>",
        text="Corpo con Москва e 日本語.",
    )

    target_dir = write_article(tmp_path, article, index=1, export_formats=("rtf",))

    rtf = (target_dir / "001-titolo-articolo.rtf").read_text(encoding="ascii")
    assert rtf.startswith(r"{\rtf1")
    assert r"\u26085?" in rtf
    assert r"\u26412?" in rtf
    assert r"\u1052?" in rtf
    assert not (target_dir / "001-titolo-articolo.html").exists()
    assert not (target_dir / "001-titolo-articolo.txt").exists()


def test_audio_cli_options_default_to_config_and_allow_overrides() -> None:
    parser = cli.build_parser()
    config = AppConfig(
        audio_auto=True,
        audio_format="m4a",
        audio_timeout=123.0,
        audio_chunked=True,
        audio_chunk_chars=2500,
        audio_chunk_concurrency=3,
        audio_chunk_retries=2,
        audio_stall_timeout=45.0,
    )

    sync_args = parser.parse_args(["sync-magazine"])
    sync_options = cli._audio_options(sync_args, config)
    assert sync_options.create is True
    assert sync_options.audio_format == "m4a"
    assert sync_options.timeout == 123.0
    assert sync_options.chunked is True
    assert sync_options.chunk_chars == 2500
    assert sync_options.concurrency == 3
    assert sync_options.retries == 2
    assert sync_options.stall_timeout == 45.0

    feed_args = parser.parse_args(["sync-feed", "--no-audio"])
    assert cli._audio_options(feed_args, config).create is False

    audiobook_no_audio_args = parser.parse_args(
        ["download", "--issue", "2026-04", "--all", "--audiobook", "--no-audio"]
    )
    audiobook_no_audio_options = cli._audio_options(audiobook_no_audio_args, config)
    assert audiobook_no_audio_options.create is False


def test_audiobook_request_defaults_to_config_and_allows_cli_override() -> None:
    parser = cli.build_parser()
    config = AppConfig(audiobook_auto=True)

    sync_args = parser.parse_args(["sync-magazine"])
    assert cli._audiobook_requested(sync_args, config) is True

    sync_no_audio_args = parser.parse_args(["sync-magazine", "--no-audio"])
    assert cli._audiobook_requested(sync_no_audio_args, config) is False

    explicit_args = parser.parse_args(["sync-magazine", "--audiobook"])
    assert cli._audiobook_requested(explicit_args, AppConfig()) is True

    explicit_no_audio_args = parser.parse_args(["sync-magazine", "--audiobook", "--no-audio"])
    assert cli._audiobook_requested(explicit_no_audio_args, AppConfig()) is False

    download_args = parser.parse_args(
        [
            "download",
            "https://example.test/a",
            "--audio",
            "--audio-format",
            "mp3",
            "--audio-timeout",
            "45",
            "--audio-jobs",
            "4",
            "--audio-chunk-chars",
            "3000",
            "--audio-retries",
            "1",
            "--audio-stall-timeout",
            "30",
        ]
    )
    download_options = cli._audio_options(download_args, AppConfig())
    assert download_options.create is True
    assert download_options.audio_format == "mp3"
    assert download_options.timeout == 45.0
    assert download_options.chunked is True
    assert download_options.chunk_chars == 3000
    assert download_options.concurrency == 4
    assert download_options.retries == 1
    assert download_options.stall_timeout == 30.0


def test_sync_feed_output_shows_destination_folder_and_article_paths(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    article_url = "https://www.rivistadomino.it/blog/2026/03/20/guerra-in-iran/"

    class FakeWebClient:
        def __init__(self, config: AppConfig) -> None:
            del config

        def download_article(self, url: str) -> Article:
            assert url == article_url
            return Article(
                title="Che succede in Medio Oriente",
                url=url,
                html="<article>Test</article>",
                text="Test",
            )

    monkeypatch.setattr(cli, "WebClient", FakeWebClient)

    result = cli._download_new_articles(
        [Link(title="Che succede in Medio Oriente", url=article_url)],
        config=AppConfig(output_dir=tmp_path, verbose=True),
        output_dir=tmp_path / "la-settimana-di-domino",
        create_audio=False,
        audio_format="m4a",
        audio_timeout=900.0,
        export_formats=("txt",),
        max_articles=None,
    )

    captured = capsys.readouterr()

    assert result == 0
    assert f"folder: {tmp_path / 'la-settimana-di-domino'}" in captured.out
    assert "Che succede in Medio Oriente" in captured.out
    assert "written" in captured.out
    assert "off" in captured.out
    assert (
        str(tmp_path / "la-settimana-di-domino" / "2026-03-20-che-succede-in-medio-oriente")
        in captured.out
    )


def test_sync_feed_audio_includes_existing_manifest_articles(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "la-settimana-di-domino"
    existing_dir = output_dir / "2026-03-20-che-succede-in-medio-oriente"
    existing_dir.mkdir(parents=True)
    (existing_dir / "article.txt").write_text("Test", encoding="utf-8")
    article_url = "https://www.rivistadomino.it/blog/2026/03/20/guerra-in-iran/"
    write_manifest(output_dir, {article_url: str(existing_dir)})
    spoken: list[Path] = []
    speak_kwargs: list[dict[str, object]] = []

    class FakeWebClient:
        def __init__(self, config: AppConfig) -> None:
            del config

        def download_article(self, url: str) -> Article:
            raise AssertionError(f"should not redownload existing article: {url}")

    def fake_speak_paths(paths: list[Path], **kwargs: object) -> int:
        speak_kwargs.append(kwargs)
        spoken.extend(paths)
        return 0

    monkeypatch.setattr(cli, "WebClient", FakeWebClient)
    monkeypatch.setattr(cli, "_speak_paths", fake_speak_paths)

    result = cli._download_new_articles(
        [Link(title="Che succede in Medio Oriente", url=article_url)],
        config=AppConfig(output_dir=tmp_path, verbose=True),
        output_dir=output_dir,
        create_audio=True,
        audio_format="m4a",
        audio_timeout=900.0,
        export_formats=("txt",),
        max_articles=None,
    )

    captured = capsys.readouterr()

    assert result == 0
    assert spoken == [existing_dir]
    assert speak_kwargs[0]["force"] is False
    assert "reused" in captured.out
    assert "pending" in captured.out
    assert str(existing_dir) in captured.out
    assert "new_articles: 0" in captured.out


def test_sync_feed_audio_existing_articles_respects_max_articles_and_force(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    output_dir = tmp_path / "la-settimana-di-domino"
    first_dir = output_dir / "2026-03-20-first"
    second_dir = output_dir / "2026-03-13-second"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)
    first_url = "https://www.rivistadomino.it/blog/2026/03/20/first/"
    second_url = "https://www.rivistadomino.it/blog/2026/03/13/second/"
    write_manifest(output_dir, {first_url: str(first_dir), second_url: str(second_dir)})
    spoken: list[Path] = []
    speak_kwargs: list[dict[str, object]] = []

    class FakeWebClient:
        def __init__(self, config: AppConfig) -> None:
            del config

        def download_article(self, url: str) -> Article:
            raise AssertionError(f"should not redownload existing article: {url}")

    def fake_speak_paths(paths: list[Path], **kwargs: object) -> int:
        spoken.extend(paths)
        speak_kwargs.append(kwargs)
        return 0

    monkeypatch.setattr(cli, "WebClient", FakeWebClient)
    monkeypatch.setattr(cli, "_speak_paths", fake_speak_paths)

    result = cli._download_new_articles(
        [
            Link(title="First", url=first_url),
            Link(title="Second", url=second_url),
        ],
        config=AppConfig(output_dir=tmp_path),
        output_dir=output_dir,
        create_audio=True,
        audio_format="m4a",
        audio_timeout=900.0,
        export_formats=("txt",),
        max_articles=1,
        force=False,
    )

    assert result == 0
    assert spoken == [first_dir]
    assert speak_kwargs[0]["force"] is False


def test_sync_feed_force_redownloads_and_forces_audio_regeneration(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    output_dir = tmp_path / "la-settimana-di-domino"
    existing_dir = output_dir / "2026-03-20-first"
    existing_dir.mkdir(parents=True)
    article_url = "https://www.rivistadomino.it/blog/2026/03/20/first/"
    write_manifest(output_dir, {article_url: str(existing_dir)})
    spoken: list[Path] = []
    speak_kwargs: list[dict[str, object]] = []
    downloaded: list[str] = []

    class FakeWebClient:
        def __init__(self, config: AppConfig) -> None:
            del config

        def download_article(self, url: str) -> Article:
            downloaded.append(url)
            return Article(
                title="First",
                url=url,
                html="<article>Updated</article>",
                text="Updated",
            )

    def fake_speak_paths(paths: list[Path], **kwargs: object) -> int:
        spoken.extend(paths)
        speak_kwargs.append(kwargs)
        return 0

    monkeypatch.setattr(cli, "WebClient", FakeWebClient)
    monkeypatch.setattr(cli, "_speak_paths", fake_speak_paths)

    result = cli._download_new_articles(
        [Link(title="First", url=article_url)],
        config=AppConfig(output_dir=tmp_path),
        output_dir=output_dir,
        create_audio=True,
        audio_format="m4a",
        audio_timeout=900.0,
        export_formats=("txt",),
        max_articles=None,
        force=True,
    )

    assert result == 0
    assert downloaded == [article_url]
    assert spoken == [existing_dir]
    assert speak_kwargs[0]["force"] is True


def test_sync_magazine_uses_tabular_output(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    issue_link = Link(title="4/2026 Guaio persiano", url="https://example.test/issue")
    article_link = Link(
        title="E la Casa Bianca restò sola",
        url="https://example.test/article",
        group="La guerra va male",
        order=2,
    )
    issue = Issue(
        title="Guaio persiano",
        url=issue_link.url,
        issue_code="2026-04",
        articles=[article_link],
    )

    class FakeWebClient:
        def __init__(self, config: AppConfig) -> None:
            del config

        def discover_issues(self) -> list[Link]:
            return [issue_link]

        def discover_issue(self, url: str) -> Issue:
            assert url == issue_link.url
            return issue

        def download_article(self, url: str) -> Article:
            assert url == article_link.url
            return Article(
                title=article_link.title,
                url=url,
                html="<article><h1>E la Casa Bianca restò sola</h1><p>Corpo.</p></article>",
                text="Corpo.",
                author="Lorenzo Maria Ricci",
            )

    monkeypatch.setattr(cli, "WebClient", FakeWebClient)
    monkeypatch.setattr(cli, "_ensure_issue_cover", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli,
        "_write_issue_sidecar",
        lambda *args, **kwargs: tmp_path / "exports" / "library" / "rivista" / "issue.json",
    )

    result = cli._handle_sync(
        AppConfig(output_dir=tmp_path / "exports", verbose=False, magazine_title="Domino"),
        create_audio=False,
        create_audiobook=False,
        audio_format="m4a",
        audio_timeout=900.0,
        export_formats=("txt",),
        max_articles=None,
    )

    captured = capsys.readouterr()

    assert result == 0
    assert "Domino" in captured.out
    assert "article" in captured.out
    assert "issue: Guaio persiano" in captured.out
    assert "written" in captured.out
    assert "downloaded:" not in captured.out


def test_sync_magazine_skips_empty_issue_audiobook_plans(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    issue_link = Link(
        title="8/2022 Numero vuoto",
        url="https://www.rivistadomino.it/prodotto/numero-vuoto?sfoglia=1",
    )
    issue = Issue(
        title="Numero vuoto",
        url=issue_link.url,
        issue_code="2022-08",
        articles=[],
    )

    class FakeWebClient:
        def __init__(self, config: AppConfig) -> None:
            del config

        def discover_issues(self) -> list[Link]:
            return [issue_link]

        def discover_issue(self, url: str) -> Issue:
            assert url == issue_link.url
            return issue

    monkeypatch.setattr(cli, "WebClient", FakeWebClient)
    monkeypatch.setattr(cli, "_speak_paths", lambda *args, **kwargs: 0)

    def fake_build_issue_audiobook(*args: object, **kwargs: object) -> Path:
        del args, kwargs
        raise AssertionError("empty issues should not be packaged as audiobooks")

    monkeypatch.setattr(cli, "_build_issue_audiobook", fake_build_issue_audiobook)

    result = cli._handle_sync(
        AppConfig(output_dir=tmp_path / "exports", audiobook_auto=True),
        create_audio=False,
        create_audiobook=True,
        audio_format="m4a",
        audio_timeout=900.0,
        export_formats=("txt",),
        max_articles=None,
    )

    assert result == 0


def test_audio_timeout_must_be_positive() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["download", "https://example.test/a", "--audio-timeout", "0"])

    try:
        cli._audio_options(args, AppConfig())
    except ValueError as exc:
        assert "audio_timeout must be greater than 0" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_speech_normalize_cli_options_default_to_config_and_allow_overrides() -> None:
    parser = cli.build_parser()
    config = AppConfig(
        speech_normalize_auto=True,
        speech_normalize_agent="codex",
        speech_normalize_command="codex",
        speech_normalize_model="gpt-5.2",
        speech_normalize_timeout=456.0,
        speech_normalize_force=False,
        speech_normalize_fallback=False,
        speech_normalize_prompt_path=Path("/tmp/default-prompt.md"),
    )

    sync_args = parser.parse_args(["sync-magazine"])
    sync_options = cli._speech_normalize_options(sync_args, config)
    assert sync_options.enabled is True
    assert sync_options.agent == "codex"
    assert sync_options.command == "codex"
    assert sync_options.model == "gpt-5.2"
    assert sync_options.timeout == 456.0
    assert sync_options.force is False
    assert sync_options.fallback is False
    assert sync_options.prompt_path == Path("/tmp/default-prompt.md")

    disabled_args = parser.parse_args(
        ["download", "https://example.test/a", "--no-speech-normalize"]
    )
    assert cli._speech_normalize_options(disabled_args, config).enabled is False

    override_args = parser.parse_args(
        [
            "download",
            "https://example.test/a",
            "--speech-normalize",
            "--speech-normalize-agent",
            "codex",
            "--speech-normalize-command",
            "/opt/homebrew/bin/codex",
            "--speech-normalize-model",
            "gpt-5.3",
            "--speech-normalize-timeout",
            "789",
            "--speech-normalize-force",
            "--speech-normalize-fallback",
            "--speech-normalize-prompt",
            "/tmp/custom-prompt.md",
        ]
    )
    override_options = cli._speech_normalize_options(override_args, AppConfig())
    assert override_options.enabled is True
    assert override_options.command == "/opt/homebrew/bin/codex"
    assert override_options.model == "gpt-5.3"
    assert override_options.timeout == 789.0
    assert override_options.force is True
    assert override_options.fallback is True
    assert override_options.prompt_path == Path("/tmp/custom-prompt.md")


def test_format_duration_uses_minutes_and_hours() -> None:
    assert cli._format_duration(0) == "00:00"
    assert cli._format_duration(65) == "01:05"
    assert cli._format_duration(3661) == "1:01:01"


def test_indeterminate_bar_bounces_inside_width() -> None:
    assert cli._indeterminate_bar(0) == "[███         ]"
    assert cli._indeterminate_bar(0, width=10, chunk_width=3) == "[███       ]"
    assert cli._indeterminate_bar(7, width=10, chunk_width=3) == "[       ███]"
    assert cli._indeterminate_bar(14, width=10, chunk_width=3) == "[███       ]"


def test_render_progress_line_truncates_long_label(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("get_my_domino.cli._terminal_columns", lambda: 60)

    line = cli._render_progress_line(
        "Generating audio 2026-03-13-conflitto-in-medio-oriente-turchia-afghanistan-vs-pakistan",
        index=0,
        detail="convert: final audio format",
    )

    assert "\n" not in line
    assert len(line) <= 60
    assert "..." in line


def test_speak_paths_uses_requested_audio_format(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    text_path = (
        tmp_path
        / "exports"
        / "2026-04-guaio-persiano"
        / "01-l-editoriale"
        / "01-editoriale"
        / "01-editoriale.txt"
    )
    text_path.parent.mkdir(parents=True)
    text_path.write_text("Titolo\n\nCorpo.", encoding="utf-8")
    calls: list[tuple[Path, Path, str | None, str, float | None]] = []

    def fake_synthesize_audio(
        source: Path,
        output: Path,
        *,
        voice: str | None,
        audio_format: str,
        timeout: float | None = None,
        progress: object | None = None,
        **kwargs: object,
    ) -> Path:
        del progress, kwargs
        calls.append((source, output, voice, audio_format, timeout))
        output.write_text("fake", encoding="utf-8")
        return output

    monkeypatch.setattr(cli, "synthesize_audio", fake_synthesize_audio)
    clock = iter([0.0, 12.0])
    monkeypatch.setattr("get_my_domino.cli.time.monotonic", lambda: next(clock))

    result = cli._speak_paths(
        [text_path],
        output_dir=tmp_path / "exports",
        voice="Siri Voice 2",
        audio_format="mp3",
        timeout=321.0,
    )

    captured = capsys.readouterr()
    assert result == 0
    audio_path = (
        tmp_path
        / "exports"
        / "2026-04-guaio-persiano"
        / "01-l-editoriale"
        / "01-editoriale"
        / "01-editoriale.mp3"
    )
    assert calls == [(text_path, audio_path, "Siri Voice 2", "mp3", 321.0)]
    assert "article" in captured.out
    assert "01-editoriale" in captured.out
    assert "off" in captured.out
    assert "generated" in captured.out
    assert "00:12" in captured.out


def test_download_continues_after_audio_failure_and_reports_summary(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    first_url = "https://www.rivistadomino.it/blog/2026/04/21/one"
    second_url = "https://www.rivistadomino.it/blog/2026/04/21/two"
    session = FakeSession(
        {
            first_url: "<article><h1>One</h1><p>Corpo uno.</p></article>",
            second_url: "<article><h1>Two</h1><p>Corpo due.</p></article>",
        }
    )
    config = AppConfig(
        output_dir=tmp_path / "exports",
        auth_session_path=tmp_path / "missing-session.json",
    )
    calls: list[Path] = []

    def fake_synthesize_audio(
        source: Path,
        output: Path,
        *,
        voice: str | None,
        audio_format: str,
        timeout: float | None = None,
        progress: object | None = None,
        chunked: bool = True,
        chunk_chars: int = 2500,
        concurrency: int = 3,
        retries: int = 2,
        stall_timeout: float | None = 45.0,
    ) -> Path:
        del voice, audio_format, timeout, progress, chunked, chunk_chars, concurrency
        del retries, stall_timeout
        calls.append(source)
        if source.name.startswith("001-"):
            raise audio_module.AudioError("say chunk 2 timed out")
        output.write_text("audio", encoding="utf-8")
        return output

    monkeypatch.setattr(
        cli, "WebClient", lambda loaded_config: WebClient(loaded_config, session=session)
    )
    monkeypatch.setattr(cli, "synthesize_audio", fake_synthesize_audio)

    result = cli._download_articles(
        [first_url, second_url],
        config,
        config.output_dir,
        create_audio=True,
        audio_format="m4a",
        audio_timeout=900.0,
    )

    captured = capsys.readouterr()
    assert result == 1
    assert len(calls) == 2
    assert "audio failures: 1" in captured.err
    assert "001-one" in captured.err
    assert "say chunk 2 timed out" in captured.err
    assert "Two" in captured.out
    assert "generated" in captured.out


def test_ensure_audio_uses_speech_text_when_normalization_is_enabled(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    article_dir = tmp_path / "exports" / "001-editoriale"
    article_dir.mkdir(parents=True)
    raw_text = article_dir / "001-editoriale.txt"
    raw_text.write_text("Titolo\n\nCorpo.", encoding="utf-8")
    speech_text = article_dir / "001-editoriale.speech.txt"
    calls: list[Path] = []

    def fake_ensure_speech_text(
        source: Path,
        *,
        options: cli.SpeechNormalizeOptions,
    ) -> Path:
        assert source == raw_text
        assert options.enabled is True
        speech_text.write_text("Titolo\n\nCorpo normalizzato.", encoding="utf-8")
        return speech_text

    def fake_synthesize_audio(
        source: Path,
        output: Path,
        **kwargs: object,
    ) -> Path:
        del kwargs
        calls.append(source)
        output.write_text("audio", encoding="utf-8")
        return output

    monkeypatch.setattr(cli, "ensure_speech_text", fake_ensure_speech_text)
    monkeypatch.setattr(cli, "synthesize_audio", fake_synthesize_audio)

    status, output = cli._ensure_audio(
        article_dir,
        output_dir=tmp_path / "exports",
        voice=None,
        audio_format="m4a",
        timeout=900.0,
        speech_options=cli.SpeechNormalizeOptions(
            enabled=True,
            agent="codex",
            command="codex",
            model="",
            timeout=900.0,
            force=False,
            fallback=False,
            prompt_path=None,
            diff=False,
        ),
    )

    assert status == "generated"
    assert calls == [speech_text]
    assert output == article_dir / "001-editoriale.m4a"


def test_codex_speech_normalizer_invokes_cli_without_printing_article(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = tmp_path / "001-editoriale.txt"
    source.write_text("prossima al\n\nJahannam\n\n. Peggio.", encoding="utf-8")
    output = tmp_path / "001-editoriale.speech.txt"
    commands: list[list[str]] = []
    prompts: list[str] = []

    def fake_run(
        command: list[str],
        *,
        input: str,
        text: bool,
        capture_output: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        del text, capture_output, timeout, check
        commands.append(command)
        prompts.append(input)
        output.write_text("prossima al Jahannam.\nPeggio.", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    monkeypatch.setattr("get_my_domino.speech_normalize.subprocess.run", fake_run)

    result = speech_normalize.ensure_speech_text(
        source,
        speech_normalize.SpeechNormalizeSettings(
            enabled=True,
            agent="codex",
            command="codex",
            model="gpt-5.2",
            timeout=123.0,
            force=False,
            fallback=False,
            prompt_path=None,
            diff=False,
        ),
    )

    assert result == output
    assert output.read_text(encoding="utf-8") == "prossima al Jahannam.\nPeggio.\n"
    assert commands
    assert Path(commands[0][0]).name == "codex"
    assert commands[0][1] == "exec"
    assert "-m" in commands[0]
    assert "gpt-5.2" in commands[0]
    assert "Conservative Italian punctuation guidance for TTS prosody" in prompts[0]
    assert "Never insert a comma between subject and predicate" in prompts[0]
    assert "Do not add expressive punctuation for drama" in prompts[0]
    assert (tmp_path / "001-editoriale.speech.log").exists()


def test_speech_normalizer_rejects_unimplemented_agents(tmp_path: Path) -> None:
    source = tmp_path / "001-editoriale.txt"
    source.write_text("Titolo", encoding="utf-8")

    with pytest.raises(speech_normalize.SpeechNormalizeError) as exc:
        speech_normalize.ensure_speech_text(
            source,
            speech_normalize.SpeechNormalizeSettings(
                enabled=True,
                agent="github-copilot",
                command="gh",
                model="",
                timeout=123.0,
                force=False,
                fallback=False,
                prompt_path=None,
                diff=False,
            ),
        )

    assert "not implemented" in str(exc.value)


def test_codex_speech_normalizer_uses_custom_prompt_file(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    source = tmp_path / "001-editoriale.txt"
    source.write_text("Titolo", encoding="utf-8")
    output = tmp_path / "001-editoriale.speech.txt"
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text(
        "Write {source_text_path} to {output_path} using {normalized_text}",
        encoding="utf-8",
    )
    prompts: list[str] = []

    def fake_run(
        command: list[str],
        *,
        input: str,
        text: bool,
        capture_output: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        del command, text, capture_output, timeout, check
        prompts.append(input)
        output.write_text("Titolo", encoding="utf-8")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr("get_my_domino.speech_normalize.subprocess.run", fake_run)

    speech_normalize.ensure_speech_text(
        source,
        speech_normalize.SpeechNormalizeSettings(
            enabled=True,
            agent="codex",
            command="codex",
            model="",
            timeout=123.0,
            force=False,
            fallback=False,
            prompt_path=prompt_path,
            diff=False,
        ),
    )

    assert prompts == [f"Write {source} to {output} using Titolo\n"]


def test_synthesize_mp3_uses_ffmpeg_after_say(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    text_path = tmp_path / "article.txt"
    text_path.write_text("Titolo", encoding="utf-8")
    output_path = tmp_path / "article.mp3"
    commands: list[list[str]] = []

    def fake_which(command: str) -> str | None:
        return f"/usr/bin/{command}"

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[0].endswith("say") and command[-1] == "?":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="Alice               it_IT    # Ciao! Mi chiamo Alice.\n",
            )
        return subprocess.CompletedProcess(command, 0)

    def fake_popen(command: list[str]) -> "FakeProcess":
        commands.append(command)
        if command[0].endswith("say"):
            Path(command[command.index("-o") + 1]).write_text("aiff", encoding="utf-8")
        elif command[0].endswith("ffmpeg"):
            Path(command[-1]).write_text("mp3", encoding="utf-8")
        return FakeProcess()

    monkeypatch.setattr("get_my_domino.audio.shutil.which", fake_which)
    monkeypatch.setattr("get_my_domino.audio.subprocess.run", fake_run)
    monkeypatch.setattr("get_my_domino.audio.subprocess.Popen", fake_popen)

    result = audio_module.synthesize_audio(
        text_path,
        output_path,
        voice="Alice",
        audio_format="mp3",
    )

    assert result == output_path
    assert commands[0] == ["/usr/bin/say", "-v", "?"]
    assert commands[1][:2] == ["/usr/bin/say", "-f"]
    assert commands[2][0] == "/usr/bin/ffmpeg"
    assert "-codec:a" in commands[2]
    assert not output_path.with_suffix(".aiff").exists()


def test_synthesize_audio_omits_voice_flag_for_system_voice(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    text_path = tmp_path / "article.txt"
    text_path.write_text("Titolo", encoding="utf-8")
    output_path = tmp_path / "article.m4a"
    commands: list[list[str]] = []

    def fake_which(command: str) -> str | None:
        return f"/usr/bin/{command}"

    def fake_popen(command: list[str]) -> "FakeProcess":
        commands.append(command)
        if command[0].endswith("say"):
            Path(command[command.index("-o") + 1]).write_text("aiff", encoding="utf-8")
        elif command[0].endswith("afconvert"):
            Path(command[-1]).write_text("m4a", encoding="utf-8")
        return FakeProcess()

    monkeypatch.setattr("get_my_domino.audio.shutil.which", fake_which)
    monkeypatch.setattr("get_my_domino.audio.subprocess.Popen", fake_popen)

    result = audio_module.synthesize_audio(
        text_path,
        output_path,
        voice="",
        audio_format="m4a",
    )

    assert result == output_path
    assert commands[0][:2] == ["/usr/bin/say", "-f"]
    assert "-v" not in commands[0]


def test_synthesize_audio_chunked_generates_aiff_chunks_then_converts_once(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    text_path = tmp_path / "article.txt"
    text_path.write_text("Titolo\n\n" + ("Primo paragrafo. " * 80) + "\n\nFine.", encoding="utf-8")
    output_path = tmp_path / "article.m4a"
    commands: list[list[str]] = []

    def fake_which(command: str) -> str | None:
        return f"/usr/bin/{command}"

    def fake_popen(command: list[str]) -> "FakeProcess":
        commands.append(command)
        if command[0].endswith("say"):
            _write_test_aiff(Path(command[command.index("-o") + 1]), frames=8)
        elif command[0].endswith("afconvert"):
            assert command[-2].endswith(".aiff")
            Path(command[-1]).write_text("m4a", encoding="utf-8")
        return FakeProcess()

    monkeypatch.setattr("get_my_domino.audio.shutil.which", fake_which)
    monkeypatch.setattr("get_my_domino.audio.subprocess.Popen", fake_popen)

    result = audio_module.synthesize_audio(
        text_path,
        output_path,
        voice="",
        audio_format="m4a",
        chunked=True,
        chunk_chars=300,
        concurrency=3,
        retries=1,
        stall_timeout=45.0,
    )

    say_commands = [command for command in commands if command[0].endswith("say")]
    convert_commands = [command for command in commands if command[0].endswith("afconvert")]
    assert result == output_path
    assert len(say_commands) > 1
    assert len(convert_commands) == 1
    assert output_path.read_text(encoding="utf-8") == "m4a"


def _write_test_aiff(path: Path, *, frames: int) -> None:
    channels = 1
    sample_size = 16
    sample_rate_44100 = bytes.fromhex("400eac44000000000000")
    sound_data = b"\x00\x00" * frames
    comm = (
        channels.to_bytes(2, "big")
        + frames.to_bytes(4, "big")
        + sample_size.to_bytes(2, "big")
        + sample_rate_44100
    )
    ssnd_payload = (0).to_bytes(4, "big") + (0).to_bytes(4, "big") + sound_data
    form_size = 4 + 8 + len(comm) + 8 + len(ssnd_payload)
    path.write_bytes(
        b"FORM"
        + form_size.to_bytes(4, "big")
        + b"AIFF"
        + b"COMM"
        + len(comm).to_bytes(4, "big")
        + comm
        + b"SSND"
        + len(ssnd_payload).to_bytes(4, "big")
        + ssnd_payload
    )


def test_synthesize_audio_locks_say_phase_across_processes(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    text_path = tmp_path / "article.txt"
    text_path.write_text("Titolo", encoding="utf-8")
    output_path = tmp_path / "article.m4a"
    lock_events: list[int] = []

    def fake_which(command: str) -> str | None:
        return f"/usr/bin/{command}"

    def fake_flock(file_descriptor: int, operation: int) -> None:
        del file_descriptor
        lock_events.append(operation)

    def fake_popen(command: list[str]) -> "FakeProcess":
        if command[0].endswith("say"):
            assert lock_events[-1] == fcntl.LOCK_EX | fcntl.LOCK_NB
            Path(command[command.index("-o") + 1]).write_text("aiff", encoding="utf-8")
        elif command[0].endswith("afconvert"):
            assert lock_events[-1] == fcntl.LOCK_UN
            Path(command[-1]).write_text("m4a", encoding="utf-8")
        return FakeProcess()

    monkeypatch.setattr("get_my_domino.audio.Path.home", lambda: tmp_path)
    monkeypatch.setattr("get_my_domino.audio.shutil.which", fake_which)
    monkeypatch.setattr("get_my_domino.audio.fcntl.flock", fake_flock)
    monkeypatch.setattr("get_my_domino.audio.subprocess.Popen", fake_popen)

    result = audio_module.synthesize_audio(
        text_path,
        output_path,
        voice="",
        audio_format="m4a",
    )

    assert result == output_path
    assert lock_events == [fcntl.LOCK_EX | fcntl.LOCK_NB, fcntl.LOCK_UN]
    assert (tmp_path / "audio.lock").exists()


def test_synthesize_audio_reports_lock_queue_and_aiff_growth(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    text_path = tmp_path / "article.txt"
    text_path.write_text("Titolo", encoding="utf-8")
    output_path = tmp_path / "article.m4a"
    lock_events: list[int] = []
    progress_events: list[tuple[str, int | None]] = []

    def fake_which(command: str) -> str | None:
        return f"/usr/bin/{command}"

    def fake_flock(file_descriptor: int, operation: int) -> None:
        del file_descriptor
        lock_events.append(operation)
        if operation == fcntl.LOCK_EX | fcntl.LOCK_NB:
            raise BlockingIOError(errno.EAGAIN, "locked")

    def fake_popen(command: list[str]) -> "FakeGrowingProcess | FakeProcess":
        if command[0].endswith("say"):
            return FakeGrowingProcess(Path(command[command.index("-o") + 1]))
        if command[0].endswith("afconvert"):
            Path(command[-1]).write_text("m4a", encoding="utf-8")
        return FakeProcess()

    def progress(event: str, path: Path | None, size: int | None) -> None:
        del path
        progress_events.append((event, size))

    monkeypatch.setattr("get_my_domino.audio.shutil.which", fake_which)
    monkeypatch.setattr("get_my_domino.audio.fcntl.flock", fake_flock)
    monkeypatch.setattr("get_my_domino.audio.subprocess.Popen", fake_popen)

    result = audio_module.synthesize_audio(
        text_path,
        output_path,
        voice="",
        audio_format="m4a",
        progress=progress,
    )

    assert result == output_path
    assert lock_events == [
        fcntl.LOCK_EX | fcntl.LOCK_NB,
        fcntl.LOCK_EX,
        fcntl.LOCK_UN,
    ]
    assert ("waiting_lock", None) in progress_events
    assert ("aiff_growth", 4) in progress_events
    assert ("aiff_growth", 8) in progress_events


class FakeProcess:
    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


class FakeGrowingProcess:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.waits = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.waits += 1
        if self.waits == 1:
            self.output_path.write_text("aiff", encoding="utf-8")
            raise subprocess.TimeoutExpired(["say"], 0.5)
        if self.waits == 2:
            self.output_path.write_text("aiffaiff", encoding="utf-8")
            raise subprocess.TimeoutExpired(["say"], 0.5)
        return 0

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


def test_synthesize_audio_terminates_subprocess_on_interrupt(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    text_path = tmp_path / "article.txt"
    text_path.write_text("Titolo", encoding="utf-8")
    output_path = tmp_path / "article.m4a"
    process = FakeInterruptingProcess()

    def fake_which(command: str) -> str | None:
        return f"/usr/bin/{command}"

    monkeypatch.setattr("get_my_domino.audio.shutil.which", fake_which)
    monkeypatch.setattr("get_my_domino.audio.subprocess.Popen", lambda command: process)

    try:
        audio_module.synthesize_audio(
            text_path,
            output_path,
            voice="",
            audio_format="m4a",
        )
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("expected KeyboardInterrupt")

    assert process.terminated is True
    assert process.killed is False
    assert not output_path.with_suffix(".aiff").exists()


class FakeInterruptingProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self._interrupted = False

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if not self._interrupted:
            self._interrupted = True
            raise KeyboardInterrupt
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def test_synthesize_audio_times_out_and_removes_temporary_aiff(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    text_path = tmp_path / "article.txt"
    text_path.write_text("Titolo", encoding="utf-8")
    output_path = tmp_path / "article.m4a"
    process = FakeTimingOutProcess(["/usr/bin/say"])

    def fake_which(command: str) -> str | None:
        return f"/usr/bin/{command}"

    def fake_popen(command: list[str]) -> "FakeTimingOutProcess":
        process.command = command
        if command[0].endswith("say"):
            Path(command[command.index("-o") + 1]).write_text("partial aiff", encoding="utf-8")
        return process

    monkeypatch.setattr("get_my_domino.audio.shutil.which", fake_which)
    monkeypatch.setattr("get_my_domino.audio.subprocess.Popen", fake_popen)

    try:
        audio_module.synthesize_audio(
            text_path,
            output_path,
            voice="",
            audio_format="m4a",
            timeout=0.01,
        )
    except audio_module.AudioError as exc:
        assert "timed out after 0.01 seconds: say" in str(exc)
    else:
        raise AssertionError("expected AudioError")

    assert process.terminated is True
    assert process.killed is False
    assert not output_path.with_suffix(".aiff").exists()


class FakeTimingOutProcess:
    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.terminated = False
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        if not self.terminated:
            raise subprocess.TimeoutExpired(self.command, timeout or 0)
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def test_synthesize_audio_rejects_voice_that_say_would_silently_ignore(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    text_path = tmp_path / "article.txt"
    text_path.write_text("Titolo", encoding="utf-8")

    def fake_which(command: str) -> str | None:
        return f"/usr/bin/{command}"

    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Alice               it_IT    # Ciao! Mi chiamo Alice.\n",
        )

    monkeypatch.setattr("get_my_domino.audio.shutil.which", fake_which)
    monkeypatch.setattr("get_my_domino.audio.subprocess.run", fake_run)

    try:
        audio_module.synthesize_audio(
            text_path,
            tmp_path / "article.m4a",
            voice="Italian (Voice 1)",
            audio_format="m4a",
        )
    except audio_module.AudioError as exc:
        assert "would fall back silently" in str(exc)
    else:
        raise AssertionError("expected AudioError")
