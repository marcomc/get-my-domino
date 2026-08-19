"""Podcast RSS and static index generation for local feed collections."""

from __future__ import annotations

import calendar
import email.utils
import html
import math
import re
import shutil
import struct
import time
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path

from .audio import normalize_audio_format
from .storage import article_basename, read_json_object, write_json_object

FEED_COLLECTION_INFO_FILE = ".feed-info.json"
PODCAST_FEED_FILE = "feed.xml"
PODCAST_INDEX_FILE = "index.html"
PODCAST_APPLE_TOUCH_ICON_FILE = "apple-touch-icon.png"
PODCAST_REMOTE_ARTWORK_FILE = "cover.png"
PODCAST_AUDIO_SUFFIXES = {".m4a", ".mp3"}
DOMINO_OFFICIAL_ARTWORK_URL = (
    "https://www.rivistadomino.it/wp-content/uploads/2023/07/cropped-145-180x180.png"
)
DOMINO_RED = (155, 28, 49, 255)
DOMINO_BLACK = (20, 20, 20, 255)
DOMINO_WHITE = (247, 247, 244, 255)
ICON_TOP_RED = (184, 31, 53, 255)
ICON_BOTTOM_RED = (116, 13, 31, 255)
_DATE_IN_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class FeedCollectionDetails:
    slug: str
    title: str
    author: str
    description: str
    page_url: str
    artwork_file: str = ""
    artwork_url: str = ""


@dataclass(frozen=True)
class PodcastEpisode:
    title: str
    guid: str
    link: str
    published_at: int
    published_date: str
    audio_path: Path


@dataclass(frozen=True)
class PodcastCollectionOutput:
    source_dir: Path
    output_dir: Path
    details: FeedCollectionDetails
    episodes: tuple[PodcastEpisode, ...]
    artwork_path: Path | None


def normalize_podcast_audio_format(value: str) -> str:
    return normalize_audio_format(value)


def media_type_for_suffix(path: Path) -> str:
    return {
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
    }.get(path.suffix.lower(), "audio/mpeg")


def xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def write_feed_collection_details(collection_dir: Path, details: FeedCollectionDetails) -> Path:
    path = collection_dir / FEED_COLLECTION_INFO_FILE
    write_json_object(
        path,
        {
            "slug": details.slug,
            "title": details.title,
            "author": details.author,
            "description": details.description,
            "page_url": details.page_url,
            "artwork_file": details.artwork_file,
            "artwork_url": details.artwork_url,
        },
    )
    return path


def ensure_feed_collection_details(
    collection_dir: Path, details: FeedCollectionDetails
) -> FeedCollectionDetails:
    payload = read_json_object(collection_dir / FEED_COLLECTION_INFO_FILE)
    if payload:
        merged = FeedCollectionDetails(
            slug=_metadata_string(payload, "slug") or details.slug or collection_dir.name,
            title=_metadata_string(payload, "title") or details.title,
            author=_metadata_string(payload, "author") or details.author,
            description=_metadata_string(payload, "description") or details.description,
            page_url=_metadata_string(payload, "page_url") or details.page_url,
            artwork_file=_metadata_string(payload, "artwork_file") or details.artwork_file,
            artwork_url=_metadata_string(payload, "artwork_url") or details.artwork_url,
        )
        if merged != load_feed_collection_details(collection_dir):
            write_feed_collection_details(collection_dir, merged)
        return merged
    write_feed_collection_details(collection_dir, details)
    return details


def load_feed_collection_details(collection_dir: Path) -> FeedCollectionDetails | None:
    payload = read_json_object(collection_dir / FEED_COLLECTION_INFO_FILE)
    if not payload:
        return None
    slug = str(payload.get("slug") or collection_dir.name)
    title = str(payload.get("title") or _title_from_slug(slug))
    return FeedCollectionDetails(
        slug=slug,
        title=title,
        author=str(payload.get("author") or "Domino"),
        description=str(payload.get("description") or title),
        page_url=str(payload.get("page_url") or ""),
        artwork_file=str(payload.get("artwork_file") or ""),
        artwork_url=str(payload.get("artwork_url") or ""),
    )


