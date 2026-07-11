from __future__ import annotations

import html
import hashlib
import os
import random
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFilter, ImageFont, ImageOps, PngImagePlugin
from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image as ReportLabImage
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from html_book import ExtractedHTMLBook, HTMLBookLimits, extract_html_book


SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown", ".zip"}
PDF_TEXT_CORRECTIONS = (
    ("개인정보호", "개인정보보호"),
    ("정보호팀", "정보보호팀"),
    ("정보호 업무", "정보보호 업무"),
    ("정보호시스템", "정보보호시스템"),
    ("정보안", "정보보안"),
)
PDF_TEXT_UNAVAILABLE_MESSAGE = "이 PDF에서는 텍스트를 추출할 수 없습니다. 원본 PDF로 열람해 주세요."
HTML_TEXT_UNAVAILABLE_MESSAGE = (
    "이 HTML 전자책에서는 본문 텍스트를 추출할 수 없습니다. "
    "HTML 원본으로 열람해 주세요."
)
HTML_BOOK_PREVIEW_TEXT_CHARS = 20_000
HTML_BOOK_PUBLICATION_TEXT_CHARS = 2_000_000
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/opentype/unifont/unifont_jp.otf",
    str(PROJECT_ROOT / "fonts" / "SeoulHangang.ttf"),
]
COVER_SIZE = (1200, 1600)
DEFAULT_MAX_SOURCE_BYTES = 100 * 1024 * 1024
COVER_BACKGROUND_DIR = PROJECT_ROOT / "static" / "images" / "covers"
COVER_REGULAR_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/unifont/unifont_jp.otf",
    str(PROJECT_ROOT / "fonts" / "SeoulHangang.ttf"),
]
COVER_BOLD_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    *COVER_REGULAR_FONT_CANDIDATES,
]


@dataclass
class BookMeta:
    title: str
    author: str
    subtitle: str = ""
    publisher: str = "혜경 전자책 스튜디오"
    description: str = ""
    language: str = "ko"


@dataclass
class Chapter:
    title: str
    body: str


@dataclass
class BuildResult:
    book_id: str
    title: str
    author: str
    chapter_count: int
    source_text_path: Path
    markdown_path: Path
    cover_path: Path
    epub_path: Path | None
    pdf_path: Path | None
    created_at: str
    cover_mode: str = "template"
    publication_type: str = "text"
    html_path: Path | None = None
    html_archive_path: Path | None = None


def safe_filename(value: str, fallback: str = "book") -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._ -]+", "_", value or "").strip(" ._")
    cleaned = re.sub(r"\s+", "_", cleaned)
    return (cleaned[:80] or fallback).strip("_") or fallback


def display_title_from_filename(path: Path) -> str:
    return safe_filename(path.stem).replace("_", " ")


