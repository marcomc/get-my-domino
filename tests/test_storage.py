from __future__ import annotations

from pathlib import Path

from get_my_domino.models import Article
from get_my_domino.storage import article_text_path, write_article_metadata


def test_write_article_metadata_normalizes_legacy_article_artifacts(tmp_path: Path) -> None:
    target_dir = tmp_path / "01-e-la-casa-bianca-rest-sola"
    target_dir.mkdir()
    (target_dir / "01-2026-04-21-e-la-casa-bianca-rest-sola.html").write_text(
        "<html></html>",
        encoding="utf-8",
    )
    (target_dir / "01-2026-04-21-e-la-casa-bianca-rest-sola.txt").write_text(
        "text",
        encoding="utf-8",
    )
    (target_dir / "01-e-la-casa-bianca-rest-sola.m4a").write_bytes(b"canonical-audio")
    (target_dir / "article.m4a").write_bytes(b"canonical-audio")

    write_article_metadata(
        target_dir,
        Article(
            title="E la Casa Bianca restò sola",
            url="https://example.test/article",
            text="Corpo.",
            html="<html></html>",
            author="Lorenzo Maria Ricci",
        ),
    )

    assert (target_dir / "01-e-la-casa-bianca-rest-sola.html").exists()
    assert (target_dir / "01-e-la-casa-bianca-rest-sola.txt").exists()
    assert (target_dir / "01-e-la-casa-bianca-rest-sola.m4a").exists()
    assert (target_dir / "metadata.json").exists()
    assert not (target_dir / "01-2026-04-21-e-la-casa-bianca-rest-sola.html").exists()
    assert not (target_dir / "01-2026-04-21-e-la-casa-bianca-rest-sola.txt").exists()
    assert not (target_dir / "article.m4a").exists()


def test_article_text_path_normalizes_legacy_text_basename(tmp_path: Path) -> None:
    target_dir = tmp_path / "11-perche-la-cina-vince"
    target_dir.mkdir()
    legacy_text = target_dir / "11-perch-la-cina-vince.txt"
    legacy_text.write_text("text", encoding="utf-8")
    (target_dir / "11-perch-la-cina-vince.speech.txt").write_text("speech", encoding="utf-8")
    (target_dir / "11-perch-la-cina-vince.speech.last-message.txt").write_text(
        "last-message", encoding="utf-8"
    )

    resolved = article_text_path(target_dir)

    assert resolved == target_dir / "11-perche-la-cina-vince.txt"
    assert resolved.exists()
    assert resolved.read_text(encoding="utf-8") == "text"
    assert not legacy_text.exists()
    assert (target_dir / "11-perch-la-cina-vince.speech.txt").exists()
    assert (target_dir / "11-perch-la-cina-vince.speech.last-message.txt").exists()
