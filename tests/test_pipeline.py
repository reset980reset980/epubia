from pathlib import Path
import zipfile

from ebook_pipeline import BookMeta, build_book, split_chapters


def test_split_korean_chapters():
    text = "제 1 장\n첫 번째 본문입니다.\n\n제 2 장\n두 번째 본문입니다."
    chapters = split_chapters(text)
    assert len(chapters) == 2
    assert chapters[0].title.startswith("제 1 장")


def test_build_book_from_text(tmp_path: Path):
    source = tmp_path / "sample.txt"
    source.write_text("제 1 장\n안녕하세요. 전자책 테스트입니다.\n\n제 2 장\n두 번째 장입니다.", encoding="utf-8")
    result = build_book(source, BookMeta(title="테스트 전자책", author="기혜경"), tmp_path / "books")
    assert result.epub_path.exists()
    assert result.pdf_path.exists()
    assert result.markdown_path.exists()
    with zipfile.ZipFile(result.epub_path) as zf:
        assert zf.read("mimetype") == b"application/epub+zip"
        assert "OEBPS/content.opf" in zf.namelist()