def generate_collection_feed(
    collection: PodcastCollectionOutput,
    podcast_output_dir: Path,
    base_url: str = "",
    *,
    audio_format: str,
) -> Path:
    normalize_podcast_audio_format(audio_format)
    details = collection.details
    episodes = collection.episodes
    collection_dir = collection.output_dir
    artwork_path = collection.artwork_path
    artwork_url = (
        _url_for_artifact(artwork_path, podcast_output_dir, base_url)
        if artwork_path is not None and artwork_path.exists()
        else ""
    )
    channel_link = details.page_url or _collection_url(collection_dir, podcast_output_dir, base_url)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">',
        "  <channel>",
        f"    <title>{xml_escape(details.title)}</title>",
        f"    <link>{xml_escape(channel_link)}</link>",
        f"    <description>{xml_escape(details.description or details.title)}</description>",
        "    <language>it</language>",
        f"    <itunes:title>{xml_escape(details.title)}</itunes:title>",
        f"    <itunes:author>{xml_escape(details.author)}</itunes:author>",
        "    <itunes:explicit>false</itunes:explicit>",
    ]
    if artwork_url:
        lines.extend(
            [
                "    <image>",
                f"      <url>{xml_escape(artwork_url)}</url>",
                f"      <title>{xml_escape(details.title)}</title>",
                f"      <link>{xml_escape(channel_link)}</link>",
                "    </image>",
                f'    <itunes:image href="{xml_escape(artwork_url)}"/>',
            ]
        )
    for episode in sorted(episodes, key=lambda item: (item.published_at, item.guid), reverse=True):
        enclosure = _url_for_artifact(episode.audio_path, podcast_output_dir, base_url)
        pub_date = email.utils.formatdate(episode.published_at, usegmt=True)
        try:
            size = str(episode.audio_path.stat().st_size)
        except OSError:
            size = "0"
        lines.extend(
            [
                "    <item>",
                f"      <title>{xml_escape(episode.title)}</title>",
                f"      <link>{xml_escape(episode.link or channel_link)}</link>",
                f'      <guid isPermaLink="false">{xml_escape(episode.guid)}</guid>',
                f"      <pubDate>{pub_date}</pubDate>",
                (
                    f'      <enclosure url="{xml_escape(enclosure)}" '
                    f'length="{size}" '
                    f'type="{xml_escape(media_type_for_suffix(episode.audio_path))}"/>'
                ),
                "    </item>",
            ]
        )
    lines.extend(["  </channel>", "</rss>"])
    feed_path = collection_dir / PODCAST_FEED_FILE
    feed_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return feed_path


def generate_podcast_index(
    podcast_output_dir: Path,
    collections: list[PodcastCollectionOutput],
    base_url: str = "",
    *,
    audio_format: str,
    apple_podcasts: bool = True,
) -> Path:
    podcast_output_dir.mkdir(parents=True, exist_ok=True)
    _write_apple_touch_icon(podcast_output_dir / PODCAST_APPLE_TOUCH_ICON_FILE)
    normalized_format = normalize_podcast_audio_format(audio_format)
    items = [
        item
        for collection in collections
        for item in [
            _collection_index_item(
                collection,
                podcast_output_dir,
                base_url,
                audio_format=normalized_format,
            )
        ]
        if item is not None
    ]
    rows = [_index_row(item, apple_podcasts=apple_podcasts) for item in items]
    content = f"""<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="DominoPodcast">
  <meta name="theme-color" content="#f7f7f4">
  <link rel="apple-touch-icon" href="{PODCAST_APPLE_TOUCH_ICON_FILE}">
  <title>DominoPodcast</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f7f4;
      --ink: #171717;
      --muted: #6b6f76;
      --line: rgba(24, 24, 21, 0.14);
      --panel: rgba(255, 255, 255, 0.88);
      --accent: #9b1c31;
      --accent-soft: rgba(155, 28, 49, 0.12);
      --podcast: #5f2eea;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      background: var(--bg);
    }}
    main {{
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      padding: 40px 0;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: end;
      padding: 0 2px 22px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0;
      font-size: 3rem;
      line-height: 1;
      letter-spacing: 0;
    }}
    .summary {{
      margin: 0;
      color: var(--muted);
      font-size: 0.98rem;
      text-align: right;
    }}
    .collection-list {{
      display: grid;
      gap: 14px;
      margin-top: 20px;
    }}
    .collection-row {{
      display: grid;
      grid-template-columns: 112px minmax(0, 1fr);
      gap: 18px;
      align-items: center;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 14px 34px rgba(15, 23, 42, 0.07);
    }}
    .cover {{
      display: block;
      width: 112px;
      aspect-ratio: 1;
      overflow: hidden;
      border-radius: 8px;
      background: #ece8df;
      box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.68);
    }}
    .cover img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}
    .cover-placeholder {{
      width: 100%;
      height: 100%;
      display: grid;
      place-items: center;
      background: linear-gradient(135deg, #171717, #9b1c31);
      color: #fff;
      font-size: 2.2rem;
      font-weight: 800;
      letter-spacing: 0;
    }}
    .collection-title {{
      color: var(--ink);
      font-size: 1.28rem;
      font-weight: 760;
      text-decoration: none;
    }}
    .collection-title:hover, .feed:hover {{ color: var(--accent); }}
    .collection-author {{
      margin: 4px 0 0;
      color: var(--muted);
      font-weight: 600;
    }}
    .collection-description {{
      margin: 10px 0 0;
      color: #30343a;
      line-height: 1.45;
    }}
    .collection-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .collection-meta span, .feed, .apple-podcasts {{
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(237, 237, 231, 0.92);
      color: inherit;
      text-decoration: none;
    }}
    .feed {{
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
    }}
    .apple-podcasts {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(95, 46, 234, 0.12);
      color: var(--podcast);
      font-weight: 700;
    }}
    @media (max-width: 680px) {{
      main {{ width: min(100% - 20px, 1120px); padding: 24px 0; }}
      header {{ display: block; }}
      h1 {{ font-size: 2.2rem; }}
      .summary {{ margin-top: 8px; text-align: left; }}
      .collection-row {{ grid-template-columns: 82px minmax(0, 1fr); gap: 12px; }}
      .cover {{ width: 82px; }}
      .collection-title {{ font-size: 1.08rem; }}
      .collection-description {{ font-size: 0.94rem; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>DominoPodcast</h1>
      <p class="summary">{len(items)} raccolte sincronizzate</p>
    </header>
    <section class="collection-list" aria-label="Raccolte">
{chr(10).join(rows)}
    </section>
  </main>
</body>
</html>
"""
    index_path = podcast_output_dir / PODCAST_INDEX_FILE
    index_path.write_text(content, encoding="utf-8")
    return index_path