def validate_source(path: Path, max_source_bytes: int | None = None) -> None:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"지원하지 않는 파일 형식입니다. 허용 형식: {allowed}")
    limit = max_source_bytes if max_source_bytes is not None else DEFAULT_MAX_SOURCE_BYTES
    if path.stat().st_size > limit:
        limit_mb = max(1, limit // 1024 // 1024)
        raise ValueError(f"파일 크기는 {limit_mb}MB 이하여야 합니다.")


def extract_text(source_path: Path, max_source_bytes: int | None = None) -> str:
    validate_source(source_path, max_source_bytes)
    suffix = source_path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(source_path)
    if suffix == ".zip":
        with tempfile.TemporaryDirectory(prefix="epubia-html-text-") as temporary_directory:
            extracted = extract_html_book(
                source_path,
                Path(temporary_directory) / "html",
            )
            return (
                html_book_publication_text(
                    extracted,
                    max_chars=HTML_BOOK_PREVIEW_TEXT_CHARS,
                )
                or HTML_TEXT_UNAVAILABLE_MESSAGE
            )
    return read_text_file(source_path)


def _read_text_prefix(path: Path, max_chars: int) -> str:
    """Decode enough of a safe extracted text asset without loading it unbounded."""

    max_bytes = max(4, max_chars * 4 + 4)
    with path.open("rb") as handle:
        raw = handle.read(max_bytes)
    truncated = path.stat().st_size > len(raw)
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        trim_limit = 4 if truncated and encoding.startswith("utf-8") else 2 if truncated else 1
        for trim_count in range(trim_limit):
            payload = raw[:-trim_count] if trim_count else raw
            try:
                return normalize_text(payload.decode(encoding))[:max_chars]
            except UnicodeDecodeError:
                continue
    return normalize_text(raw.decode("utf-8", errors="replace"))[:max_chars]


def html_book_publication_text(
    extracted: ExtractedHTMLBook,
    *,
    max_chars: int,
) -> str:
    """Choose the fullest early text available in a validated HTML package.

    OCR packages commonly keep the rendered shell in HTML and the actual page
    text in ``text/*.txt`` or a top-level full-text Markdown/TXT export. A
    clearly named full-text export takes priority; otherwise page text files are
    concatenated in filename order and compared with visible HTML body text.
    """

    if max_chars <= 0:
        return ""
    html_text = normalize_text(extracted.visible_text)[:max_chars]
    text_paths = sorted(
        (
            path
            for path in extracted.root.rglob("*")
            if path.is_file() and path.suffix.casefold() in {".txt", ".md"}
        ),
        key=lambda path: path.relative_to(extracted.root).as_posix().casefold(),
    )
    preferred_text = ""
    aggregate_parts: list[str] = []
    aggregate_chars = 0
    preferred_tokens = ("전체텍스트", "전체", "fulltext", "full_text", "complete", "whole")

    for path in text_paths:
        relative_name = path.relative_to(extracted.root).as_posix().casefold()
        if path.name.casefold().startswith("readme"):
            continue
        text = _read_text_prefix(path, max_chars)
        if not text:
            continue
        if any(token in relative_name for token in preferred_tokens) and len(text) > len(preferred_text):
            preferred_text = text
        if aggregate_chars < max_chars:
            separator = 1 if aggregate_parts else 0
            remaining = max_chars - aggregate_chars - separator
            if remaining > 0:
                aggregate_parts.append(text[:remaining])
                aggregate_chars += separator + min(len(text), remaining)

    companion_text = preferred_text or "\n".join(aggregate_parts)
    return max((html_text, companion_text), key=len, default="")[:max_chars]


def extract_pdf_text(source_path: Path) -> str:
    chunks: list[str] = []
    try:
        reader = PdfReader(str(source_path))
        for page_index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                chunks.append(f"\n\n[페이지 {page_index}]\n{text}")
    except (PdfReadError, PdfStreamError) as exc:
        raise ValueError("PDF 파일 구조가 손상되었거나 업로드가 완전히 끝나지 않은 파일입니다. 원본 PDF를 다시 저장한 뒤 업로드해주세요.") from exc
    extracted = clean_extracted_pdf_text(normalize_text("\n".join(chunks)))
    if not extracted:
        return PDF_TEXT_UNAVAILABLE_MESSAGE
    return extracted


def read_text_file(source_path: Path) -> str:
    raw = source_path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return normalize_text(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    return normalize_text(raw.decode("utf-8", errors="replace"))


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def clean_extracted_pdf_text(text: str) -> str:
    cleaned_lines: list[str] = []
    previous = ""
    seen_lines: set[str] = set()
    for raw_line in text.splitlines():
        line = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw_line).strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1]:
                cleaned_lines.append("")
            continue
        if re.fullmatch(r"\[페이지\s+\d+\]", line):
            continue
        if is_pdf_navigation_noise(line):
            continue
        if line.count("�") >= max(2, len(line) // 5):
            line = line.replace("�", "")
        line = re.sub(r"�+", "", line)
        noisy_glyphs = has_repeated_pdf_glyph_noise(line)
        if noisy_glyphs:
            line = collapse_repeated_pdf_glyphs(line)
            line = apply_pdf_text_corrections(line)
        line = re.sub(r"\s{2,}", " ", line).strip()
        comparable = re.sub(r"\s+", " ", line)
        if not line or line == previous or (len(comparable) > 8 and comparable in seen_lines):
            continue
        cleaned_lines.append(line)
        previous = line
        if len(comparable) > 8:
            seen_lines.add(comparable)
    return normalize_text("\n".join(cleaned_lines))


def is_pdf_navigation_noise(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    return ("◀" in compact or "▶" in compact) and len(compact) <= 24


def has_repeated_pdf_glyph_noise(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    if len(compact) < 8:
        return False
    duplicate_count = sum(
        1
        for left, right in zip(compact, compact[1:])
        if left == right and re.match(r"[가-힣0-9:.,ㆍ·\-]", left)
    )
    return duplicate_count / len(compact) >= 0.18


def collapse_repeated_pdf_glyphs(line: str) -> str:
    line = re.sub(r"([가-힣0-9:.,ㆍ·\-])\1+", r"\1", line)
    line = re.sub(r"([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])\1+", r"\1", line)
    return line


def apply_pdf_text_corrections(line: str) -> str:
    for broken, fixed in PDF_TEXT_CORRECTIONS:
        line = line.replace(broken, fixed)
    line = re.sub(r"(?<!D)DoS", "DDoS", line)
    return line


def looks_like_broken_title(value: str) -> bool:
    if not value:
        return True
    if "�" in value:
        return True
    if "◀" in value or "▶" in value:
        return True
    if re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]{1,4}\s*[.]", value):
        return True
    if re.match(r"^\d{1,3}\s+", value) or re.match(r"^[가-힣]\.\s+", value):
        return True
    if value.startswith(("○", "w ", "※")):
        return True
    if value.startswith(("담당자:", "담당자：")):
        return True
    control_count = sum(1 for char in value if ord(char) < 32)
    return control_count > 0 or len(re.sub(r"[^가-힣A-Za-z0-9]", "", value)) < 2


def infer_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        candidate = line.strip(" #\t")
        if 2 <= len(candidate) <= 50 and not candidate.startswith("[페이지") and not looks_like_broken_title(candidate):
            return candidate
    return fallback


def split_chapters(text: str) -> list[Chapter]:
    markdown_chapters = split_markdown_chapters(text)
    if markdown_chapters:
        return markdown_chapters

    heading_chapters = split_heading_chapters(text)
    if heading_chapters:
        return heading_chapters

    return chunk_chapters(text)


def split_markdown_chapters(text: str) -> list[Chapter]:
    matches = list(re.finditer(r"(?m)^#{1,2}\s+(.+?)\s*$", text))
    if not matches:
        return []
    chapters: list[Chapter] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            chapters.append(Chapter(match.group(1).strip(), body))
    return chapters


def split_heading_chapters(text: str) -> list[Chapter]:
    roman_pattern = re.compile(r"(?m)^([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]{1,4}\.\s+.{2,40})\s*$")
    roman_matches = list(roman_pattern.finditer(text))
    if len(roman_matches) >= 2:
        return chapters_from_heading_matches(text, roman_matches)

    pattern = re.compile(
        r"(?m)^(제\s*\d+\s*[장절부편].{0,40}|[0-9]{1,2}\.\s+.{2,40}|Chapter\s+\d+.{0,40}|Prologue|Epilogue)\s*$",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) < 2:
        return []
    return chapters_from_heading_matches(text, matches)


def chapters_from_heading_matches(text: str, matches: list[re.Match[str]]) -> list[Chapter]:
    chapters: list[Chapter] = []
    preface = text[: matches[0].start()].strip()
    if preface:
        chapters.append(Chapter("머리말", preface))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            chapters.append(Chapter(match.group(1).strip(), body))
    return chapters


def chunk_chapters(text: str, max_chars: int = 2600) -> list[Chapter]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chapters: list[Chapter] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        if current and current_len + len(paragraph) > max_chars:
            chapters.append(Chapter(f"제 {len(chapters) + 1}장", "\n\n".join(current)))
            current = []
            current_len = 0
        current.append(paragraph)
        current_len += len(paragraph)
    if current:
        chapters.append(Chapter(f"제 {len(chapters) + 1}장", "\n\n".join(current)))
    return chapters or [Chapter("본문", text)]


def build_markdown(meta: BookMeta, chapters: Iterable[Chapter]) -> str:
    front = [
        f"# {meta.title}",
        "",
        f"저자: {meta.author}",
        f"출판: {meta.publisher}",
        f"생성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    if meta.subtitle:
        front.insert(2, meta.subtitle)
    if meta.description:
        front.extend(["", meta.description.strip()])
    body: list[str] = []
    for chapter in chapters:
        body.extend(["", f"## {chapter.title}", "", chapter.body.strip()])
    return normalize_text("\n".join(front + body)) + "\n"


def paragraphs_from_text(text: str) -> list[str]:
    return [re.sub(r"\s*\n\s*", " ", p.strip()) for p in re.split(r"\n\s*\n", text) if p.strip()]


def select_cover_background(title: str, background_dir: Path | None = None) -> Path | None:
    """Select a project cover background deterministically for a given title."""
    cover_dir = Path(background_dir) if background_dir is not None else COVER_BACKGROUND_DIR
    if not cover_dir.is_dir():
        return None
    candidates: list[Path] = []
    for pattern in ("cover-bg-*.png", "cover-bg-*.jpg", "cover-bg-*.jpeg", "cover-bg-*.webp"):
        candidates.extend(cover_dir.glob(pattern))
    candidates = sorted({candidate.resolve() for candidate in candidates if candidate.is_file()})
    if not candidates:
        return None
    title_hash = int.from_bytes(hashlib.sha256(title.encode("utf-8")).digest()[:8], "big")
    return candidates[title_hash % len(candidates)]


def find_cover_font(*, bold: bool = False) -> str:
    candidates = COVER_BOLD_FONT_CANDIDATES if bold else COVER_REGULAR_FONT_CANDIDATES
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    raise RuntimeError("한글 표지 생성을 위한 실제 한글 글꼴을 찾지 못했습니다.")


def _procedural_cover_background(title: str) -> PILImage.Image:
    """Create a deterministic, text-free background when AI assets are unavailable."""
    width, height = COVER_SIZE
    digest = hashlib.sha256(title.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    palettes = (
        ((4, 12, 38), (18, 73, 112), (45, 212, 191)),
        ((16, 12, 45), (82, 35, 118), (52, 152, 219)),
        ((7, 31, 45), (22, 94, 105), (242, 174, 74)),
        ((25, 15, 31), (110, 39, 70), (242, 120, 75)),
        ((9, 24, 52), (31, 78, 121), (112, 219, 255)),
    )
    top, bottom, accent = palettes[digest[8] % len(palettes)]
    gradient = PILImage.new("RGB", (1, height))
    gradient.putdata(
        [
            tuple(round(top[channel] * (1 - y / (height - 1)) + bottom[channel] * (y / (height - 1))) for channel in range(3))
            for y in range(height)
        ]
    )
    canvas = gradient.resize(COVER_SIZE)

    glow = PILImage.new("RGBA", COVER_SIZE, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    for _ in range(7):
        diameter = rng.randint(260, 720)
        x = rng.randint(-diameter // 2, width - diameter // 2)
        y = rng.randint(-diameter // 2, height - diameter // 2)
        color = tuple(min(255, channel + rng.randint(-20, 28)) for channel in accent)
        glow_draw.ellipse((x, y, x + diameter, y + diameter), fill=(*color, rng.randint(24, 65)))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    canvas = PILImage.alpha_composite(canvas.convert("RGBA"), glow)

    geometry = PILImage.new("RGBA", COVER_SIZE, (0, 0, 0, 0))
    geometry_draw = ImageDraw.Draw(geometry)
    for index in range(9):
        inset = 38 + index * 47
        geometry_draw.arc(
            (width - 680 - inset, -190 + inset, width + 210 - inset, 700 + inset),
            start=195,
            end=338,
            fill=(*accent, max(18, 72 - index * 5)),
            width=3,
        )
    for _ in range(22):
        x = rng.randint(55, width - 55)
        y = rng.randint(60, height - 60)
        radius = rng.choice((2, 3, 5, 8))
        geometry_draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*accent, rng.randint(45, 125)))
    return PILImage.alpha_composite(canvas, geometry).convert("RGB")


def _load_cover_background(
    title: str,
    background_dir: Path | None,
    background_path: Path | None = None,
) -> PILImage.Image:
    if background_path is not None and background_path.is_file():
        try:
            with PILImage.open(background_path) as image:
                return ImageOps.fit(image.convert("RGB"), COVER_SIZE, method=PILImage.Resampling.LANCZOS)
        except (OSError, ValueError):
            pass
    background_path = select_cover_background(title, background_dir)
    if background_path is not None:
        try:
            with PILImage.open(background_path) as image:
                return ImageOps.fit(image.convert("RGB"), COVER_SIZE, method=PILImage.Resampling.LANCZOS)
        except (OSError, ValueError):
            pass
    return _procedural_cover_background(title)


def _wrap_cover_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in (text.splitlines() or [text]):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        line = ""
        for character in paragraph:
            candidate = line + character
            if not line or draw.textlength(candidate, font=font) <= max_width:
                line = candidate
                continue
            break_at = max(line.rfind(" "), line.rfind("\t"))
            if break_at > 0:
                lines.append(line[:break_at].rstrip())
                line = (line[break_at + 1 :] + character).lstrip()
            else:
                lines.append(line.rstrip())
                line = character.lstrip()
        if line:
            lines.append(line.rstrip())
    return lines or [text]


def _fit_title(
    draw: ImageDraw.ImageDraw, title: str, font_path: str, max_width: int, max_height: int
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    for size in range(126, 27, -4):
        font = ImageFont.truetype(font_path, size=size)
        lines = _wrap_cover_text(draw, title, font, max_width)
        line_height = round(size * 1.32)
        if len(lines) * line_height <= max_height:
            return font, lines, line_height
    font = ImageFont.truetype(font_path, size=28)
    return font, _wrap_cover_text(draw, title, font, max_width), 37


def _fit_single_line_font(draw: ImageDraw.ImageDraw, text: str, font_path: str, max_width: int, start_size: int) -> ImageFont.FreeTypeFont:
    for size in range(start_size, 19, -2):
        font = ImageFont.truetype(font_path, size=size)
        if draw.textlength(text, font=font) <= max_width:
            return font
    return ImageFont.truetype(font_path, size=20)


def create_cover(
    meta: BookMeta,
    output_path: Path,
    background_dir: Path | None = None,
    *,
    background_path: Path | None = None,
) -> Path:
    """Render a 1200x1600 PNG cover with exact UTF-8 metadata and Korean text."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = _load_cover_background(meta.title, background_dir, background_path).convert("RGBA")
    overlay = PILImage.new("RGBA", COVER_SIZE, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(
        (72, 145, 1128, 1455),
        radius=42,
        fill=(3, 10, 27, 178),
        outline=(146, 226, 255, 90),
        width=2,
    )
    overlay_draw.rectangle((72, 145, 86, 1455), fill=(54, 217, 201, 220))
    canvas = PILImage.alpha_composite(canvas, overlay)
    draw = ImageDraw.Draw(canvas)

    bold_font_path = find_cover_font(bold=True)
    regular_font_path = find_cover_font()
    eyebrow_font = ImageFont.truetype(bold_font_path, size=28)
    draw.text((132, 225), "EPUBIA  DIGITAL BOOK", font=eyebrow_font, fill=(110, 232, 223))
    draw.line((132, 294, 1068, 294), fill=(157, 230, 255, 125), width=2)

    title_font, title_lines, line_height = _fit_title(draw, meta.title, bold_font_path, 910, 700)
    title_y = 375
    for line in title_lines:
        draw.text((132, title_y), line, font=title_font, fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0))
        title_y += line_height

    if meta.subtitle:
        subtitle_font = _fit_single_line_font(draw, meta.subtitle, regular_font_path, 910, 38)
        draw.text((132, min(1090, title_y + 34)), meta.subtitle, font=subtitle_font, fill=(190, 229, 242))

    author_text = f"저자  {meta.author}"
    publisher_text = meta.publisher
    author_font = _fit_single_line_font(draw, author_text, bold_font_path, 910, 43)
    publisher_font = _fit_single_line_font(draw, publisher_text, regular_font_path, 910, 31)
    draw.line((132, 1212, 360, 1212), fill=(54, 217, 201), width=8)
    draw.text((132, 1250), author_text, font=author_font, fill=(244, 249, 252))
    draw.text((132, 1331), publisher_text, font=publisher_font, fill=(194, 211, 222))

    png_info = PngImagePlugin.PngInfo()
    png_info.add_itxt("Title", meta.title, lang=meta.language)
    png_info.add_itxt("Author", meta.author, lang=meta.language)
    png_info.add_itxt("Publisher", meta.publisher, lang=meta.language)
    png_info.add_text("Language", meta.language)
    canvas.convert("RGB").save(output_path, format="PNG", pnginfo=png_info, optimize=True)
    return output_path


def chapter_xhtml(chapter: Chapter, index: int) -> str:
    html_body = "\n".join(f"<p>{html.escape(p, quote=False)}</p>" for p in paragraphs_from_text(chapter.body))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="ko" xml:lang="ko">
  <head><title>{html.escape(chapter.title)}</title></head>
  <body>
    <h1>{html.escape(chapter.title)}</h1>
    {html_body}
  </body>
</html>"""


def create_epub(meta: BookMeta, chapters: list[Chapter], output_path: Path, cover_path: Path | None = None) -> None:
    identifier = f"urn:uuid:{uuid.uuid4()}"
    has_cover = cover_path is not None and cover_path.is_file()
    css = """
body { font-family: serif; line-height: 1.75; margin: 6%; color: #1f2933; }
h1 { font-size: 1.65em; margin: 1.2em 0 1em; border-bottom: 1px solid #ddd; padding-bottom: .4em; }
p { margin: 0 0 1em; text-indent: 1em; }
.cover-page { margin: 0; padding: 0; text-align: center; }
.cover-page img { display: block; width: 100%; height: auto; margin: 0 auto; }
"""
    cover_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="{html.escape(meta.language)}" xml:lang="{html.escape(meta.language)}">
  <head><title>{html.escape(meta.title)} 표지</title><link rel="stylesheet" type="text/css" href="style/book.css"/></head>
  <body class="cover-page"><img src="images/cover.png" alt="{html.escape(meta.title)} 표지"/></body>
</html>"""
    intro = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="{html.escape(meta.language)}" xml:lang="{html.escape(meta.language)}">
  <head><title>{html.escape(meta.title)}</title></head>
  <body>
    <h1>{html.escape(meta.title)}</h1>
    <p><strong>{html.escape(meta.author)}</strong></p>
    <p>{html.escape(meta.subtitle or meta.description or meta.publisher)}</p>
  </body>
</html>"""
    chapter_files = [(f"chapter_{i:03d}.xhtml", chapter) for i, chapter in enumerate(chapters, start=1)]
    nav_items = "\n".join(
        f'<li><a href="{filename}">{html.escape(chapter.title)}</a></li>' for filename, chapter in chapter_files
    )
    cover_nav_item = '<li><a href="cover.xhtml">표지</a></li>' if has_cover else '<li><a href="intro.xhtml">표제지</a></li>'
    nav = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{html.escape(meta.language)}" xml:lang="{html.escape(meta.language)}">
  <head><title>목차</title></head>
  <body>
    <nav epub:type="toc" id="toc">
      <h1>목차</h1>
      <ol>
        {cover_nav_item}
        {nav_items}
      </ol>
    </nav>
  </body>
</html>"""
    manifest_chapters = "\n".join(
        f'<item id="chapter{i}" href="{filename}" media-type="application/xhtml+xml"/>'
        for i, (filename, _) in enumerate(chapter_files, start=1)
    )
    spine_chapters = "\n".join(f'<itemref idref="chapter{i}"/>' for i in range(1, len(chapter_files) + 1))
    description_metadata = f"<dc:description>{html.escape(meta.description)}</dc:description>" if meta.description else ""
    cover_manifest = (
        '<item id="cover-image" href="images/cover.png" media-type="image/png" properties="cover-image"/>\n'
        '    <item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>'
        if has_cover
        else ""
    )
    cover_spine = '<itemref idref="cover-page" linear="yes"/>' if has_cover else ""
    content_opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{html.escape(meta.language)}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{identifier}</dc:identifier>
    <dc:title>{html.escape(meta.title)}</dc:title>
    <dc:creator>{html.escape(meta.author)}</dc:creator>
    <dc:language>{html.escape(meta.language)}</dc:language>
    <dc:publisher>{html.escape(meta.publisher)}</dc:publisher>
    {description_metadata}
    <meta property="dcterms:modified">{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="style" href="style/book.css" media-type="text/css"/>
    <item id="intro" href="intro.xhtml" media-type="application/xhtml+xml"/>
    {cover_manifest}
    {manifest_chapters}
  </manifest>
  <spine>
    {cover_spine}
    <itemref idref="intro"/>
    {spine_chapters}
  </spine>
</package>"""
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    with zipfile.ZipFile(output_path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", content_opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/style/book.css", css, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/intro.xhtml", intro, compress_type=zipfile.ZIP_DEFLATED)
        if has_cover:
            zf.writestr("OEBPS/cover.xhtml", cover_xhtml, compress_type=zipfile.ZIP_DEFLATED)
            zf.write(cover_path, "OEBPS/images/cover.png", compress_type=zipfile.ZIP_DEFLATED)
        for index, (filename, chapter) in enumerate(chapter_files, start=1):
            zf.writestr(f"OEBPS/{filename}", chapter_xhtml(chapter, index), compress_type=zipfile.ZIP_DEFLATED)


def find_font() -> str:
    for candidate in DEFAULT_FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    raise RuntimeError("한글 PDF 생성을 위한 글꼴을 찾지 못했습니다.")


def register_pdf_font() -> str:
    family = "NotoSansCJK"
    if family in pdfmetrics.getRegisteredFontNames():
        return family
    for font_path in DEFAULT_FONT_CANDIDATES:
        if not os.path.exists(font_path):
            continue
        try:
            pdfmetrics.registerFont(TTFont(family, font_path))
            return family
        except Exception:
            continue

    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    fallback = "HYSMyeongJo-Medium"
    if fallback not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(fallback))
    return fallback


def create_pdf(meta: BookMeta, chapters: list[Chapter], output_path: Path, cover_path: Path | None = None) -> None:
    font_name = register_pdf_font()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "KoreanTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=24,
        leading=32,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#1b2a2f"),
        spaceAfter=14,
    )
    meta_style = ParagraphStyle(
        "KoreanMeta",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=10,
        leading=16,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#58636a"),
    )
    heading_style = ParagraphStyle(
        "KoreanHeading",
        parent=styles["Heading1"],
        fontName=font_name,
        fontSize=17,
        leading=24,
        textColor=colors.HexColor("#243b42"),
        spaceAfter=9,
    )
    body_style = ParagraphStyle(
        "KoreanBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=10.5,
        leading=18,
        firstLineIndent=8,
        spaceAfter=7,
    )

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A5,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=meta.title,
        author=meta.author,
    )
    story = []
    if cover_path is not None and cover_path.is_file():
        available_width = A5[0] - (32 * mm)
        available_height = A5[1] - (36 * mm)
        cover_width = min(available_width, available_height * COVER_SIZE[0] / COVER_SIZE[1])
        cover_height = cover_width * COVER_SIZE[1] / COVER_SIZE[0]
        story.extend(
            [
                Spacer(1, max(0, (available_height - cover_height) / 2)),
                ReportLabImage(str(cover_path), width=cover_width, height=cover_height, hAlign="CENTER"),
                PageBreak(),
            ]
        )
    story.extend([
        Spacer(1, 35 * mm),
        Paragraph(html.escape(meta.title), title_style),
        Paragraph(html.escape(meta.subtitle), meta_style) if meta.subtitle else Spacer(1, 1),
        Spacer(1, 8 * mm),
        Paragraph(f"저자 {html.escape(meta.author)}", meta_style),
        Paragraph(html.escape(meta.publisher), meta_style),
        PageBreak(),
    ])
    if meta.description:
        story.extend([Paragraph("소개", heading_style), Paragraph(html.escape(meta.description), body_style), PageBreak()])

    for chapter in chapters:
        story.append(Paragraph(html.escape(chapter.title), heading_style))
        for paragraph in paragraphs_from_text(chapter.body):
            story.append(Paragraph(html.escape(paragraph), body_style))
        story.append(PageBreak())
    doc.build(story)


def build_book(
    source_path: Path,
    meta: BookMeta,
    output_root: Path,
    *,
    prepared_cover_path: Path | None = None,
    prepared_cover_mode: str = "ai",
    cover_creator: Callable[[BookMeta, str, Path], str | None] | None = None,
    extracted_text_override: str | None = None,
    max_source_bytes: int | None = None,
) -> BuildResult:
    validate_source(source_path, max_source_bytes)
    book_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    book_dir = output_root / book_id
    book_dir.mkdir(parents=True, exist_ok=False)

    try:
        source_suffix = source_path.suffix.lower()
        extracted_html: ExtractedHTMLBook | None = None
        if source_suffix == ".zip":
            extracted_html = extract_html_book(
                source_path,
                book_dir / "html",
                limits=HTMLBookLimits(
                    max_visible_text_chars=HTML_BOOK_PUBLICATION_TEXT_CHARS,
                ),
            )

        if extracted_html is not None:
            extracted_text = (
                html_book_publication_text(
                    extracted_html,
                    max_chars=HTML_BOOK_PUBLICATION_TEXT_CHARS,
                )
                or HTML_TEXT_UNAVAILABLE_MESSAGE
            )
        elif extracted_text_override is not None:
            extracted_text = extracted_text_override
        else:
            extracted_text = extract_text(source_path, max_source_bytes)
        if not meta.title:
            meta.title = (
                display_title_from_filename(source_path)
                if source_suffix == ".pdf"
                else infer_title(extracted_text, source_path.stem)
            )
        if not meta.author:
            meta.author = "기혜경"
        chapters = split_chapters(extracted_text)
        base_name = safe_filename(meta.title, book_id)

        source_text_path = book_dir / "source.txt"
        markdown_path = book_dir / f"{base_name}.md"
        cover_path = book_dir / "cover.png"
        epub_path = None if extracted_html is not None else book_dir / f"{base_name}.epub"
        pdf_path = None if extracted_html is not None else book_dir / f"{base_name}.pdf"
        html_archive_path = book_dir / f"{base_name}.zip" if extracted_html is not None else None

        source_text_path.write_text(extracted_text, encoding="utf-8")
        markdown_path.write_text(build_markdown(meta, chapters), encoding="utf-8")
        cover_mode = "template"
        if prepared_cover_path is not None and prepared_cover_path.is_file():
            shutil.copyfile(prepared_cover_path, cover_path)
            cover_mode = prepared_cover_mode if prepared_cover_mode in {"ai", "template"} else "ai"
        elif cover_creator is not None:
            cover_mode = cover_creator(meta, extracted_text, cover_path) or "ai"
            if not cover_path.is_file():
                raise RuntimeError("표지 생성 결과 파일을 찾을 수 없습니다.")
        else:
            create_cover(meta, cover_path)
        if epub_path is not None:
            create_epub(meta, chapters, epub_path, cover_path)
        if source_suffix == ".pdf" and pdf_path is not None:
            shutil.copyfile(source_path, pdf_path)
        elif pdf_path is not None:
            create_pdf(meta, chapters, pdf_path, cover_path)
        if html_archive_path is not None:
            shutil.copyfile(source_path, html_archive_path)

        publication_type = "html" if extracted_html is not None else "pdf" if source_suffix == ".pdf" else "text"

        return BuildResult(
            book_id=book_id,
            title=meta.title,
            author=meta.author,
            chapter_count=len(chapters),
            source_text_path=source_text_path,
            markdown_path=markdown_path,
            cover_path=cover_path,
            epub_path=epub_path,
            pdf_path=pdf_path,
            created_at=datetime.now().isoformat(timespec="seconds"),
            cover_mode=cover_mode,
            publication_type=publication_type,
            html_path=extracted_html.entrypoint if extracted_html is not None else None,
            html_archive_path=html_archive_path,
        )
    except Exception:
        shutil.rmtree(book_dir, ignore_errors=True)
        raise
