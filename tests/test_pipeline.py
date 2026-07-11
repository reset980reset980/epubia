from pathlib import Path
import zipfile

from PIL import Image
from pypdf import PdfReader
import pytest
from reportlab.pdfgen import canvas

from ebook_pipeline import (
    HTML_TEXT_UNAVAILABLE_MESSAGE,
    PDF_TEXT_UNAVAILABLE_MESSAGE,
    BookMeta,
    build_book,
    clean_extracted_pdf_text,
    extract_text,
    infer_title,
    select_cover_background,
    split_chapters,
    validate_source,
)


def write_html_book_zip(path: Path, files: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            payload = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(name, payload)
    return path


def test_split_korean_chapters():
    text = "제 1 장\n첫 번째 본문입니다.\n\n제 2 장\n두 번째 본문입니다."
    chapters = split_chapters(text)
    assert len(chapters) == 2
    assert chapters[0].title.startswith("제 1 장")


def test_build_book_from_text(tmp_path: Path, monkeypatch):
    source = tmp_path / "sample.txt"
    source.write_text("제 1 장\n안녕하세요. 전자책 테스트입니다.\n\n제 2 장\n두 번째 장입니다.", encoding="utf-8")
    monkeypatch.setattr("ebook_pipeline.COVER_BACKGROUND_DIR", tmp_path / "covers-not-created-yet")
    result = build_book(source, BookMeta(title="테스트 전자책", author="기혜경"), tmp_path / "books")
    assert result.epub_path.exists()
    assert result.pdf_path.exists()
    assert result.markdown_path.exists()
    assert result.cover_path.exists()
    assert result.publication_type == "text"
    assert result.html_path is None
    assert result.html_archive_path is None
    with Image.open(result.cover_path) as cover:
        assert cover.size == (1200, 1600)
        assert cover.format == "PNG"
        assert cover.info["Title"] == "테스트 전자책"
        assert cover.info["Author"] == "기혜경"
        assert cover.info["Publisher"] == "혜경 전자책 스튜디오"
        preview_colors = cover.resize((60, 80)).getcolors(maxcolors=60 * 80)
        assert len(preview_colors or []) > 10
    with zipfile.ZipFile(result.epub_path) as zf:
        assert zf.read("mimetype") == b"application/epub+zip"
        assert "OEBPS/content.opf" in zf.namelist()
        assert "OEBPS/cover.xhtml" in zf.namelist()
        assert "OEBPS/images/cover.png" in zf.namelist()
        assert zf.read("OEBPS/images/cover.png") == result.cover_path.read_bytes()
        package = zf.read("OEBPS/content.opf").decode("utf-8")
        assert "<dc:title>테스트 전자책</dc:title>" in package
        assert "<dc:creator>기혜경</dc:creator>" in package
        assert 'properties="cover-image"' in package
        assert 'href="images/cover.png"' in package

    generated_pdf = PdfReader(str(result.pdf_path))
    assert len(generated_pdf.pages) >= 4
    assert "/XObject" in generated_pdf.pages[0]["/Resources"]
    extracted_pdf_text = "\n".join(page.extract_text() or "" for page in generated_pdf.pages[1:])
    assert "테스트 전자책" in extracted_pdf_text
    assert "안녕하세요" in extracted_pdf_text


def test_cover_background_selection_is_stable(tmp_path: Path):
    cover_dir = tmp_path / "covers"
    cover_dir.mkdir()
    candidates = []
    for index, color in enumerate(((10, 20, 30), (40, 50, 60), (70, 80, 90)), start=1):
        path = cover_dir / f"cover-bg-{index:02d}.png"
        Image.new("RGB", (80, 120), color).save(path)
        candidates.append(path.resolve())

    first = select_cover_background("한글 제목 해시 선택", cover_dir)
    second = select_cover_background("한글 제목 해시 선택", cover_dir)
    assert first == second
    assert first in candidates


def test_default_source_limit_accepts_over_30mb_and_rejects_over_100mb(tmp_path: Path):
    accepted = tmp_path / "31메가 원고.txt"
    rejected = tmp_path / "101메가 원고.txt"
    with accepted.open("wb") as handle:
        handle.seek(31 * 1024 * 1024 - 1)
        handle.write(b"x")
    with rejected.open("wb") as handle:
        handle.seek(101 * 1024 * 1024 - 1)
        handle.write(b"x")

    validate_source(accepted)
    with pytest.raises(ValueError, match="100MB"):
        validate_source(rejected)


def test_scanned_pdf_publishes_original_bytes_with_korean_notice(tmp_path: Path):
    source = tmp_path / "스캔 원본.pdf"
    pdf = canvas.Canvas(str(source))
    pdf.showPage()
    pdf.save()
    original_bytes = source.read_bytes()

    result = build_book(source, BookMeta(title="스캔 자료집", author="한글 저자"), tmp_path / "books")

    assert result.pdf_path.read_bytes() == original_bytes
    assert result.publication_type == "pdf"
    assert result.source_text_path.read_text(encoding="utf-8") == PDF_TEXT_UNAVAILABLE_MESSAGE
    assert PDF_TEXT_UNAVAILABLE_MESSAGE in result.markdown_path.read_text(encoding="utf-8")
    with zipfile.ZipFile(result.epub_path) as zf:
        chapter = zf.read("OEBPS/chapter_001.xhtml").decode("utf-8")
        package = zf.read("OEBPS/content.opf").decode("utf-8")
        assert PDF_TEXT_UNAVAILABLE_MESSAGE in chapter
        assert "<dc:title>스캔 자료집</dc:title>" in package
        assert "<dc:creator>한글 저자</dc:creator>" in package


def test_build_book_from_html_zip_preserves_archive_without_rebuilding_pdf(
    tmp_path: Path,
    monkeypatch,
):
    source = write_html_book_zip(
        tmp_path / "관상록_웹전환본.zip",
        {
            "관상록/index.html": """<!doctype html><html lang="ko"><head>
                <meta charset="utf-8"><link rel="stylesheet" href="assets/book.css">
                <script src="assets/reader.js" defer></script></head><body>
                <h1>기혜경의 관상 톡</h1>
                <p>하루의 운기를 보는 관상부위 인당</p></body></html>""",
            "관상록/text/page-002.html": (
                "<article><h2>인당은 하루의 일진을 보여주는 거울</h2>"
                "<p>오늘의 기색과 마음을 살펴봅니다.</p></article>"
            ),
            "관상록/assets/book.css": "body { font-family: sans-serif; }",
            "관상록/assets/reader.js": "document.documentElement.dataset.ready = 'true';",
            "관상록/ocr_pages.json": '{"pages": 2}',
        },
    )
    original_archive = source.read_bytes()
    prepared_cover = tmp_path / "prepared-cover.png"
    Image.new("RGB", (1200, 1600), (12, 42, 68)).save(prepared_cover)

    def fail_if_derivative_generation_starts(*_args, **_kwargs):
        raise AssertionError("완성형 HTML 출판은 EPUB/PDF 재조판을 시작하면 안 됩니다.")

    monkeypatch.setattr("ebook_pipeline.create_epub", fail_if_derivative_generation_starts)
    monkeypatch.setattr("ebook_pipeline.create_pdf", fail_if_derivative_generation_starts)

    result = build_book(
        source,
        BookMeta(title="기혜경의 관상 톡", author="기혜경"),
        tmp_path / "books",
        prepared_cover_path=prepared_cover,
        prepared_cover_mode="template",
    )

    assert result.publication_type == "html"
    assert result.html_path == (result.source_text_path.parent / "html/index.html").resolve()
    assert result.html_path.is_file()
    assert "하루의 운기" in result.html_path.read_text(encoding="utf-8")
    assert (result.html_path.parent / "text/page-002.html").is_file()
    assert result.html_archive_path is not None
    assert result.html_archive_path.read_bytes() == original_archive
    assert result.html_archive_path.suffix == ".zip"

    extracted_source = result.source_text_path.read_text(encoding="utf-8")
    assert "기혜경의 관상 톡" in extracted_source
    assert "인당은 하루의 일진을 보여주는 거울" in extracted_source
    assert result.markdown_path.is_file()
    assert "오늘의 기색과 마음" in result.markdown_path.read_text(encoding="utf-8")
    assert result.epub_path is None
    assert result.pdf_path is None


def test_html_zip_publication_skips_slow_synchronous_pdf_derivative(
    tmp_path: Path,
    monkeypatch,
):
    large_ocr_text = "\n".join(
        f"{index:04d} 하루의 운기를 보는 관상부위 {index * 7919}"
        for index in range(2_000)
    )
    source = write_html_book_zip(
        tmp_path / "large-html-book.zip",
        {
            "index.html": "<main><h1>기혜경의 관상 톡</h1><p>완성된 HTML 책</p></main>",
            "관상톡_OCR_전체텍스트.txt": large_ocr_text,
        },
    )
    prepared_cover = tmp_path / "cover.png"
    Image.new("RGB", (1200, 1600), (24, 48, 72)).save(prepared_cover)

    def fail_if_derivative_generation_starts(*_args, **_kwargs):
        raise AssertionError("HTML 출판은 동기 EPUB/PDF 조판을 시작하면 안 됩니다.")

    monkeypatch.setattr("ebook_pipeline.create_epub", fail_if_derivative_generation_starts)
    monkeypatch.setattr("ebook_pipeline.create_pdf", fail_if_derivative_generation_starts)

    result = build_book(
        source,
        BookMeta(title="기혜경의 관상 톡", author="기혜경"),
        tmp_path / "books",
        prepared_cover_path=prepared_cover,
        prepared_cover_mode="template",
    )

    assert result.publication_type == "html"
    assert result.epub_path is None
    assert result.pdf_path is None
    assert result.html_path is not None and result.html_path.is_file()
    assert result.html_archive_path is not None and result.html_archive_path.is_file()


def test_extract_text_from_html_zip_uses_visible_body_only(tmp_path: Path):
    source = write_html_book_zip(
        tmp_path / "html-book.zip",
        {
            "index.html": (
                "<html><head><title>숨겨진 제목</title>"
                "<script>숨겨진 스크립트</script></head>"
                "<body><h1>보이는 한글 본문</h1></body></html>"
            )
        },
    )

    extracted = extract_text(source)

    assert extracted == "보이는 한글 본문"
    assert "숨겨진" not in extracted


def test_html_zip_with_no_visible_body_gets_korean_reader_notice(tmp_path: Path):
    source = write_html_book_zip(
        tmp_path / "script-only.zip",
        {"index.html": "<html><head><script>renderBook()</script></head><body></body></html>"},
    )

    assert extract_text(source) == HTML_TEXT_UNAVAILABLE_MESSAGE


def test_html_extracted_text_override_never_bypasses_archive_validation(tmp_path: Path):
    source = write_html_book_zip(
        tmp_path / "unsafe.zip",
        {
            "index.html": "<h1>표지</h1>",
            "../outside.js": "alert(1)",
        },
    )
    output_root = tmp_path / "books"

    with pytest.raises(ValueError, match="경로"):
        build_book(
            source,
            BookMeta(title="검증 전자책", author="기혜경"),
            output_root,
            extracted_text_override="미리 추출한 안전한 문장",
        )

    assert not (tmp_path / "outside.js").exists()
    assert not output_root.exists() or not any(output_root.iterdir())


def test_html_preview_is_bounded_but_publication_uses_full_ocr_companion_text(
    tmp_path: Path,
    monkeypatch,
):
    full_ocr_text = "관상 원문 시작\n" + "\n".join(
        f"{page:04d} 오늘의 운기와 인당을 살펴는 관상 원문 {page * 7919}."
        for page in range(1, 1_201)
    )
    assert len(full_ocr_text) > 20_000
    source = write_html_book_zip(
        tmp_path / "large-ocr-html.zip",
        {
            "index.html": "<main><h1>기혜경의 관상 톡</h1><p>웹 뷰어</p></main>",
            "관상톡_OCR_전체텍스트.txt": full_ocr_text,
        },
    )
    prepared_cover = tmp_path / "cover.png"
    Image.new("RGB", (1200, 1600), (24, 48, 72)).save(prepared_cover)

    preview = extract_text(source)
    assert len(preview) == 20_000
    assert preview.startswith("관상 원문 시작")

    def fake_create_epub(_meta, _chapters, output_path, _cover_path):
        output_path.write_bytes(b"epub-placeholder")

    def fake_create_pdf(_meta, _chapters, output_path, _cover_path):
        output_path.write_bytes(b"pdf-placeholder")

    monkeypatch.setattr("ebook_pipeline.create_epub", fake_create_epub)
    monkeypatch.setattr("ebook_pipeline.create_pdf", fake_create_pdf)

    result = build_book(
        source,
        BookMeta(title="기혜경의 관상 톡", author="기혜경"),
        tmp_path / "books",
        prepared_cover_path=prepared_cover,
        prepared_cover_mode="template",
        extracted_text_override=preview,
    )
    published_text = result.source_text_path.read_text(encoding="utf-8")

    assert published_text == full_ocr_text.strip()
    assert len(published_text) > len(preview)
    assert result.markdown_path.stat().st_size > len(preview.encode("utf-8"))


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