def generate_podcast_outputs(
    library_dir: Path,
    podcast_output_dir: Path,
    base_url: str = "",
    *,
    rss: bool = False,
    index: bool = False,
    audio_format: str,
    apple_podcasts: bool = True,
) -> dict[str, int | Path | None]:
    validate_podcast_output_dir(library_dir, podcast_output_dir)
    library_dir.mkdir(parents=True, exist_ok=True)
    podcast_output_dir.mkdir(parents=True, exist_ok=True)
    normalized_format = normalize_podcast_audio_format(audio_format)
    collections = (
        _prepare_podcast_collections(
            library_dir,
            podcast_output_dir,
            audio_format=normalized_format,
        )
        if rss
        else _read_podcast_collections(
            library_dir,
            podcast_output_dir,
            audio_format=normalized_format,
        )
    )
    rss_count = 0
    for collection in collections:
        if rss:
            generate_collection_feed(
                collection,
                podcast_output_dir,
                base_url,
                audio_format=normalized_format,
            )
            rss_count += 1
    index_path = (
        generate_podcast_index(
            podcast_output_dir,
            collections,
            base_url,
            audio_format=normalized_format,
            apple_podcasts=apple_podcasts,
        )
        if index
        else None
    )
    return {"rss": rss_count, "index": index_path}


def validate_podcast_output_dir(library_dir: Path, podcast_output_dir: Path) -> None:
    normalized_library_dir = _normalized_path(library_dir)
    normalized_podcast_output_dir = _normalized_path(podcast_output_dir)
    if normalized_library_dir == normalized_podcast_output_dir:
        raise ValueError("podcast_output_dir must not be library_dir.")
    if normalized_podcast_output_dir.is_relative_to(normalized_library_dir):
        raise ValueError("podcast_output_dir must not be inside library_dir.")


def _prepare_podcast_collections(
    library_dir: Path,
    podcast_output_dir: Path,
    *,
    audio_format: str,
) -> list[PodcastCollectionOutput]:
    collections: list[PodcastCollectionOutput] = []
    for source_collection_dir in _iter_collection_dirs(library_dir):
        details = _collection_details(source_collection_dir)
        target_collection_dir = podcast_output_dir / (details.slug or source_collection_dir.name)
        source_episodes = _collection_episodes(source_collection_dir, audio_format=audio_format)
        if not source_episodes:
            _remove_stale_collection_output(target_collection_dir)
            continue
        target_collection_dir.mkdir(parents=True, exist_ok=True)
        _remove_stale_audio_outputs(source_episodes, target_collection_dir)
        episodes = tuple(_copy_episode_audio(source_episodes, target_collection_dir))
        artwork_path = _ensure_collection_artwork(
            details,
            source_collection_dir,
            target_collection_dir,
        )
        collections.append(
            PodcastCollectionOutput(
                source_dir=source_collection_dir,
                output_dir=target_collection_dir,
                details=details,
                episodes=episodes,
                artwork_path=artwork_path,
            )
        )
    return collections


