import base64
import hashlib
import hmac
import json
import re
import time
from pathlib import Path

import pytest

import app as app_module


SECRET = "shared-reader-secret-for-tests-0123456789"


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def saju_ticket(book_id: str, *, user_id: int = 41, exp: int | None = None, secret: str = SECRET) -> str:
    payload = {
        "bookId": book_id,
        "userId": user_id,
        "scope": "full",
        "exp": exp if exp is not None else int(time.time()) + 90,
        "nonce": "test-reader-nonce",
    }
    encoded = b64url(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    signature = b64url(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def write_paid_html_book(root: Path, book_id: str = "paid-reader") -> None:
    book_dir = root / book_id
    html_root = book_dir / "html"
    image_root = html_root / "images"
    image_root.mkdir(parents=True)
    pages = []
    for page in range(1, 4):
        image = f"page-{page:03d}.jpg"
        (image_root / image).write_bytes(f"paid-image-{page}".encode())
        pages.append({
            "page": page,
            "title": f"구매자 {page}쪽",
            "paragraphs": [f"구매자에게만 보이는 {page}쪽 본문"],
            "image": f"images/{image}",
        })
    (html_root / "index.html").write_text("원본", encoding="utf-8")
    (html_root / "ocr_pages.json").write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    cover = book_dir / "cover.png"
    cover.write_bytes(b"cover")
    manifest = {
        "book_id": book_id,
        "title": "구매자 전용 관상책",
        "author": "기혜경",
        "description": "열람권 검증용 책",
        "created_at": "2026-07-11T20:00:00",
        "publication_type": "html",
        "cover_path": str(cover),
        "html_root": str(html_root),
        "html_path": str(html_root / "index.html"),
        "html_entry": "index.html",
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
    (book_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def access_library(monkeypatch, tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    write_paid_html_book(root)
    write_paid_html_book(root, "other-reader")
    monkeypatch.setattr(app_module, "BOOK_DIR", root)
    monkeypatch.setattr(app_module, "EBOOK_ACCESS_NONCE_DIR", tmp_path / "used-nonces")
    monkeypatch.setenv("EBOOK_ACCESS_SECRET", SECRET)
    return root


def test_saju_access_ticket_verification_rejects_tampering_and_expiry(monkeypatch):
    monkeypatch.setenv("EBOOK_ACCESS_SECRET", SECRET)
    valid = saju_ticket("paid-reader")
    assert app_module.verify_ebook_access_ticket(valid)["bookId"] == "paid-reader"
    assert app_module.verify_ebook_access_ticket(valid + "x") is None
    assert app_module.verify_ebook_access_ticket(saju_ticket("paid-reader", exp=int(time.time()) - 1)) is None
    assert app_module.verify_ebook_access_ticket(saju_ticket("../secret")) is None


def test_access_exchange_sets_scoped_http_only_grant_and_redirects(access_library):
    with app_module.app.test_client() as client:
        response = client.post("/library/access", data={"ticket": saju_ticket("paid-reader")})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/library/books/paid-reader/read")
    cookie = response.headers["Set-Cookie"]
    assert "epubia_reader_grant=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=Lax" in cookie
    assert "Path=/library/books/paid-reader" in cookie


def test_paid_full_reader_requires_grant_and_exchange_unlocks_every_page(access_library):
    with app_module.app.test_client() as anonymous:
        denied = anonymous.get("/library/books/paid-reader/read")

    with app_module.app.test_client() as buyer:
        exchange = buyer.post("/library/access", data={"ticket": saju_ticket("paid-reader")})
        full_reader = buyer.get(exchange.headers["Location"])
        final_scan = buyer.get("/library/books/paid-reader/pages/3")

    assert denied.status_code == 403
    assert full_reader.status_code == 200
    assert b"sandbox=" in full_reader.data
    iframe_path = re.search(rb'src="https://html\.epub\.xsw\.kr([^\"]+)', full_reader.data)
    assert iframe_path is not None
    original = buyer.get(iframe_path.group(1).decode(), headers={"Host": "html.epub.xsw.kr"})
    assert original.status_code == 200
    assert original.data == "원본".encode()
    assert final_scan.status_code == 200
    assert final_scan.data == b"paid-image-3"


def test_book_scoped_grant_cannot_unlock_another_paid_book(access_library):
    with app_module.app.test_client() as buyer:
        buyer.post("/library/access", data={"ticket": saju_ticket("paid-reader")})
        other = buyer.get("/library/books/other-reader/read")

    assert other.status_code == 403


def test_invalid_ticket_and_nonpublished_book_fail_closed(access_library):
    private_manifest_path = access_library / "paid-reader" / "manifest.json"
    private_manifest = json.loads(private_manifest_path.read_text(encoding="utf-8"))

    with app_module.app.test_client() as client:
        invalid = client.post("/library/access", data={"ticket": "not-a-ticket"})
        private_manifest["commerce"]["sale_status"] = "private"
        private_manifest_path.write_text(json.dumps(private_manifest), encoding="utf-8")
        private = client.post("/library/access", data={"ticket": saju_ticket("paid-reader")})

    assert invalid.status_code == 404
    assert private.status_code == 404


def test_access_ticket_nonce_can_only_be_exchanged_once(access_library, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "EBOOK_ACCESS_NONCE_DIR", tmp_path / "used-nonces")
    ticket = saju_ticket("paid-reader")

    with app_module.app.test_client() as client:
        first = client.post("/library/access", data={"ticket": ticket})
        replay = client.post("/library/access", data={"ticket": ticket})

    assert first.status_code == 302
    assert replay.status_code == 404


def test_archived_free_book_is_not_anonymous_full_content(access_library):
    manifest_path = access_library / "paid-reader" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["commerce"].update({"sale_status": "archived", "access": "free", "price_krw": 0})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with app_module.app.test_client() as client:
        response = client.get("/library/books/paid-reader/read")

    assert response.status_code == 403


def test_archived_paid_book_remains_readable_with_existing_entitlement_ticket(access_library):
    manifest_path = access_library / "paid-reader" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["commerce"]["sale_status"] = "archived"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with app_module.app.test_client() as client:
        sample = client.get("/books/paid-reader/sample")
        exchange = client.post("/library/access", data={"ticket": saju_ticket("paid-reader")})
        reader = client.get(exchange.headers["Location"])

    assert sample.status_code == 404
    assert exchange.status_code == 302
    assert reader.status_code == 200
    assert b"sandbox=" in reader.data
