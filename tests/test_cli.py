from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import requests
from pytest import CaptureFixture, MonkeyPatch
from requests import Response
from requests.cookies import RequestsCookieJar

from get_my_domino import __version__, cli
from get_my_domino import audio as audio_module
from get_my_domino.config import AppConfig, load_config
from get_my_domino.extract import extract_article, extract_links
from get_my_domino.models import Article, Link
from get_my_domino.session_store import load_cookies, save_cookies
from get_my_domino.storage import article_text_document, write_article
from get_my_domino.web import WebClient


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
                'app_name = "Example App"',
                'default_output = "json"',
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
    assert "app_name: Example App" in captured.out
    assert "default_output: json" in captured.out
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
    config_path.write_text('app_name = "Example App"\n', encoding="utf-8")

    result = cli.main(["--config", str(config_path), "info", "--json"])

    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["project_name"] == "get-my-domino"
    assert payload["cli_name"] == "get-my-domino"
    assert payload["config"]["app_name"] == "Example App"


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
                'audio_format = "mp4a"',
                'siri_voice = "Siri Voice 2"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.audio_auto is True
    assert config.audio_format == "m4a"
    assert config.siri_voice == "Siri Voice 2"


def test_config_rejects_unknown_audio_format(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('audio_format = "wav"\n', encoding="utf-8")

    try:
        load_config(config_path)
    except ValueError as exc:
        assert "audio_format" in str(exc)
    else:
        raise AssertionError("Expected audio_format validation failure.")


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
    assert "progress: retry 2/3 GET" in captured.err
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
              <div class="summary">
                <h1 class="product_title">Guaio persiano</h1>
                <p>4/2026 Guaio persiano</p>
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
    assert issue.published_month == "2026-04"
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
    assert "month: 2026-04" in captured.out
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
        assert "Use a YYYY-MM issue code" in str(exc)
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
        output_dir=config.output_dir,
        create_audio=False,
        audio_format="m4a",
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "progress: resolve issue 2026-04" in captured.err
    assert "progress: resolve article 1" in captured.err
    assert f"progress: fetch {article_url}" in captured.err
    assert "progress: write export" in captured.err
    assert "downloaded: Editoriale" in captured.out
    assert (tmp_path / "exports" / "001-editoriale" / "article.html").exists()


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
    ) -> Path:
        del voice, audio_format
        audio_calls.append((source, output))
        output.write_text("audio", encoding="utf-8")
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
    )

    assert result == 0
    assert not (tmp_path / "exports" / "002-editoriale").exists()
    assert (existing_dir / "article.html").exists()
    assert "Corpo aggiornato." in (existing_dir / "article.txt").read_text(encoding="utf-8")
    assert (existing_dir / "article.rtf").exists()
    assert audio_calls == [(existing_dir / "article.txt", existing_dir / "article.m4a")]