def _read_podcast_collections(
    library_dir: Path,
    podcast_output_dir: Path,
    *,
    audio_format: str,
) -> list[PodcastCollectionOutput]:
    collections: list[PodcastCollectionOutput] = []
    for source_collection_dir in _iter_collection_dirs(library_dir):
        details = _collection_details(source_collection_dir)
        target_collection_dir = podcast_output_dir / (details.slug or source_collection_dir.name)
        target_episodes = _collection_published_episodes(
            source_collection_dir,
            target_collection_dir,
            audio_format=audio_format,
        )
        if not target_episodes:
            continue
        artwork_path = _collection_artwork_path(details, target_collection_dir)
        collections.append(
            PodcastCollectionOutput(
                source_dir=source_collection_dir,
                output_dir=target_collection_dir,
                details=details,
                episodes=tuple(target_episodes),
                artwork_path=artwork_path,
            )
        )
    return collections


def _normalized_path(path: Path) -> Path:
    return (
        Path.cwd().joinpath(path).resolve(strict=False)
        if not path.is_absolute()
        else path.resolve(strict=False)
    )


def _remove_stale_collection_output(target_collection_dir: Path) -> None:
    if not target_collection_dir.exists():
        return
    shutil.rmtree(target_collection_dir)


def _remove_stale_audio_outputs(
    source_episodes: list[PodcastEpisode],
    target_collection_dir: Path,
) -> None:
    expected_names = {episode.audio_path.name for episode in source_episodes}
    for path in target_collection_dir.iterdir():
        if (
            path.is_file()
            and path.suffix.lower() in PODCAST_AUDIO_SUFFIXES
            and path.name not in expected_names
        ):
            path.unlink()


def _copy_episode_audio(
    episodes: list[PodcastEpisode],
    target_collection_dir: Path,
) -> list[PodcastEpisode]:
    copied: list[PodcastEpisode] = []
    used_names: set[str] = set()
    for episode in sorted(episodes, key=lambda item: (item.published_at, item.guid), reverse=True):
        target_name = _unique_filename(episode.audio_path.name, used_names)
        target_audio_path = target_collection_dir / target_name
        _copy_file(episode.audio_path, target_audio_path)
        copied.append(
            PodcastEpisode(
                title=episode.title,
                guid=episode.guid,
                link=episode.link,
                published_at=episode.published_at,
                published_date=episode.published_date,
                audio_path=target_audio_path,
            )
        )
    return copied


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if source.resolve() == target.resolve():
            return
    except OSError:
        pass
    shutil.copy2(source, target)


def _unique_filename(filename: str, used_names: set[str]) -> str:
    if filename not in used_names:
        used_names.add(filename)
        return filename
    path = Path(filename)
    counter = 2
    while True:
        candidate = f"{path.stem}-{counter}{path.suffix}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        counter += 1


def _ensure_collection_artwork(
    details: FeedCollectionDetails,
    source_collection_dir: Path,
    target_collection_dir: Path,
) -> Path | None:
    if details.artwork_file:
        source_artwork = source_collection_dir / details.artwork_file
        target_artwork = target_collection_dir / details.artwork_file
        if source_artwork.exists():
            _copy_file(source_artwork, target_artwork)
            return target_artwork
        if target_artwork.exists():
            return target_artwork
    if not details.artwork_url:
        return None
    target_artwork = target_collection_dir / PODCAST_REMOTE_ARTWORK_FILE
    if target_artwork.exists():
        return target_artwork
    try:
        target_artwork.write_bytes(_download_artwork(details.artwork_url))
    except OSError:
        return None
    return target_artwork


def _collection_artwork_path(
    details: FeedCollectionDetails,
    target_collection_dir: Path,
) -> Path | None:
    if details.artwork_file:
        target_artwork = target_collection_dir / details.artwork_file
        if target_artwork.exists():
            return target_artwork
    target_artwork = target_collection_dir / PODCAST_REMOTE_ARTWORK_FILE
    return target_artwork if target_artwork.exists() else None


