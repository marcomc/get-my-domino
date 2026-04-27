"""Command-line interface for get-my-domino."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
from dataclasses import asdict, replace
from pathlib import Path
from typing import Sequence

from . import __version__
from .audio import AudioError, available_say_voices, normalize_audio_format, synthesize_audio
from .browser_auth import BrowserAuthError, login_with_browser
from .config import DEFAULT_CONFIG_PATH, AppConfig, load_config
from .extract import article_date_from_url, issue_month_from_text, slugify
from .models import Article, Issue, Link
from .session_store import clear_cookies
from .storage import (
    missing_article_export_files,
    read_manifest,
    write_article,
    write_article_export,
    write_article_named,
    write_manifest,
)
from .web import FetchError, WebClient, discover_articles, discover_feed_articles, discover_issues


def format_main_help() -> str:
    return "\n".join(
        [
            "usage: get-my-domino [--version] [--config PATH] [--verbose] <command>",
            "",
            "Download rivistadomino.it articles as clean HTML, text, RTF, and audio.",
            "",
            "Commands:",
            "  catalog       Browse readable issue and feed indexes",
            "  download      Download one or more article URLs",
            "  sync-magazine Download new magazine articles",
            "  sync-feed     Download new weekly feed articles",
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
            "Browse Domino content in a readable format. Without options it lists available "
            "issues; --issue YYYY-MM expands one issue; --all expands every issue; --feed "
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
        help="Show one issue by YYYY-MM issue code or by issue URL.",
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
        help="Download one or more articles as clean HTML and text.",
    )
    download_parser.add_argument(
        "article_urls",
        nargs="*",
        help="Article page URLs to download.",
    )
    download_parser.add_argument(
        "--issue",
        help="Download an article from a magazine issue by YYYY-MM issue code.",
    )
    download_parser.add_argument(
        "--article",
        help="Article order inside --issue, such as 1 or 01.",
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
    _add_audio_options(download_parser)

    sync_parser = subparsers.add_parser(
        "sync-magazine",
        aliases=["sync"],
        help="Download new magazine issue articles.",
    )
    _add_audio_options(sync_parser)
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


def _audio_options(args: argparse.Namespace, config: AppConfig) -> tuple[bool, str]:
    if bool(getattr(args, "no_audio", False)):
        create_audio = False
    else:
        create_audio = bool(getattr(args, "audio", False)) or config.audio_auto
    raw_format = str(getattr(args, "audio_format", None) or config.audio_format)
    return create_audio, normalize_audio_format(raw_format)


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
            "Use a YYYY-MM issue code from `catalog`, or pass the issue URL."
        )
    if re.fullmatch(r"\d{4}-\d{2}", selector):
        matches = [issue for issue in issues if issue_month_from_text(issue.title) == selector]
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
        month, title, synopsis = _issue_summary_parts(issue.title)
        display_month = month or "unknown"
        print(f"{display_month}  {title}")
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
    if issue.published_month:
        print(f"month: {issue.published_month}")
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
    month = issue_month_from_text(title) or "0000-00"
    return (month, _strip_price_text(title))


def _issue_summary_parts(title: str) -> tuple[str | None, str, str | None]:
    clean_title = " ".join(title.split())
    month = issue_month_from_text(clean_title)
    first_price = re.search(_PRICE_PATTERN, clean_title)
    if first_price is None:
        return month, _strip_issue_month(_strip_price_text(clean_title)), None

    title_part = clean_title[: first_price.start()]
    synopsis_part = clean_title[first_price.end() :]
    title_part = _strip_issue_month(_strip_price_text(title_part))
    synopsis_part = _strip_price_text(synopsis_part).strip(" -–—:")
    return month, title_part, synopsis_part or None


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


def _strip_issue_month(title: str) -> str:
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
    print(f"app_name: {config.app_name}")
    print(f"default_output: {config.default_output}")
    print(f"verbose: {config.verbose}")
    print(f"magazine_index_url: {config.magazine_index_url}")
    print(f"output_dir: {config.output_dir}")
    print(f"feed_output_dir: {config.feed_output_dir}")
    print(f"siri_voice: {config.siri_voice or ''}")
    print(f"audio_auto: {config.audio_auto}")
    print(f"audio_format: {config.audio_format}")
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
    force: bool = False,
    issue_titles: dict[str, str] | None = None,
) -> int:
    client = WebClient(config)
    manifest = read_manifest(output_dir)
    next_index = len(manifest) + 1
    downloaded_dirs: list[Path] = []
    for article_url in article_urls:
        existing_dir = _existing_article_dir(manifest, article_url)
        if existing_dir is not None:
            target_dir = existing_dir
            missing_files = missing_article_export_files(target_dir)
            if force or missing_files:
                reason = "force" if force else f"missing {', '.join(missing_files)}"
                _progress(f"fetch {article_url} ({reason})")
                article = client.download_article(article_url)
                article = _with_article_context(article, issue_titles=issue_titles)
                _progress(f"write export {target_dir}")
                write_article_export(target_dir, article)
                manifest[article.url] = str(target_dir)
                print(f"downloaded: {article.title}")
            else:
                _progress("export complete; reusing local files")
                print(f"existing: {target_dir.name}")
            downloaded_dirs.append(target_dir)
            print(f"  {target_dir}")
            continue

        _progress(f"fetch {article_url}")
        article = client.download_article(article_url)
        article = _with_article_context(article, issue_titles=issue_titles)
        if article.url in manifest:
            target_dir = Path(manifest[article.url]).expanduser()
            _progress(f"write export {target_dir}")
            write_article_export(target_dir, article)
        else:
            target_dir = write_article(output_dir, article, index=next_index)
            _progress(f"write export {target_dir}")
            next_index += 1
        manifest[article.url] = str(target_dir)
        downloaded_dirs.append(target_dir)
        print(f"downloaded: {article.title}")
        print(f"  {target_dir}")
    write_manifest(output_dir, manifest)
    if create_audio:
        _speak_paths(downloaded_dirs, voice=config.siri_voice, audio_format=audio_format)
    return 0


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


def _existing_article_dir(manifest: dict[str, str], article_url: str) -> Path | None:
    if article_url in manifest:
        return Path(manifest[article_url]).expanduser()
    normalized_url = article_url.rstrip("/")
    if normalized_url in manifest:
        return Path(manifest[normalized_url]).expanduser()
    return None


def _progress(message: str) -> None:
    print(f"progress: {message}", file=sys.stderr, flush=True)


def _download_issue_article(
    *,
    issue_selector: str,
    article_selector: str,
    config: AppConfig,
    output_dir: Path,
    create_audio: bool,
    audio_format: str,
    force: bool = False,
) -> int:
    client = WebClient(config)
    _progress(f"resolve issue {issue_selector}")
    issues = _sort_catalog_issues(client.discover_issues())
    issue_url = _resolve_issue_selector(issues, issue_selector)
    issue = client.discover_issue(issue_url)
    _progress(f"resolve article {article_selector}")
    article_link = _resolve_article_selector(issue.articles, article_selector)
    return _download_articles(
        [article_link.url],
        config,
        output_dir,
        create_audio=create_audio,
        audio_format=audio_format,
        force=force,
        issue_titles={article_link.url: issue.title},
    )


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
    max_articles: int | None,
) -> int:
    client = WebClient(config)
    manifest = read_manifest(output_dir)
    next_index = len(manifest) + 1
    downloaded_dirs: list[Path] = []

    for article_link in article_links:
        if max_articles is not None and len(downloaded_dirs) >= max_articles:
            break
        if article_link.url in manifest:
            continue
        article = client.download_article(article_link.url)
        article = replace(article, issue_title="La settimana di Domino")
        target_dir = write_article_named(
            output_dir,
            article,
            name=_feed_article_folder_name(article_link),
        )
        manifest[article.url] = str(target_dir)
        downloaded_dirs.append(target_dir)
        print(f"downloaded: {article.title}")
        next_index += 1

    write_manifest(output_dir, manifest)
    if create_audio:
        _speak_paths(downloaded_dirs, voice=config.siri_voice, audio_format=audio_format)
    print(f"new_articles: {len(downloaded_dirs)}")
    return 0


def _handle_sync(
    config: AppConfig,
    *,
    create_audio: bool,
    audio_format: str,
    max_articles: int | None,
) -> int:
    client = WebClient(config)
    issues = client.discover_issues()
    output_dir = config.output_dir
    manifest = read_manifest(output_dir)
    downloaded_dirs: list[Path] = []

    for issue in issues:
        issue_detail = client.discover_issue(issue.url)
        issue_dir = output_dir / _issue_folder_name(
            issue_detail.title, issue_detail.published_month
        )
        print(f"issue: {issue_detail.title}")
        group_indexes = _group_indexes(issue_detail.articles)
        for article_link in issue_detail.articles:
            if max_articles is not None and len(downloaded_dirs) >= max_articles:
                break
            if article_link.url in manifest:
                continue
            article = client.download_article(article_link.url)
            article = replace(article, issue_title=issue_detail.title)
            group_name = article_link.group or "Articoli"
            group_dir = issue_dir / _group_folder_name(group_name, group_indexes[group_name])
            target_dir = write_article_named(
                group_dir,
                article,
                name=_article_folder_name(
                    article_link,
                    fallback_index=article_link.order or len(downloaded_dirs) + 1,
                ),
            )
            manifest[article.url] = str(target_dir)
            downloaded_dirs.append(target_dir)
            print(f"  downloaded: {article.title}")
        if max_articles is not None and len(downloaded_dirs) >= max_articles:
            break

    write_manifest(output_dir, manifest)
    if create_audio:
        _speak_paths(downloaded_dirs, voice=config.siri_voice, audio_format=audio_format)
    print(f"new_articles: {len(downloaded_dirs)}")
    return 0


def _issue_folder_name(title: str, published_month: str | None) -> str:
    prefix = published_month or "unknown-month"
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
    date = link.published_date or article_date_from_url(link.url) or "unknown-date"
    order = link.order or fallback_index
    return f"{order:02d}-{date}-{slugify(link.title, fallback='articolo')}"


def _feed_article_folder_name(link: Link) -> str:
    date = link.published_date or article_date_from_url(link.url) or "unknown-date"
    return f"{date}-{slugify(link.title, fallback='articolo')}"


def _handle_sync_feed(
    config: AppConfig,
    *,
    create_audio: bool,
    audio_format: str,
    max_articles: int | None,
    pages: int,
) -> int:
    links = discover_feed_articles(config, max_pages=pages)
    return _download_new_articles(
        links,
        config=config,
        output_dir=config.feed_output_dir,
        create_audio=create_audio,
        audio_format=audio_format,
        max_articles=max_articles,
    )


def _resolve_text_paths(output_dir: Path, paths: list[Path]) -> list[Path]:
    if paths:
        candidates = paths
    else:
        candidates = sorted(output_dir.glob("*/article.txt"))

    text_paths: list[Path] = []
    for path in candidates:
        if path.is_dir():
            text_paths.append(path / "article.txt")
        else:
            text_paths.append(path)
    return text_paths


def _speak_paths(paths: list[Path], *, voice: str | None, audio_format: str) -> int:
    normalized_format = normalize_audio_format(audio_format)
    for raw_path in paths:
        text_path = raw_path / "article.txt" if raw_path.is_dir() else raw_path
        if not text_path.exists():
            raise AudioError(f"Text file not found: {text_path}")
        output_path = text_path.with_suffix(f".{normalized_format}")
        _progress(f"audio start {output_path.name}")
        synthesize_audio(text_path, output_path, voice=voice, audio_format=normalized_format)
        print(f"audio: {output_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or args_list == ["--help"] or args_list == ["-h"]:
        print(format_main_help())
        return 0

    if args_list == ["--version"]:
        print(__version__)
        return 0

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
            output_dir = args.output_dir or config.output_dir
            create_audio, audio_format = _audio_options(args, config)
            if args.issue or args.article:
                if not args.issue or not args.article:
                    raise ValueError("Use --issue and --article together.")
                if args.article_urls:
                    raise ValueError("Do not pass article URLs with --issue/--article.")
                return _download_issue_article(
                    issue_selector=str(args.issue),
                    article_selector=str(args.article),
                    config=config,
                    output_dir=output_dir,
                    create_audio=create_audio,
                    audio_format=audio_format,
                    force=bool(args.force),
                )
            if not args.article_urls:
                raise ValueError("Pass one or more article URLs, or use --issue/--article.")
            return _download_articles(
                list(args.article_urls),
                config,
                output_dir,
                create_audio=create_audio,
                audio_format=audio_format,
                force=bool(args.force),
            )
        if args.command in {"sync-magazine", "sync"}:
            create_audio, audio_format = _audio_options(args, config)
            return _handle_sync(
                config,
                create_audio=create_audio,
                audio_format=audio_format,
                max_articles=args.max_articles,
            )
        if args.command in {"sync-feed", "sync-weekly"}:
            create_audio, audio_format = _audio_options(args, config)
            return _handle_sync_feed(
                config,
                create_audio=create_audio,
                audio_format=audio_format,
                max_articles=args.max_articles,
                pages=int(args.pages),
            )
        if args.command == "speak":
            voice = args.voice if args.voice is not None else config.siri_voice
            audio_format = normalize_audio_format(args.audio_format or config.audio_format)
            return _speak_paths(
                _resolve_text_paths(config.output_dir, list(args.paths)),
                voice=voice,
                audio_format=audio_format,
            )
        if args.command == "voices":
            return _handle_voices(all_voices=bool(args.all))
    except (AudioError, BrowserAuthError, FetchError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(format_main_help())
    return 0
