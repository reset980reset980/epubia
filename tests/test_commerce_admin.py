import json
from urllib.parse import parse_qs, urlsplit

import pytest

import app as app_module


def write_book(root, book_id, *, commerce=None, title="관상과 운명", created_at="2026-07-11T10:00:00"):
    book_dir = root / book_id
    book_dir.mkdir(parents=True)
    source = book_dir / "source.txt"
    cover = book_dir / "cover.png"
    source.write_text("사주와 관상에 관한 원문", encoding="utf-8")
    cover.write_bytes(b"png")
    manifest = {
        "book_id": book_id,
        "title": title,
        "author": "기혜경",
        "description": "하루의 운기를 읽는 방법을 소개합니다.",
        "chapter_count": 12,
        "created_at": created_at,
        "publication_type": "pdf",
        "source_path": str(source),
        "cover_path": str(cover),
        "private_note": "외부에 공개하면 안 되는 내부 메모",
    }
    if commerce is not None:
        manifest["commerce"] = commerce
    (book_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return book_dir, manifest


@pytest.fixture
def isolated_books(monkeypatch, tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    monkeypatch.setattr(app_module, "BOOK_DIR", root)
    monkeypatch.setattr(app_module, "SITE_EDITOR_USERS", {"admin"})
    return root


def login(client, *, username="admin", csrf="commerce-csrf"):
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["username"] = username
        session["_csrf_token"] = csrf


def valid_paid_form(**overrides):
    payload = {
        "csrf_token": "commerce-csrf",
        "sale_status": "published",
        "access": "paid",
        "price_krw": "19000",
        "sample_pages": "5",
        "benefit_type": "percent",
        "benefit_value": "10",
        "benefit_max_discount_krw": "10000",
        "benefit_max_uses": "1",
        "benefit_valid_days": "90",
    }
    payload.update(overrides)
    return payload


def test_legacy_manifest_is_normalized_to_safe_private_defaults(isolated_books):
    write_book(isolated_books, "legacy-book")

    manifest = app_module.read_manifest("legacy-book")

    assert manifest["commerce"] == {
        "sale_status": "private",
        "access": "free",
        "price_krw": 0,
        "sample_pages": 5,
        "consultation_benefit": {
            "type": "none",
            "value": 0,
            "max_discount_krw": 10000,
            "max_uses": 1,
            "valid_days": 90,
        },
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sale_status", "deleted"),
        ("access", "rental"),
        ("price_krw", "-1"),
        ("price_krw", "99"),
        ("price_krw", "one million"),
        ("sample_pages", "0"),
        ("sample_pages", "501"),
        ("benefit_type", "coupon"),
        ("benefit_value", "101"),
        ("benefit_max_discount_krw", "-1"),
        ("benefit_max_uses", "0"),
        ("benefit_valid_days", "0"),
    ],
)
def test_commerce_update_rejects_invalid_values_without_writing(isolated_books, field, value):
    _book_dir, original = write_book(isolated_books, "validation-book")
    with app_module.app.test_client() as client:
        login(client)
        response = client.post(
            "/settings/books/validation-book/commerce",
            data=valid_paid_form(**{field: value}),
        )

    saved = json.loads((isolated_books / "validation-book" / "manifest.json").read_text(encoding="utf-8"))
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings#book-commerce")
    assert saved == original


def test_paid_book_requires_positive_price(isolated_books):
    _book_dir, original = write_book(isolated_books, "zero-price")
    with app_module.app.test_client() as client:
        login(client)
        response = client.post(
            "/settings/books/zero-price/commerce",
            data=valid_paid_form(price_krw="0"),
        )

    saved = json.loads((isolated_books / "zero-price" / "manifest.json").read_text(encoding="utf-8"))
    assert response.status_code == 302
    assert saved == original


def test_commerce_update_requires_admin_and_csrf(isolated_books):
    write_book(isolated_books, "protected-book")

    with app_module.app.test_client() as client:
        login(client, username="reader")
        forbidden = client.post(
            "/settings/books/protected-book/commerce",
            data=valid_paid_form(),
        )

    with app_module.app.test_client() as client:
        login(client)
        missing_csrf = client.post(
            "/settings/books/protected-book/commerce",
            data=valid_paid_form(csrf_token="wrong"),
        )

    assert forbidden.status_code == 403
    assert missing_csrf.status_code == 400


def test_non_admin_cannot_open_settings_or_inspect_private_catalog(isolated_books):
    write_book(isolated_books, "private-settings-book", title="관리자 비공개 원고")

    with app_module.app.test_client() as client:
        login(client, username="reader")
        response = client.get("/settings")

    assert response.status_code == 403
    assert "관리자 비공개 원고".encode() not in response.data
    assert str(isolated_books).encode() not in response.data


def test_admin_can_save_paid_book_and_settings_lists_it(isolated_books):
    write_book(isolated_books, "paid-book", title="관상 실전 안내서")
    with app_module.app.test_client() as client:
        login(client)
        saved = client.post(
            "/settings/books/paid-book/commerce",
            data=valid_paid_form(),
        )
        settings = client.get("/settings")

    manifest = app_module.read_manifest("paid-book")
    assert saved.status_code == 302
    assert manifest["commerce"]["sale_status"] == "published"
    assert manifest["commerce"]["access"] == "paid"
    assert manifest["commerce"]["price_krw"] == 19000
    assert manifest["commerce"]["consultation_benefit"]["value"] == 10
    assert settings.status_code == 200
    assert "책별 판매 설정".encode() in settings.data
    assert "관상 실전 안내서".encode() in settings.data


def test_admin_can_publish_free_book_without_losing_login_session(isolated_books):
    write_book(isolated_books, "free-published-book", title="무료 공개 전자책")

    with app_module.app.test_client() as client:
        login(client)
        response = client.post(
            "/settings/books/free-published-book/commerce",
            data=valid_paid_form(access="free", price_krw="0", benefit_type="none", benefit_value="0"),
            follow_redirects=True,
        )
        with client.session_transaction() as saved_session:
            authenticated = saved_session.get("authenticated")

    assert response.status_code == 200
    assert "무료 공개 전자책" in response.get_data(as_text=True)
    assert "판매 설정을 저장했습니다" in response.get_data(as_text=True)
    assert "운영자 로그인" not in response.get_data(as_text=True)
    assert authenticated is True
    commerce = app_module.read_manifest("free-published-book")["commerce"]
    assert commerce["sale_status"] == "published"
    assert commerce["access"] == "free"
    assert commerce["price_krw"] == 0


def test_archive_and_restore_are_soft_delete_operations(isolated_books):
    book_dir, _manifest = write_book(
        isolated_books,
        "archive-book",
        commerce={**app_module.DEFAULT_COMMERCE, "sale_status": "published"},
    )
    original_files = {path.name for path in book_dir.iterdir()}

    with app_module.app.test_client() as client:
        login(client)
        archived = client.post(
            "/settings/books/archive-book/archive",
            data={"csrf_token": "commerce-csrf"},
        )
        archived_manifest = app_module.read_manifest("archive-book")
        restored = client.post(
            "/settings/books/archive-book/restore",
            data={"csrf_token": "commerce-csrf"},
        )

    assert archived.status_code == 302
    assert archived_manifest["commerce"]["sale_status"] == "archived"
    assert restored.status_code == 302
    assert app_module.read_manifest("archive-book")["commerce"]["sale_status"] == "published"
    assert original_files <= {path.name for path in book_dir.iterdir()}
    assert (book_dir / "source.txt").read_text(encoding="utf-8") == "사주와 관상에 관한 원문"


def test_archive_and_restore_require_admin_and_csrf(isolated_books):
    write_book(isolated_books, "archive-protected")
    with app_module.app.test_client() as client:
        login(client, username="reader")
        forbidden = client.post(
            "/settings/books/archive-protected/archive",
            data={"csrf_token": "commerce-csrf"},
        )
    with app_module.app.test_client() as client:
        login(client)
        bad_csrf = client.post(
            "/settings/books/archive-protected/restore",
            data={"csrf_token": "bad"},
        )

    assert forbidden.status_code == 403
    assert bad_csrf.status_code == 400


def test_archive_rejects_invalid_book_id_before_creating_lock(isolated_books):
    escaped_lock = isolated_books.parent / ".commerce.lock"
    with app_module.app.test_client() as client:
        login(client)
        response = client.post(
            "/settings/books/../archive",
            data={"csrf_token": "commerce-csrf"},
        )

    assert response.status_code == 404
    assert not escaped_lock.exists()


def test_public_catalog_only_returns_published_books_and_never_paths(isolated_books):
    write_book(
        isolated_books,
        "public-free",
        commerce={**app_module.DEFAULT_COMMERCE, "sale_status": "published"},
        title="무료 사주 입문",
    )
    write_book(
        isolated_books,
        "public-paid",
        commerce={
            **app_module.DEFAULT_COMMERCE,
            "sale_status": "published",
            "access": "paid",
            "price_krw": 25000,
        },
        title="유료 관상 실전",
    )
    write_book(isolated_books, "private-book", title="비공개 책")
    write_book(
        isolated_books,
        "archived-book",
        commerce={**app_module.DEFAULT_COMMERCE, "sale_status": "archived"},
        title="보관된 책",
    )

    with app_module.app.test_client() as client:
        listing = client.get("/api/catalog/books")
        private_detail = client.get("/api/catalog/books/private-book")
        paid_detail = client.get("/api/catalog/books/public-paid")
        published_cover = client.get("/api/catalog/books/public-paid/cover")
        private_cover = client.get("/api/catalog/books/private-book/cover")
        archived_cover = client.get("/api/catalog/books/archived-book/cover")

    assert listing.status_code == 200
    payload = listing.get_json()
    assert [book["id"] for book in payload["books"]] == ["public-paid", "public-free"]
    assert payload["count"] == 2
    assert private_detail.status_code == 404
    assert paid_detail.status_code == 200
    assert published_cover.status_code == 200
    assert private_cover.status_code == 404
    assert archived_cover.status_code == 200
    assert paid_detail.get_json()["status"] == "published"
    encoded = json.dumps({"list": payload, "detail": paid_detail.get_json()}, ensure_ascii=False)
    assert "source_path" not in encoded
    assert "cover_path" not in encoded
    assert "private_note" not in encoded
    assert str(isolated_books) not in encoded
    assert paid_detail.get_json()["commerce"]["price_krw"] == 25000
    assert paid_detail.get_json()["cover_url"].endswith("/api/catalog/books/public-paid/cover")
    assert paid_detail.get_json()["sample_url"].endswith("/books/public-paid/sample")


def test_gallery_and_detail_show_commerce_badges_for_publisher(isolated_books):
    write_book(
        isolated_books,
        "paid-gallery",
        commerce={
            **app_module.DEFAULT_COMMERCE,
            "sale_status": "published",
            "access": "paid",
            "price_krw": 32000,
            "consultation_benefit": {
                "type": "percent",
                "value": 10,
                "max_discount_krw": 10000,
                "max_uses": 1,
                "valid_days": 90,
            },
        },
        title="관상 유료 전자책",
    )
    write_book(isolated_books, "private-gallery", title="내부 검토본")
    write_book(
        isolated_books,
        "archive-gallery",
        commerce={**app_module.DEFAULT_COMMERCE, "sale_status": "archived"},
        title="판매 종료본",
    )

    with app_module.app.test_client() as client:
        login(client)
        gallery = client.get("/")
        detail = client.get("/books/paid-gallery")

    assert gallery.status_code == 200
    assert "유료".encode() in gallery.data
    assert "32,000원".encode() in gallery.data
    assert "비공개".encode() in gallery.data
    assert "보관됨".encode() in gallery.data
    assert detail.status_code == 200
    assert "판매 공개".encode() in detail.data
    assert "상담료 10% 할인".encode() in detail.data


def test_public_library_is_visible_without_login_and_hides_unpublished_books(isolated_books):
    write_book(
        isolated_books,
        "public-free-library",
        commerce={**app_module.DEFAULT_COMMERCE, "sale_status": "published"},
        title="무료 사주 입문서",
    )
    write_book(
        isolated_books,
        "public-paid-library",
        commerce={
            **app_module.DEFAULT_COMMERCE,
            "sale_status": "published",
            "access": "paid",
            "price_krw": 27000,
            "sample_pages": 7,
        },
        title="유료 관상 실전서",
    )
    write_book(isolated_books, "private-library", title="관리자 비공개 원고")
    write_book(
        isolated_books,
        "archived-library",
        commerce={**app_module.DEFAULT_COMMERCE, "sale_status": "archived"},
        title="판매 종료 원고",
    )

    with app_module.app.test_client() as client:
        response = client.get("/")

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "private" in response.headers["Cache-Control"]
    assert "no-store" in response.headers["Cache-Control"]
    assert "Cookie" in response.headers["Vary"]
    assert "Set-Cookie" not in response.headers
    assert "무료 사주 입문서" in html
    assert "유료 관상 실전서" in html
    assert "27,000원" in html
    assert "관리자 비공개 원고" not in html
    assert "판매 종료 원고" not in html
    assert "외부에 공개하면 안 되는 내부 메모" not in html
    assert str(isolated_books) not in html
    assert 'id="publish-workbench"' not in html
    assert 'action="/publish"' not in html
    assert "/api/catalog/books/public-free-library/cover" in html
    assert "/books/public-free-library/sample" in html
    assert "/books/public-paid-library/sample" in html
    assert "https://saju.xsw.kr/books/public-paid-library" in html
    assert "서재" in html
    assert "로그인" in html


def test_public_library_keeps_publisher_controls_behind_login(isolated_books):
    write_book(
        isolated_books,
        "protected-library-book",
        commerce={**app_module.DEFAULT_COMMERCE, "sale_status": "published"},
    )

    with app_module.app.test_client() as client:
        book_detail = client.get("/books/protected-library-book")
        settings = client.get("/settings")
        publish = client.post("/publish", data={})

    assert book_detail.status_code == 302
    assert book_detail.headers["Location"].endswith("/login?next=/books/protected-library-book")
    assert settings.status_code == 302
    assert settings.headers["Location"].endswith("/login?next=/settings")
    assert publish.status_code == 302
    publish_location = urlsplit(publish.headers["Location"])
    assert publish_location.path == "/login"
    assert parse_qs(publish_location.query)["next"] == ["/#publish-workbench"]


def test_logged_in_publisher_still_sees_workbench_and_private_books(isolated_books):
    write_book(isolated_books, "publisher-private-library", title="나만 보는 출판 원고")

    with app_module.app.test_client() as client:
        login(client)
        response = client.get("/")

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="publish-workbench"' in html
    assert 'action="/publish"' in html
    assert "나만 보는 출판 원고" in html
    assert "/books/publisher-private-library" in html


def test_non_admin_publisher_does_not_see_admin_settings_navigation(isolated_books):
    with app_module.app.test_client() as client:
        login(client, username="reader")
        response = client.get("/")

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert ">설정</a>" not in html
    assert ">로그아웃</button>" in html


def test_expired_session_on_commerce_post_returns_to_settings_not_post_only_url(isolated_books):
    write_book(isolated_books, "expired-commerce-session")

    with app_module.app.test_client() as client:
        response = client.post(
            "/settings/books/expired-commerce-session/commerce",
            data=valid_paid_form(),
        )

    assert response.status_code == 302
    location = urlsplit(response.headers["Location"])
    assert location.path == "/login"
    assert parse_qs(location.query)["next"] == ["/settings#book-commerce"]


def test_authenticated_settings_page_cannot_be_restored_from_browser_cache(isolated_books):
    write_book(isolated_books, "no-cache-settings")

    with app_module.app.test_client() as client:
        login(client)
        response = client.get("/settings")

    assert response.status_code == 200
    assert "private" in response.headers["Cache-Control"]
    assert "no-store" in response.headers["Cache-Control"]
    assert "Cookie" in response.headers["Vary"]