def _download_artwork(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "get-my-domino podcast output"},
    )
    with urllib.request.urlopen(request, timeout=30.0) as response:
        return bytes(response.read())


def _index_row(item: dict[str, str | int], *, apple_podcasts: bool) -> str:
    folder_href = html.escape(str(item["folder_href"]), quote=True)
    title = html.escape(str(item["title"]))
    author = html.escape(str(item["author"]))
    description = html.escape(str(item["description"]) or "Descrizione non disponibile.")
    image = (
        f'<img src="{html.escape(str(item["artwork_href"]), quote=True)}" '
        f'alt="{html.escape(str(item["title"]), quote=True)}">'
        if item["artwork_href"]
        else (
            '<div class="cover-placeholder" aria-hidden="true">'
            f"{html.escape(str(item['initial']))}</div>"
        )
    )
    feed = (
        f'<a class="feed" href="{html.escape(str(item["feed_href"]), quote=True)}">RSS</a>'
        if item["feed_href"]
        else ""
    )
    apple_podcasts_href = _apple_podcasts_href(str(item["feed_href"])) if apple_podcasts else ""
    apple_podcasts_link = (
        '<a class="apple-podcasts" '
        f'href="{html.escape(apple_podcasts_href, quote=True)}" '
        f'aria-label="Apri {title} in Apple Podcasts" '
        'title="Apri in Apple Podcasts">Apple Podcasts</a>'
        if apple_podcasts_href
        else ""
    )
    latest = str(item["latest_date"] or "Nessun episodio locale")
    return (
        '      <article class="collection-row">'
        f'<a class="cover" href="{folder_href}">{image}</a>'
        '<div class="collection-copy">'
        f'<a class="collection-title" href="{folder_href}">{title}</a>'
        f'<p class="collection-author">{author}</p>'
        f'<p class="collection-description">{description}</p>'
        '<div class="collection-meta">'
        f"<span>{item['episode_count']} episodi</span>"
        f"<span>Ultimo: {html.escape(latest)}</span>"
        f"{feed}"
        f"{apple_podcasts_link}"
        "</div>"
        "</div>"
        "</article>"
    )


def _collection_index_item(
    collection: PodcastCollectionOutput,
    podcast_output_dir: Path,
    base_url: str,
    *,
    audio_format: str,
) -> dict[str, str | int] | None:
    normalize_podcast_audio_format(audio_format)
    collection_dir = collection.output_dir
    episodes = list(collection.episodes)
    if not episodes:
        return None
    details = collection.details
    latest = max((episode.published_date for episode in episodes), default="")
    folder_href = f"{_quote_path_parts(collection_dir.name)}/"
    feed_path = collection_dir / PODCAST_FEED_FILE
    feed_href = (
        _url_for_artifact(feed_path, podcast_output_dir, base_url) if feed_path.exists() else ""
    )
    artwork_path = collection.artwork_path
    artwork_href = (
        _quote_path_parts(collection_dir.name, artwork_path.name)
        if artwork_path is not None and artwork_path.exists()
        else ""
    )
    return {
        "slug": collection_dir.name,
        "title": details.title,
        "author": details.author,
        "description": details.description,
        "folder_href": folder_href,
        "feed_href": feed_href,
        "artwork_href": artwork_href,
        "episode_count": len(episodes),
        "latest_date": latest,
        "initial": _initial(details.title),
    }


def _collection_episodes(collection_dir: Path, *, audio_format: str) -> list[PodcastEpisode]:
    episodes: list[PodcastEpisode] = []
    for article_dir in _iter_article_dirs(collection_dir):
        audio_path = _article_audio_path(article_dir, audio_format=audio_format)
        if audio_path is None:
            continue
        metadata = read_json_object(article_dir / "metadata.json")
        published_date = _metadata_string(metadata, "published_date") or _date_from_name(
            article_dir.name
        )
        published_at = _published_at(published_date, fallback_path=audio_path)
        title = _metadata_string(metadata, "title") or _title_from_slug(article_dir.name)
        feed_number = _metadata_int(metadata, "feed_number")
        if feed_number is not None:
            title = f"#{feed_number} - {title}"
        guid = _metadata_string(metadata, "url") or f"{collection_dir.name}/{article_dir.name}"
        episodes.append(
            PodcastEpisode(
                title=title,
                guid=guid,
                link=_metadata_string(metadata, "url") or "",
                published_at=published_at,
                published_date=published_date or _date_from_timestamp(published_at),
                audio_path=audio_path,
            )
        )
    return episodes