def test_explicit_download_uses_existing_exports_when_only_audio_is_missing(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    article_url = "https://www.rivistadomino.it/blog/2026/04/21/editoriale"
    existing_dir = tmp_path / "exports" / "001-editoriale"
    existing_dir.mkdir(parents=True)
    (existing_dir / "article.html").write_text("<article>old</article>", encoding="utf-8")
    (existing_dir / "article.txt").write_text("Titolo\n\nCorpo 日本語.", encoding="utf-8")
    (existing_dir / "article.rtf").write_text(r"{\rtf1 old}", encoding="ascii")
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
    ) -> Path:
        del voice, audio_format
        audio_calls.append((source, output))
        output.write_text("audio", encoding="utf-8")
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
    )

    assert result == 0
    captured = capsys.readouterr()
    assert "progress: export complete; reusing local files" in captured.err
    assert "progress: audio start article.m4a" in captured.err
    assert session.gets == []
    assert (existing_dir / "article.html").read_text(encoding="utf-8") == "<article>old</article>"
    assert audio_calls == [(existing_dir / "article.txt", existing_dir / "article.m4a")]


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
        force=True,
    )

    assert result == 0
    assert session.gets == [article_url]
    assert "Corpo forzato." in (existing_dir / "article.txt").read_text(encoding="utf-8")


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
    month, title, synopsis = cli._issue_summary_parts(
        "8/2025 Europei brava gente 7,50 € - 10,00 € Fascia di prezzo: da 7,50 € a 10,00 € Sinossi."
    )

    assert month == "2025-08"
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
        == "01-2026-04-21-cosa-fare-a-teheran-quando-sei-morto"
    )
    assert (
        cli._article_folder_name(feed_link, fallback_index=2)
        == "02-2026-04-24-usa-e-globalizzazione"
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


def test_extract_article_normalizes_domino_directed_by_author() -> None:
    article = extract_article(
        "<article><h1>Titolo</h1><p>diretta da Dario Fabbri</p><p>Corpo.</p></article>",
        page_url="https://example.test/articolo/",
        content_selectors=("article",),
    )

    assert article.author == "Dario Fabbri"


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

    target_dir = write_article(tmp_path, article, index=1)

    assert target_dir.name == "001-titolo-articolo"
    assert (target_dir / "article.html").exists()
    article_text = (target_dir / "article.txt").read_text(encoding="utf-8")
    assert article_text.startswith("Titolo articolo 日本語\n\n")
    assert "https://example.test/articolo/" not in article_text
    rtf = (target_dir / "article.rtf").read_text(encoding="ascii")
    assert rtf.startswith(r"{\rtf1")
    assert r"\u26085?" in rtf
    assert r"\u26412?" in rtf
    assert r"\u1052?" in rtf


def test_audio_cli_options_default_to_config_and_allow_overrides() -> None:
    parser = cli.build_parser()
    config = AppConfig(audio_auto=True, audio_format="m4a")

    sync_args = parser.parse_args(["sync-magazine"])
    assert cli._audio_options(sync_args, config) == (True, "m4a")

    feed_args = parser.parse_args(["sync-feed", "--no-audio"])
    assert cli._audio_options(feed_args, config) == (False, "m4a")

    download_args = parser.parse_args(
        ["download", "https://example.test/a", "--audio", "--audio-format", "mp3"]
    )
    assert cli._audio_options(download_args, AppConfig()) == (True, "mp3")


def test_speak_paths_uses_requested_audio_format(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    text_path = tmp_path / "article.txt"
    text_path.write_text("Titolo\n\nCorpo.", encoding="utf-8")
    calls: list[tuple[Path, Path, str | None, str]] = []

    def fake_synthesize_audio(
        source: Path,
        output: Path,
        *,
        voice: str | None,
        audio_format: str,
    ) -> Path:
        calls.append((source, output, voice, audio_format))
        output.write_text("fake", encoding="utf-8")
        return output

    monkeypatch.setattr(cli, "synthesize_audio", fake_synthesize_audio)

    result = cli._speak_paths([text_path], voice="Siri Voice 2", audio_format="mp3")

    captured = capsys.readouterr()
    assert result == 0
    assert calls == [(text_path, tmp_path / "article.mp3", "Siri Voice 2", "mp3")]
    assert "article.mp3" in captured.out


def test_synthesize_mp3_uses_ffmpeg_after_say(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    text_path = tmp_path / "article.txt"
    text_path.write_text("Titolo", encoding="utf-8")
    output_path = tmp_path / "article.mp3"
    commands: list[list[str]] = []

    def fake_which(command: str) -> str | None:
        return f"/usr/bin/{command}"

    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text
        commands.append(command)
        if command[0].endswith("say") and command[-1] == "?":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="Alice               it_IT    # Ciao! Mi chiamo Alice.\n",
            )
        if command[0].endswith("say"):
            Path(command[command.index("-o") + 1]).write_text("aiff", encoding="utf-8")
        elif command[0].endswith("ffmpeg"):
            Path(command[-1]).write_text("mp3", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("get_my_domino.audio.shutil.which", fake_which)
    monkeypatch.setattr("get_my_domino.audio.subprocess.run", fake_run)

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

    def fake_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[0].endswith("say"):
            Path(command[command.index("-o") + 1]).write_text("aiff", encoding="utf-8")
        elif command[0].endswith("afconvert"):
            Path(command[-1]).write_text("m4a", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("get_my_domino.audio.shutil.which", fake_which)
    monkeypatch.setattr("get_my_domino.audio.subprocess.run", fake_run)

    result = audio_module.synthesize_audio(
        text_path,
        output_path,
        voice="",
        audio_format="m4a",
    )

    assert result == output_path
    assert commands[0][:2] == ["/usr/bin/say", "-f"]
    assert "-v" not in commands[0]


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
