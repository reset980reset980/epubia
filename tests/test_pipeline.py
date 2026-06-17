from pathlib import Path
import zipfile

from ebook_pipeline import BookMeta, build_book, clean_extracted_pdf_text, infer_title, split_chapters


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


def test_clean_broken_pdf_text_and_skip_damaged_title():
    raw = """Ⅳ������������추진◀
ⅣⅣ..  개개인인정정보보보보호호  업업무무  추추진진
담담당당자자::  정정보보보보호호팀팀  이이은은경경,,  오오유유미미((44220077--662288,,  663322))
11  최근 개인정보 보호법 등 주요 개정사항
Ⅳ������������추진◀
ⅣⅣ..  개개인인정정보보보보호호  업업무무  추추진진
"""
    cleaned = clean_extracted_pdf_text(raw)

    assert "Ⅳ추진◀" not in cleaned
    assert "Ⅳ. 개인정보보호 업무 추진" in cleaned
    assert "담당자: 정보보호팀 이은경, 오유미((4207-628, 632))" in cleaned
    assert cleaned.count("개인정보보호 업무 추진") == 1
    assert infer_title(cleaned, "개인정보보호 교육자료") == "개인정보보호 교육자료"