def _collection_published_episodes(
    source_collection_dir: Path,
    target_collection_dir: Path,
    *,
    audio_format: str,
) -> list[PodcastEpisode]:
    episodes: list[PodcastEpisode] = []
    for source_episode in _collection_episodes(source_collection_dir, audio_format=audio_format):
        target_audio_path = target_collection_dir / source_episode.audio_path.name
        if not target_audio_path.exists():
            continue
        episodes.append(
            PodcastEpisode(
                title=source_episode.title,
                guid=source_episode.guid,
                link=source_episode.link,
                published_at=source_episode.published_at,
                published_date=source_episode.published_date,
                audio_path=target_audio_path,
            )
        )
    if episodes:
        return episodes
    return _published_audio_episodes(source_collection_dir, target_collection_dir)


def _published_audio_episodes(
    source_collection_dir: Path,
    target_collection_dir: Path,
) -> list[PodcastEpisode]:
    try:
        audio_paths = [
            path
            for path in sorted(
                target_collection_dir.iterdir(), key=lambda item: item.name.casefold()
            )
            if path.is_file() and path.suffix.lower() in PODCAST_AUDIO_SUFFIXES
        ]
    except OSError:
        return []
    episodes: list[PodcastEpisode] = []
    for audio_path in audio_paths:
        published_date = _date_from_name(audio_path.stem)
        published_at = _published_at(published_date, fallback_path=audio_path)
        episodes.append(
            PodcastEpisode(
                title=_title_from_slug(audio_path.stem),
                guid=f"{source_collection_dir.name}/{audio_path.name}",
                link="",
                published_at=published_at,
                published_date=published_date or _date_from_timestamp(published_at),
                audio_path=audio_path,
            )
        )
    return episodes


def _article_audio_path(article_dir: Path, *, audio_format: str) -> Path | None:
    suffix = f".{normalize_podcast_audio_format(audio_format)}"
    canonical_path = article_dir / f"{article_basename(article_dir)}{suffix}"
    if canonical_path.exists():
        return canonical_path
    candidates = sorted(
        path
        for path in article_dir.glob(f"*{suffix}")
        if path.is_file() and path.suffix.lower() in PODCAST_AUDIO_SUFFIXES
    )
    if len(candidates) == 1:
        return candidates[0]
    return None


def _collection_details(collection_dir: Path) -> FeedCollectionDetails:
    cached = load_feed_collection_details(collection_dir)
    if cached is not None:
        return cached
    inferred_title = _inferred_collection_title(collection_dir)
    return FeedCollectionDetails(
        slug=collection_dir.name,
        title=inferred_title,
        author="Domino",
        description=inferred_title,
        page_url="",
    )


def _inferred_collection_title(collection_dir: Path) -> str:
    for article_dir in _iter_article_dirs(collection_dir):
        feed_name = _metadata_string(read_json_object(article_dir / "metadata.json"), "feed")
        if feed_name:
            return feed_name
    return _title_from_slug(collection_dir.name)


def _iter_collection_dirs(library_dir: Path) -> list[Path]:
    try:
        children = list(library_dir.iterdir())
    except OSError:
        return []
    collection_dirs: list[Path] = []
    for child in sorted(children, key=lambda item: item.name.casefold()):
        if child.name.startswith(".") or child.name in {"rivista"}:
            continue
        try:
            if child.is_dir():
                collection_dirs.append(child)
        except OSError:
            continue
    return collection_dirs


def _iter_article_dirs(collection_dir: Path) -> list[Path]:
    try:
        children = list(collection_dir.iterdir())
    except OSError:
        return []
    article_dirs: list[Path] = []
    for child in sorted(children, key=lambda item: item.name.casefold()):
        if child.name.startswith("."):
            continue
        try:
            if child.is_dir():
                article_dirs.append(child)
        except OSError:
            continue
    return article_dirs


def _metadata_string(metadata: dict[str, object], key: str) -> str:
    value = metadata.get(key)
    return str(value).strip() if value is not None else ""


def _metadata_int(metadata: dict[str, object], key: str) -> int | None:
    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _title_from_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.replace("_", "-").split("-") if part)


def _initial(title: str) -> str:
    stripped = title.strip()
    return stripped[:1].upper() if stripped else "D"


