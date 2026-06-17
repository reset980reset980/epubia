from __future__ import annotations

import html
import os
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown"}
DEFAULT_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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
    epub_path: Path
    pdf_path: Path
    created_at: str


def safe_filename(value: str, fallback: str = "book") -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._ -]+", "_", value or "").strip(" ._")
    cleaned = re.sub(r"\s+", "_", cleaned)
    return (cleaned[:80] or fallback).strip("_") or fallback


def validate_source(path: Path) -> None:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"지원하지 않는 파일 형식입니다. 허용 형식: {allowed}")
    if path.stat().st_size > 30 * 1024 * 1024:
        raise ValueError("파일 크기는 30MB 이하여야 합니다.")


def extract_text(source_path: Path) -> str:
    validate_source(source_path)
    suffix = source_path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(source_path)
    return read_text_file(source_path)


def extract_pdf_text(source_path: Path) -> str:
    chunks: list[str] = []
    reader = PdfReader(str(source_path))
    for page_index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            chunks.append(f"\n\n[페이지 {page_index}]\n{text}")
    extracted = normalize_text("\n".join(chunks))
    if not extracted:
        raise ValueError("PDF에서 텍스트를 추출하지 못했습니다. 스캔 이미지 PDF는 OCR 처리가 필요합니다.")
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


def infer_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        candidate = line.strip(" #\t")
        if 2 <= len(candidate) <= 50 and not candidate.startswith("[페이지"):
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
    pattern = re.compile(
        r"(?m)^(제\s*\d+\s*[장절부편].{0,40}|[0-9]{1,2}\.\s+.{2,40}|Chapter\s+\d+.{0,40}|Prologue|Epilogue)\s*$",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) < 2:
        return []
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


def create_epub(meta: BookMeta, chapters: list[Chapter], output_path: Path) -> None:
    identifier = f"urn:uuid:{uuid.uuid4()}"
    css = """
body { font-family: serif; line-height: 1.75; margin: 6%; color: #1f2933; }
h1 { font-size: 1.65em; margin: 1.2em 0 1em; border-bottom: 1px solid #ddd; padding-bottom: .4em; }
p { margin: 0 0 1em; text-indent: 1em; }
"""
    intro = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="ko" xml:lang="ko">
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
    nav = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="ko" xml:lang="ko">
  <head><title>목차</title></head>
  <body>
    <nav epub:type="toc" id="toc">
      <h1>목차</h1>
      <ol>
        <li><a href="intro.xhtml">표지</a></li>
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
    content_opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="ko">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{identifier}</dc:identifier>
    <dc:title>{html.escape(meta.title)}</dc:title>
    <dc:creator>{html.escape(meta.author)}</dc:creator>
    <dc:language>{meta.language}</dc:language>
    <dc:publisher>{html.escape(meta.publisher)}</dc:publisher>
    <meta property="dcterms:modified">{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="style" href="style/book.css" media-type="text/css"/>
    <item id="intro" href="intro.xhtml" media-type="application/xhtml+xml"/>
    {manifest_chapters}
  </manifest>
  <spine>
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
        for index, (filename, chapter) in enumerate(chapter_files, start=1):
            zf.writestr(f"OEBPS/{filename}", chapter_xhtml(chapter, index), compress_type=zipfile.ZIP_DEFLATED)


def find_font() -> str:
    for candidate in DEFAULT_FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    raise RuntimeError("한글 PDF 생성을 위한 글꼴을 찾지 못했습니다.")


def register_pdf_font() -> str:
    font_path = find_font()
    family = "NotoSansCJK"
    try:
        pdfmetrics.registerFont(TTFont(family, font_path))
        return family
    except Exception:
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont

        fallback = "HYSMyeongJo-Medium"
        pdfmetrics.registerFont(UnicodeCIDFont(fallback))
        return fallback


def create_pdf(meta: BookMeta, chapters: list[Chapter], output_path: Path) -> None:
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
    story = [
        Spacer(1, 35 * mm),
        Paragraph(html.escape(meta.title), title_style),
        Paragraph(html.escape(meta.subtitle), meta_style) if meta.subtitle else Spacer(1, 1),
        Spacer(1, 8 * mm),
        Paragraph(f"저자 {html.escape(meta.author)}", meta_style),
        Paragraph(html.escape(meta.publisher), meta_style),
        PageBreak(),
    ]
    if meta.description:
        story.extend([Paragraph("소개", heading_style), Paragraph(html.escape(meta.description), body_style), PageBreak()])

    for chapter in chapters:
        story.append(Paragraph(html.escape(chapter.title), heading_style))
        for paragraph in paragraphs_from_text(chapter.body):
            story.append(Paragraph(html.escape(paragraph), body_style))
        story.append(PageBreak())
    doc.build(story)


def build_book(source_path: Path, meta: BookMeta, output_root: Path) -> BuildResult:
    validate_source(source_path)
    book_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    book_dir = output_root / book_id
    book_dir.mkdir(parents=True, exist_ok=False)

    try:
        extracted_text = extract_text(source_path)
        if not meta.title:
            meta.title = infer_title(extracted_text, source_path.stem)
        if not meta.author:
            meta.author = "기혜경"
        chapters = split_chapters(extracted_text)
        base_name = safe_filename(meta.title, book_id)

        source_text_path = book_dir / "source.txt"
        markdown_path = book_dir / f"{base_name}.md"
        epub_path = book_dir / f"{base_name}.epub"
        pdf_path = book_dir / f"{base_name}.pdf"

        source_text_path.write_text(extracted_text, encoding="utf-8")
        markdown_path.write_text(build_markdown(meta, chapters), encoding="utf-8")
        create_epub(meta, chapters, epub_path)
        create_pdf(meta, chapters, pdf_path)

        return BuildResult(
            book_id=book_id,
            title=meta.title,
            author=meta.author,
            chapter_count=len(chapters),
            source_text_path=source_text_path,
            markdown_path=markdown_path,
            epub_path=epub_path,
            pdf_path=pdf_path,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception:
        shutil.rmtree(book_dir, ignore_errors=True)
        raise
