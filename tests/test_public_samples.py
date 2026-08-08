import json
from pathlib import Path

import pytest

import app as app_module


def write_manifest(root: Path, book_id: str, **overrides) -> Path:
    book_dir = root / book_id
    book_dir.mkdir(parents=True)
    cover = book_dir / "cover.png"
    cover.write_bytes(b"png")
    payload = {
        "book_id": book_id,
        "title": "관상 실전 샘플",
        "author": "기혜경",
        "description": "앞부분을 먼저 읽어보세요.",
        "created_at": "2026-07-11T19:30:00",
        "publication_type": "html",
        "cover_path": str(cover),
        "commerce": {
            "sale_status": "published",
            "access": "paid",
            "price_krw": 19000,
            "sample_pages": 2,
            "consultation_benefit": {
                "type": "percent",
                "value": 10,
                "max_discount_krw": 10000,
                "max_uses": 1,
                "valid_days": 90,
            },
        },
    }
    payload.update(overrides)
    (book_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return book_dir


@pytest.fixture
def isolated_library(monkeypatch, tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    monkeypatch.setattr(app_module, "BOOK_DIR", root)
    return root


def write_ocr_html_book(root: Path, book_id: str = "paid-html", **manifest_overrides) -> Path:
    book_dir = write_manifest(root, book_id, **manifest_overrides)
    html_root = book_dir / "html"
    images = html_root / "images"
    images.mkdir(parents=True)
    pages = []
    for page in range(1, 4):
        image_name = f"page-{page:03d}.jpg"
        (images / image_name).write_bytes(f"image-{page}".encode())
        pages.append(
            {
                "page": page,
                "title": f"제 {page}쪽 제목",
                "paragraphs": [f"공개 범위를 확인하는 {page}쪽 본문"],
                "text": f"공개 범위를 확인하는 {page}쪽 본문",
                "image": f"images/{image_name}",
            }
        )
    (html_root / "index.html").write_text("전체 원문 HTML", encoding="utf-8")
    (html_root / "ocr_pages.json").write_text(
        json.dumps(pages, ensure_ascii=False),
        encoding="utf-8",
    )
    manifest_path = book_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "html_root": str(html_root),
            "html_path": str(html_root / "index.html"),
            "html_entry": "index.html",
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return book_dir


def test_paid_html_sample_only_contains_configured_leading_pages(isolated_library):
    write_ocr_html_book(isolated_library)

    with app_module.app.test_client() as client:
        response = client.get("/books/paid-html/sample")

    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("private, no-store")
    assert "제 1쪽 제목".encode() in response.data
    assert "제 2쪽 제목".encode() in response.data
    assert "제 3쪽 제목".encode() not in response.data
    assert "2쪽 무료 미리보기".encode() in response.data
    assert b'data-book-reader' in response.data
    assert b'data-reader-leaf' in response.data
    assert b'data-reader-stage' in response.data
    assert b'data-reader-prev' in response.data
    assert b'data-reader-next' in response.data
    assert b'data-reader-mode-toggle' in response.data
    assert b'aria-live="polite"' in response.data


def test_html_sample_scan_asset_is_limited_by_server_page_range(isolated_library):
    write_ocr_html_book(isolated_library)

    with app_module.app.test_client() as client:
        allowed = client.get("/books/paid-html/sample/pages/2")
        blocked = client.get("/books/paid-html/sample/pages/3")

    assert allowed.status_code == 200
    assert allowed.data == b"image-2"
    assert blocked.status_code == 404


def test_private_and_archived_books_never_have_public_samples(isolated_library):
    private_dir = write_ocr_html_book(isolated_library, "private-html")
    private_manifest = json.loads((private_dir / "manifest.json").read_text(encoding="utf-8"))
    private_manifest["commerce"]["sale_status"] = "private"
    (private_dir / "manifest.json").write_text(json.dumps(private_manifest), encoding="utf-8")

    archived_dir = write_ocr_html_book(isolated_library, "archived-html")
    archived_manifest = json.loads((archived_dir / "manifest.json").read_text(encoding="utf-8"))
    archived_manifest["commerce"]["sale_status"] = "archived"
    (archived_dir / "manifest.json").write_text(json.dumps(archived_manifest), encoding="utf-8")

    with app_module.app.test_client() as client:
        private_sample = client.get("/books/private-html/sample")
        archived_sample = client.get("/books/archived-html/sample")

    assert private_sample.status_code == 404
    assert archived_sample.status_code == 404


def test_paid_pdf_sample_never_serves_pages_after_configured_limit(isolated_library, monkeypatch):
    book_dir = write_manifest(isolated_library, "paid-pdf", publication_type="pdf")
    pdf = book_dir / "original.pdf"
    pdf.write_bytes(b"pdf")
    manifest_path = book_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pdf_path"] = str(pdf)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    rendered = book_dir / "rendered.png"
    rendered.write_bytes(b"rendered-page")
    monkeypatch.setattr(app_module, "pdf_page_count", lambda _path: 91)
    monkeypatch.setattr(
        app_module,
        "render_pdf_page",
        lambda _pdf, _cache, _page, _variant: rendered,
    )

    with app_module.app.test_client() as client:
        shell = client.get("/books/paid-pdf/sample")
        allowed = client.get("/books/paid-pdf/sample/pages/2")
        blocked = client.get("/books/paid-pdf/sample/pages/3")

    assert shell.status_code == 200
    assert "2쪽 무료 미리보기".encode() in shell.data
    assert allowed.status_code == 200
    assert allowed.data == b"rendered-page"
    assert blocked.status_code == 404


def test_free_html_book_opens_the_original_isolated_webbook(isolated_library):
    book_dir = write_ocr_html_book(isolated_library, "free-html")
    manifest_path = book_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["commerce"].update({"access": "free", "price_krw": 0})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    with app_module.app.test_client() as client:
        response = client.get("/books/free-html/sample")
        final_scan = client.get("/books/free-html/sample/pages/3")

    assert response.status_code == 200
    assert b"sandbox=" in response.data
    assert b"html-content" in response.data
    assert final_scan.status_code == 200