def _write_apple_touch_icon(path: Path) -> None:
    width = 180
    height = 180
    scale = 4
    canvas_width = width * scale
    canvas_height = height * scale
    pixels = _icon_background(canvas_width, canvas_height)
    center_x = 90 * scale
    center_y = 91 * scale
    tile_width = 78 * scale
    tile_height = 120 * scale
    radius = 17 * scale
    angle = math.radians(-8)
    _draw_rotated_rounded_rect(
        pixels,
        canvas_width,
        canvas_height,
        center_x + 4 * scale,
        center_y + 8 * scale,
        tile_width,
        tile_height,
        radius,
        angle,
        (0, 0, 0, 82),
    )
    _draw_rotated_rounded_rect(
        pixels,
        canvas_width,
        canvas_height,
        center_x,
        center_y,
        tile_width,
        tile_height,
        radius,
        angle,
        (16, 16, 16, 255),
    )
    _draw_rotated_rounded_rect(
        pixels,
        canvas_width,
        canvas_height,
        center_x - 2 * scale,
        center_y - 4 * scale,
        64 * scale,
        102 * scale,
        13 * scale,
        angle,
        (42, 42, 42, 52),
    )
    _draw_rotated_rounded_rect(
        pixels,
        canvas_width,
        canvas_height,
        center_x,
        center_y,
        58 * scale,
        6 * scale,
        3 * scale,
        angle,
        (238, 235, 224, 234),
    )
    for local_x, local_y in ((-18, -37), (17, -28), (-11, -7), (-20, 26), (18, 16), (11, 43)):
        pip_x, pip_y = _rotated_point(center_x, center_y, local_x * scale, local_y * scale, angle)
        _draw_circle(
            pixels,
            canvas_width,
            canvas_height,
            round(pip_x),
            round(pip_y),
            7 * scale,
            DOMINO_WHITE,
        )
    icon = _downsample_rgba(bytes(pixels), canvas_width, canvas_height, width, height, scale)
    _write_rgba_png(path, width, height, icon)


def _icon_background(width: int, height: int) -> bytearray:
    pixels = bytearray(width * height * 4)
    highlight_x = width * 0.34
    highlight_y = height * 0.16
    max_distance = math.hypot(width, height)
    for y in range(height):
        t = y / max(1, height - 1)
        for x in range(width):
            red = _lerp(ICON_TOP_RED[0], ICON_BOTTOM_RED[0], t)
            green = _lerp(ICON_TOP_RED[1], ICON_BOTTOM_RED[1], t)
            blue = _lerp(ICON_TOP_RED[2], ICON_BOTTOM_RED[2], t)
            highlight = max(0.0, 1.0 - math.hypot(x - highlight_x, y - highlight_y) / max_distance)
            vignette = math.hypot(x - width / 2, y - height / 2) / max_distance
            color = (
                _clamp(round(red + highlight * 28 - vignette * 28)),
                _clamp(round(green + highlight * 10 - vignette * 9)),
                _clamp(round(blue + highlight * 6 - vignette * 7)),
                255,
            )
            _set_pixel(pixels, width, x, y, color)
    return pixels


def _draw_rotated_rounded_rect(
    pixels: bytearray,
    width: int,
    height: int,
    center_x: float,
    center_y: float,
    rect_width: float,
    rect_height: float,
    radius: float,
    angle: float,
    color: tuple[int, int, int, int],
) -> None:
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    extent = math.ceil(math.hypot(rect_width, rect_height) / 2 + radius)
    left = max(0, math.floor(center_x - extent))
    right = min(width, math.ceil(center_x + extent))
    top = max(0, math.floor(center_y - extent))
    bottom = min(height, math.ceil(center_y + extent))
    half_width = rect_width / 2
    half_height = rect_height / 2
    for y in range(top, bottom):
        for x in range(left, right):
            dx = x + 0.5 - center_x
            dy = y + 0.5 - center_y
            local_x = dx * cos_a + dy * sin_a
            local_y = -dx * sin_a + dy * cos_a
            if _inside_rounded_rect(local_x, local_y, half_width, half_height, radius):
                _blend_pixel(pixels, width, x, y, color)


def _inside_rounded_rect(
    x: float,
    y: float,
    half_width: float,
    half_height: float,
    radius: float,
) -> bool:
    inner_x = half_width - radius
    inner_y = half_height - radius
    dx = abs(x) - inner_x
    dy = abs(y) - inner_y
    if dx <= 0 and abs(y) <= half_height:
        return True
    if dy <= 0 and abs(x) <= half_width:
        return True
    return max(dx, 0) ** 2 + max(dy, 0) ** 2 <= radius**2


