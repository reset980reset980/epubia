import os
import time
from pathlib import Path

import pytest
from PIL import Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from pdf_rendering import PDFRenderingError, pdf_page_count, render_pdf_page


@pytest.fixture
def korean_pdf(tmp_path: Path) -> Path:
    font_path = Path(__file__).parents[1] / "fonts" / "SeoulHangang.ttf"
    pdfmetrics.registerFont(TTFont("EpubiaTestKorean", str(font_path)))

    path = tmp_path / "한글 전자책.pdf"
    document = canvas.Canvas(str(path), pagesize=(600, 800))
    document.setFont("EpubiaTestKorean", 28)
    document.setFillColorRGB(0.05, 0.35, 0.75)
    document.drawString(72, 700, "첫 번째 한글 페이지")
    document.rect(72, 540, 220, 100, fill=1, stroke=0)
    document.showPage()

    document.setFont("EpubiaTestKorean", 28)
    document.setFillColorRGB(0.85, 0.25, 0.15)
    document.drawString(72, 700, "두 번째 한글 페이지")
    document.circle(180, 580, 80, fill=1, stroke=0)
    document.showPage()
    document.save()
    return path


def test_pdf_page_count_reads_korean_reportlab_pdf(korean_pdf: Path):
    assert pdf_page_count(korean_pdf) == 2


def test_render_pdf_pages_as_sized_rgb_pngs(korean_pdf: Path, tmp_path: Path):
    cache_dir = tmp_path / "page-cache"

    first_thumb = render_pdf_page(korean_pdf, cache_dir, 1, "thumb")
    second_thumb = render_pdf_page(korean_pdf, cache_dir, 2, "thumb")
    first_screen = render_pdf_page(korean_pdf, cache_dir, 1, "screen")

    assert first_thumb.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert second_thumb.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert first_thumb.read_bytes() != second_thumb.read_bytes()

    with Image.open(first_thumb) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert abs(image.width - 220) <= 1
    with Image.open(first_screen) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert abs(image.width - 1600) <= 1


def test_render_pdf_page_reuses_fresh_cache(korean_pdf: Path, tmp_path: Path):
    cache_dir = tmp_path / "page-cache"
    cached = render_pdf_page(korean_pdf, cache_dir, 1, "thumb")
    cached_bytes = cached.read_bytes()
    fresh_ns = max(time.time_ns(), korean_pdf.stat().st_mtime_ns) + 2_000_000_000
    os.utime(cached, ns=(fresh_ns, fresh_ns))

    reused = render_pdf_page(korean_pdf, cache_dir, 1, "thumb")

    assert reused == cached
    assert reused.stat().st_mtime_ns == fresh_ns
    assert reused.read_bytes() == cached_bytes


def test_extremely_tall_page_is_bounded_to_safe_dimensions(tmp_path: Path):
    pdf_path = tmp_path / "아주 긴 페이지.pdf"
    pymupdf = __import__("pymupdf")
    document = pymupdf.open()
    document.new_page(width=10, height=100_000)
    document.save(pdf_path)
    document.close()

    rendered = render_pdf_page(pdf_path, tmp_path / "cache", 1, "screen")

    with Image.open(rendered) as image:
        assert max(image.size) <= 3_200
        assert image.width * image.height <= 6_000_000


@pytest.mark.parametrize("page_number", [0, 3])
def test_render_pdf_page_rejects_out_of_range_pages(
    korean_pdf: Path, tmp_path: Path, page_number: int
):
    with pytest.raises(ValueError, match="페이지 번호"):
        render_pdf_page(korean_pdf, tmp_path / "cache", page_number)


def test_render_pdf_page_rejects_unknown_variant(korean_pdf: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="variant"):
        render_pdf_page(korean_pdf, tmp_path / "cache", 1, "poster")


def test_corrupt_pdf_has_clear_korean_error(tmp_path: Path):
    corrupt = tmp_path / "손상.pdf"
    corrupt.write_bytes(b"%PDF-1.7\nnot a complete pdf")

    with pytest.raises(PDFRenderingError, match="손상|읽을 수"):
        pdf_page_count(corrupt)


def test_encrypted_pdf_has_clear_korean_error(tmp_path: Path):
    encrypted = tmp_path / "암호.pdf"
    document = canvas.Canvas(str(encrypted), encrypt="secret")
    document.drawString(72, 700, "protected")
    document.save()

    with pytest.raises(PDFRenderingError, match="암호"):
        pdf_page_count(encrypted)
