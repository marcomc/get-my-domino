from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

import get_my_domino.podcast_outputs as podcast_outputs
from get_my_domino.podcast_outputs import (
    FeedCollectionDetails,
    generate_podcast_outputs,
    validate_podcast_output_dir,
    write_feed_collection_details,
)


def test_generate_podcast_outputs_writes_feed_and_index(tmp_path: Path) -> None:
    library_dir = tmp_path / "library"
    podcast_dir = tmp_path / "podcasts"
    collection_dir = library_dir / "la-settimana-di-domino"
    article_dir = collection_dir / "2026-04-24-usa-e-globalizzazione"
    article_dir.mkdir(parents=True)
    source_artwork = collection_dir / "domino-official.png"
    source_artwork.write_bytes(b"png")
    write_feed_collection_details(
        collection_dir,
        FeedCollectionDetails(
            slug="la-settimana-di-domino",
            title="La settimana di Domino",
            author="Domino",
            description="La raccolta settimanale.",
            page_url="https://www.rivistadomino.it/blog/category/la-settimana-di-domino/",
            artwork_file="domino-official.png",
        ),
    )
    (article_dir / "metadata.json").write_text(
        "\n".join(
            [
                "{",
                '  "title": "USA e globalizzazione",',
                '  "url": "https://www.rivistadomino.it/blog/2026/04/24/usa/",',
                '  "published_date": "2026-04-24",',
                '  "feed": "La settimana di Domino"',
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    audio_path = article_dir / "2026-04-24-usa-e-globalizzazione.mp3"
    audio_path.write_bytes(b"audio")

    result = generate_podcast_outputs(
        library_dir,
        podcast_dir,
        "https://podcasts.example.test/domino",
        rss=True,
        index=True,
        audio_format="mp3",
    )

    podcast_collection_dir = podcast_dir / "la-settimana-di-domino"
    podcast_audio_path = podcast_collection_dir / "2026-04-24-usa-e-globalizzazione.mp3"
    assert result == {"rss": 1, "index": podcast_dir / "index.html"}
    assert audio_path.exists()
    assert podcast_audio_path.read_bytes() == b"audio"
    assert (podcast_dir / "apple-touch-icon.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (podcast_collection_dir / "domino-official.png").read_bytes() == b"png"

    feed = (podcast_collection_dir / "feed.xml").read_text(encoding="utf-8")
    assert "<title>La settimana di Domino</title>" in feed
    assert "<title>USA e globalizzazione</title>" in feed
    assert 'type="audio/mpeg"' in feed
    assert 'length="5"' in feed
    assert (
        "https://podcasts.example.test/domino/la-settimana-di-domino/"
        "2026-04-24-usa-e-globalizzazione.mp3"
    ) in feed
    assert "2026-04-24-usa-e-globalizzazione/" not in feed

    index = (podcast_dir / "index.html").read_text(encoding="utf-8")
    assert "DominoPodcast" in index
    assert "La settimana di Domino" in index
    assert "la-settimana-di-domino/domino-official.png" in index
    assert "cover-placeholder" in index
    assert ">L</div>" not in index
    assert "https://podcasts.example.test/domino/la-settimana-di-domino/feed.xml" in index
    assert "pcast://podcasts.example.test/domino/la-settimana-di-domino/feed.xml" in index


def test_generate_podcast_outputs_uses_requested_audio_format(tmp_path: Path) -> None:
    library_dir = tmp_path / "library"
    podcast_dir = tmp_path / "podcasts"
    article_dir = library_dir / "la-settimana-di-domino" / "2026-04-24-usa-e-globalizzazione"
    article_dir.mkdir(parents=True)
    (article_dir / "metadata.json").write_text(
        '{"title": "USA", "published_date": "2026-04-24"}\n',
        encoding="utf-8",
    )
    (article_dir / "2026-04-24-usa-e-globalizzazione.m4a").write_bytes(b"audio-m4a")
    (article_dir / "2026-04-24-usa-e-globalizzazione.mp3").write_bytes(b"audio-mp3")

    generate_podcast_outputs(library_dir, podcast_dir, rss=True, audio_format="m4a")

    assert not (
        podcast_dir / "la-settimana-di-domino" / "2026-04-24-usa-e-globalizzazione.mp3"
    ).exists()
    feed = (podcast_dir / "la-settimana-di-domino" / "feed.xml").read_text(encoding="utf-8")
    assert "2026-04-24-usa-e-globalizzazione.m4a" in feed
    assert "2026-04-24-usa-e-globalizzazione.mp3" not in feed
    assert 'type="audio/mp4"' in feed


def test_generate_podcast_outputs_removes_stale_collection_when_format_has_no_audio(
    tmp_path: Path,
) -> None:
    library_dir = tmp_path / "library"
    podcast_dir = tmp_path / "podcasts"
    collection_dir = library_dir / "la-settimana-di-domino"
    article_dir = collection_dir / "2026-04-24-usa-e-globalizzazione"
    article_dir.mkdir(parents=True)
    write_feed_collection_details(
        collection_dir,
        FeedCollectionDetails(
            slug="la-settimana-di-domino",
            title="La settimana di Domino",
            author="Domino",
            description="La raccolta settimanale.",
            page_url="",
        ),
    )
    (article_dir / "metadata.json").write_text(
        '{"title": "USA", "published_date": "2026-04-24"}\n',
        encoding="utf-8",
    )
    (article_dir / "2026-04-24-usa-e-globalizzazione.m4a").write_bytes(b"audio-m4a")

    generate_podcast_outputs(library_dir, podcast_dir, rss=True, index=True, audio_format="m4a")
    stale_collection_dir = podcast_dir / "la-settimana-di-domino"
    assert (stale_collection_dir / "feed.xml").exists()
    assert (stale_collection_dir / "2026-04-24-usa-e-globalizzazione.m4a").exists()

    result = generate_podcast_outputs(
        library_dir,
        podcast_dir,
        rss=True,
        index=True,
        audio_format="mp3",
    )

    assert result == {"rss": 0, "index": podcast_dir / "index.html"}
    assert not stale_collection_dir.exists()
    assert "La settimana di Domino" not in (podcast_dir / "index.html").read_text(encoding="utf-8")


def test_generate_podcast_outputs_rejects_output_dir_inside_library(tmp_path: Path) -> None:
    library_dir = tmp_path / "library"
    podcast_dir = library_dir / "published-podcast"
    article_dir = library_dir / "la-settimana-di-domino" / "2026-04-24-usa-e-globalizzazione"
    article_dir.mkdir(parents=True)
    (article_dir / "metadata.json").write_text(
        '{"title": "USA", "published_date": "2026-04-24"}\n',
        encoding="utf-8",
    )
    (article_dir / "2026-04-24-usa-e-globalizzazione.m4a").write_bytes(b"audio-m4a")

    try:
        generate_podcast_outputs(library_dir, podcast_dir, rss=True, audio_format="mp3")
    except ValueError as error:
        assert "podcast_output_dir must not be inside library_dir" in str(error)
    else:
        raise AssertionError("expected overlapping output directory to be rejected")

    assert article_dir.exists()
    assert (article_dir / "metadata.json").exists()


def test_validate_podcast_output_dir_accepts_sibling_output_dir(tmp_path: Path) -> None:
    validate_podcast_output_dir(tmp_path / "library", tmp_path / "podcasts")


def test_generate_podcast_outputs_preserves_existing_feed_on_index_only_format_mismatch(
    tmp_path: Path,
) -> None:
    library_dir = tmp_path / "library"
    podcast_dir = tmp_path / "podcasts"
    collection_dir = library_dir / "la-settimana-di-domino"
    article_dir = collection_dir / "2026-04-24-usa-e-globalizzazione"
    article_dir.mkdir(parents=True)
    write_feed_collection_details(
        collection_dir,
        FeedCollectionDetails(
            slug="la-settimana-di-domino",
            title="La settimana di Domino",
            author="Domino",
            description="La raccolta settimanale.",
            page_url="",
        ),
    )
    (article_dir / "metadata.json").write_text(
        '{"title": "USA", "published_date": "2026-04-24"}\n',
        encoding="utf-8",
    )
    (article_dir / "2026-04-24-usa-e-globalizzazione.mp3").write_bytes(b"audio-mp3")
    generate_podcast_outputs(library_dir, podcast_dir, rss=True, index=True, audio_format="mp3")
    published_collection_dir = podcast_dir / "la-settimana-di-domino"
    feed_path = published_collection_dir / "feed.xml"
    audio_path = published_collection_dir / "2026-04-24-usa-e-globalizzazione.mp3"
    assert feed_path.exists()
    assert audio_path.exists()

    result = generate_podcast_outputs(library_dir, podcast_dir, index=True, audio_format="m4a")

    assert result == {"rss": 0, "index": podcast_dir / "index.html"}
    assert feed_path.exists()
    assert audio_path.exists()


def test_generate_podcast_outputs_ignores_m4b_files(tmp_path: Path) -> None:
    library_dir = tmp_path / "library"
    podcast_dir = tmp_path / "podcasts"
    article_dir = library_dir / "la-settimana-di-domino" / "2026-04-24-usa-e-globalizzazione"
    article_dir.mkdir(parents=True)
    (article_dir / "metadata.json").write_text(
        '{"title": "USA", "published_date": "2026-04-24"}\n',
        encoding="utf-8",
    )
    (article_dir / "2026-04-24-usa-e-globalizzazione.m4b").write_bytes(b"book")

    result = generate_podcast_outputs(
        library_dir, podcast_dir, rss=True, index=True, audio_format="mp3"
    )

    assert result == {"rss": 0, "index": podcast_dir / "index.html"}
    assert not (podcast_dir / "la-settimana-di-domino" / "feed.xml").exists()


def test_generate_podcast_outputs_downloads_collection_artwork(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    library_dir = tmp_path / "library"
    podcast_dir = tmp_path / "podcasts"
    article_dir = library_dir / "la-settimana-di-domino" / "2026-04-24-usa-e-globalizzazione"
    article_dir.mkdir(parents=True)
    write_feed_collection_details(
        article_dir.parent,
        FeedCollectionDetails(
            slug="la-settimana-di-domino",
            title="La settimana di Domino",
            author="Domino",
            description="La raccolta settimanale.",
            page_url="https://www.rivistadomino.it/blog/category/la-settimana-di-domino/",
            artwork_url="https://cdn.example.test/domino.png",
        ),
    )
    (article_dir / "metadata.json").write_text(
        '{"title": "USA", "published_date": "2026-04-24"}\n',
        encoding="utf-8",
    )
    (article_dir / "2026-04-24-usa-e-globalizzazione.mp3").write_bytes(b"audio")

    monkeypatch.setattr(
        podcast_outputs,
        "_download_artwork",
        lambda _url: b"official-domino-png",
    )

    generate_podcast_outputs(library_dir, podcast_dir, rss=True, index=True, audio_format="mp3")

    artwork_path = podcast_dir / "la-settimana-di-domino" / "cover.png"
    assert artwork_path.read_bytes() == b"official-domino-png"
    feed = (podcast_dir / "la-settimana-di-domino" / "feed.xml").read_text(encoding="utf-8")
    assert "https://cdn.example.test/domino.png" not in feed
    assert "https://podcasts.example.test" not in feed