def _rotated_point(
    center_x: float,
    center_y: float,
    local_x: float,
    local_y: float,
    angle: float,
) -> tuple[float, float]:
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    return (
        center_x + local_x * cos_a - local_y * sin_a,
        center_y + local_x * sin_a + local_y * cos_a,
    )


def _draw_circle(
    pixels: bytearray,
    width: int,
    height: int,
    center_x: int,
    center_y: int,
    radius: int,
    color: tuple[int, int, int, int],
) -> None:
    radius_squared = radius**2
    for y in range(max(0, center_y - radius), min(height, center_y + radius + 1)):
        for x in range(max(0, center_x - radius), min(width, center_x + radius + 1)):
            if (x - center_x) ** 2 + (y - center_y) ** 2 <= radius_squared:
                _blend_pixel(pixels, width, x, y, color)


def _set_pixel(
    pixels: bytearray,
    width: int,
    x: int,
    y: int,
    color: tuple[int, int, int, int],
) -> None:
    offset = (y * width + x) * 4
    pixels[offset : offset + 4] = bytes(color)


def _blend_pixel(
    pixels: bytearray,
    width: int,
    x: int,
    y: int,
    color: tuple[int, int, int, int],
) -> None:
    offset = (y * width + x) * 4
    alpha = color[3] / 255
    inverse = 1 - alpha
    pixels[offset] = _clamp(round(color[0] * alpha + pixels[offset] * inverse))
    pixels[offset + 1] = _clamp(round(color[1] * alpha + pixels[offset + 1] * inverse))
    pixels[offset + 2] = _clamp(round(color[2] * alpha + pixels[offset + 2] * inverse))
    pixels[offset + 3] = 255


def _downsample_rgba(
    rgba: bytes,
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
    scale: int,
) -> bytes:
    del source_height
    target = bytearray(target_width * target_height * 4)
    for target_y in range(target_height):
        for target_x in range(target_width):
            totals = [0, 0, 0, 0]
            for y in range(target_y * scale, (target_y + 1) * scale):
                for x in range(target_x * scale, (target_x + 1) * scale):
                    offset = (y * source_width + x) * 4
                    totals[0] += rgba[offset]
                    totals[1] += rgba[offset + 1]
                    totals[2] += rgba[offset + 2]
                    totals[3] += rgba[offset + 3]
            pixel_count = scale * scale
            offset = (target_y * target_width + target_x) * 4
            target[offset : offset + 4] = bytes(round(channel / pixel_count) for channel in totals)
    return bytes(target)


def _lerp(start: int, end: int, amount: float) -> float:
    return start + (end - start) * amount


def _clamp(value: int) -> int:
    return max(0, min(255, value))


def _write_rgba_png(path: Path, width: int, height: int, rgba: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw.extend(rgba[y * stride : (y + 1) * stride])
    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(_png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
    png.extend(_png_chunk(b"IDAT", zlib.compress(bytes(raw), level=9)))
    png.extend(_png_chunk(b"IEND", b""))
    path.write_bytes(bytes(png))


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def _date_from_name(name: str) -> str:
    match = _DATE_IN_NAME_RE.search(name)
    return match.group(1) if match else ""


def _published_at(date_text: str, *, fallback_path: Path) -> int:
    if date_text:
        try:
            return calendar.timegm(time.strptime(date_text, "%Y-%m-%d"))
        except ValueError:
            pass
    try:
        return int(fallback_path.stat().st_mtime)
    except OSError:
        return int(time.time())


def _date_from_timestamp(timestamp: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(timestamp))


def _url_for_artifact(path: Path, library_dir: Path, base_url: str) -> str:
    if base_url:
        try:
            relative = path.relative_to(library_dir)
        except ValueError:
            relative = Path(path.name)
        return f"{base_url.rstrip('/')}/{_quote_path_parts(*relative.parts)}"
    return path.resolve().as_uri()


def _collection_url(collection_dir: Path, library_dir: Path, base_url: str) -> str:
    if base_url:
        try:
            relative = collection_dir.relative_to(library_dir)
        except ValueError:
            relative = Path(collection_dir.name)
        return f"{base_url.rstrip('/')}/{_quote_path_parts(*relative.parts)}/"
    return collection_dir.resolve().as_uri()


def _quote_path_parts(*parts: str) -> str:
    return "/".join(urllib.parse.quote(part, safe="") for part in parts)


def _apple_podcasts_href(feed_href: str) -> str:
    parsed = urllib.parse.urlparse(feed_href)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"pcast://{parsed.netloc}{path}{query}{fragment}"
