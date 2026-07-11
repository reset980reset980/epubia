"""PDF 페이지를 웹 미리보기용 PNG로 렌더링한다."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from pathlib import Path
from typing import Final

import pymupdf


class PDFRenderingError(RuntimeError):
    """사용자에게 안내할 수 있는 PDF 열기/렌더링 오류."""


_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_RENDER_CACHE_VERSION: Final = "v2"
_VARIANT_WIDTHS: Final = {"thumb": 220, "screen": 1600}
_VARIANT_MAX_DIMENSIONS: Final = {"thumb": 640, "screen": 3200}
_VARIANT_MAX_PIXELS: Final = {"thumb": 250_000, "screen": 6_000_000}


def _pdf_path(path: str | os.PathLike[str]) -> Path:
    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise PDFRenderingError(f"PDF 파일을 찾을 수 없습니다: {pdf_path}")
    return pdf_path


def _open_pdf(path: Path) -> pymupdf.Document:
    try:
        document = pymupdf.open(str(path))
    except Exception as exc:
        raise PDFRenderingError(
            f"PDF 파일이 손상되었거나 읽을 수 없습니다: {path.name}"
        ) from exc

    try:
        if not document.is_pdf:
            raise PDFRenderingError(f"올바른 PDF 파일이 아닙니다: {path.name}")
        if document.needs_pass or document.is_encrypted:
            raise PDFRenderingError(
                "암호화된 PDF는 렌더링할 수 없습니다. "
                "암호를 해제한 뒤 다시 시도해 주세요."
            )
    except Exception:
        document.close()
        raise
    return document


def pdf_page_count(path: str | os.PathLike[str]) -> int:
    """PDF의 전체 페이지 수를 반환한다."""

    pdf_path = _pdf_path(path)
    with _open_pdf(pdf_path) as document:
        return document.page_count


def _cache_path(
    pdf_path: Path,
    cache_dir: Path,
    page_number: int,
    variant: str,
    source_stat: os.stat_result,
) -> Path:
    source_id = hashlib.sha256(
        str(pdf_path.resolve()).encode("utf-8")
    ).hexdigest()[:16]
    return cache_dir / (
        f"{source_id}-{source_stat.st_size}-{source_stat.st_mtime_ns}"
        f"-{_RENDER_CACHE_VERSION}-w{_VARIANT_WIDTHS[variant]}"
        f"-d{_VARIANT_MAX_DIMENSIONS[variant]}-p{_VARIANT_MAX_PIXELS[variant]}"
        f"-page-{page_number:06d}-{variant}.png"
    )


def _cache_is_fresh(cache_path: Path, source_stat: os.stat_result) -> bool:
    try:
        cache_stat = cache_path.stat()
        if cache_stat.st_mtime_ns < source_stat.st_mtime_ns:
            return False
        if cache_stat.st_size <= len(_PNG_SIGNATURE):
            return False
        with cache_path.open("rb") as cached:
            return cached.read(len(_PNG_SIGNATURE)) == _PNG_SIGNATURE
    except OSError:
        return False


def _write_atomically(destination: Path, content: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.chmod(0o644)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _safe_render_zoom(page_width: float, page_height: float, variant: str) -> float:
    """비정상적인 종횡비도 제한된 픽셀 수 안에서 렌더링할 배율을 구한다."""

    if (
        not math.isfinite(page_width)
        or not math.isfinite(page_height)
        or page_width <= 0
        or page_height <= 0
    ):
        raise ValueError("페이지 크기가 올바르지 않습니다")

    target_width = _VARIANT_WIDTHS[variant]
    max_dimension = _VARIANT_MAX_DIMENSIONS[variant]
    max_pixels = _VARIANT_MAX_PIXELS[variant]
    zoom = min(
        target_width / page_width,
        max_dimension / max(page_width, page_height),
        math.sqrt(max_pixels / (page_width * page_height)),
    )
    if not math.isfinite(zoom) or zoom <= 0:
        raise ValueError("안전한 PDF 렌더링 배율을 계산할 수 없습니다")
    return zoom


def render_pdf_page(
    pdf_path: str | os.PathLike[str],
    cache_dir: str | os.PathLike[str],
    page_number: int,
    variant: str = "thumb",
) -> Path:
    """1부터 시작하는 PDF 페이지를 RGB PNG로 렌더링하고 캐시 경로를 반환한다."""

    if variant not in _VARIANT_WIDTHS:
        allowed = ", ".join(sorted(_VARIANT_WIDTHS))
        raise ValueError(f"variant는 {allowed} 중 하나여야 합니다: {variant!r}")
    if isinstance(page_number, bool) or not isinstance(page_number, int):
        raise ValueError("페이지 번호는 1부터 시작하는 정수여야 합니다.")

    source = _pdf_path(pdf_path)
    source_stat = source.stat()
    destination_dir = Path(cache_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    with _open_pdf(source) as document:
        if page_number < 1 or page_number > document.page_count:
            if document.page_count == 0:
                raise ValueError("이 PDF에는 렌더링할 페이지가 없습니다.")
            raise ValueError(
                f"페이지 번호는 1부터 {document.page_count}까지여야 합니다: "
                f"{page_number}"
            )

        destination = _cache_path(
            source, destination_dir, page_number, variant, source_stat
        )
        if _cache_is_fresh(destination, source_stat):
            return destination

        try:
            page = document.load_page(page_number - 1)
            page_width = float(page.rect.width)
            page_height = float(page.rect.height)
            zoom = _safe_render_zoom(page_width, page_height, variant)
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(zoom, zoom),
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
            png = pixmap.tobytes("png")
        except Exception as exc:
            raise PDFRenderingError(
                f"PDF {page_number}페이지 이미지를 만들 수 없습니다. "
                "파일이 손상되었는지 확인해 주세요."
            ) from exc

    if not png.startswith(_PNG_SIGNATURE):
        raise PDFRenderingError(
            f"PDF {page_number}페이지의 PNG 이미지를 만들지 못했습니다."
        )
    _write_atomically(destination, png)
    return destination


__all__ = ["PDFRenderingError", "pdf_page_count", "render_pdf_page"]
