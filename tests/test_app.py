from io import BytesIO
import json
from pathlib import Path
import zipfile

import app as app_module
from PIL import Image


def test_split_paragraph_for_pages_limits_long_sentence_chunks():
    long_sentence = "가" * 760
    chunks = app_module.split_paragraph_for_pages(long_sentence, max_chars=300)

    assert len(chunks) == 3
    assert all(len(chunk) <= 300 for chunk in chunks)


def test_publish_accepts_korean_pdf_filename_and_reader(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    app_module.BOOK_DIR.mkdir()
    app_module.UPLOAD_DIR.mkdir()

    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
            sess["_csrf_token"] = "publish-test-token"

        def fake_build_book(upload_path, meta, output_root, **_kwargs):
            book_dir = output_root / "test-book"
            book_dir.mkdir(parents=True, exist_ok=True)
            source = book_dir / "source.txt"
            markdown = book_dir / "book.md"
            epub = book_dir / "book.epub"
            pdf = book_dir / "book.pdf"
            source.write_text("제 1 장\n본문입니다.", encoding="utf-8")
            markdown.write_text("# 테스트\n", encoding="utf-8")
            epub.write_bytes(b"epub")
            pdf.write_bytes(b"pdf")

            class Result:
                book_id = "test-book"
                title = meta.title
                author = meta.author
                chapter_count = 1
                source_text_path = source
                markdown_path = markdown
                epub_path = epub
                pdf_path = pdf
                created_at = "2026-06-17T16:30:00"

            return Result()

        monkeypatch.setattr(app_module, "build_book", fake_build_book)
        res = client.post(
            "/publish",
            data={
                "title": "웹 리더 테스트",
                "author": "기혜경",
                "csrf_token": "publish-test-token",
                "source": (BytesIO(b"%PDF-1.4"), "기혜경자료.PDF"),
            },
            content_type="multipart/form-data",
        )
        assert res.status_code == 302
        reader = client.get("/books/test-book/read")
        assert reader.status_code == 200
        assert "웹 리더 테스트".encode() in reader.data
        assert b"book-spread" in reader.data
        assert b"READER_PAGES" in reader.data


def test_publish_uses_korean_filename_when_title_is_blank(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    app_module.BOOK_DIR.mkdir()
    app_module.UPLOAD_DIR.mkdir()
    captured = {}

    def fake_build_book(upload_path, meta, output_root, **_kwargs):
        captured["title"] = meta.title
        book_dir = output_root / "filename-title-book"
        book_dir.mkdir(parents=True, exist_ok=True)
        source = book_dir / "source.txt"
        markdown = book_dir / "book.md"
        epub = book_dir / "book.epub"
        pdf = book_dir / "book.pdf"
        source.write_text("본문입니다.", encoding="utf-8")
        markdown.write_text("# 테스트\n", encoding="utf-8")
        epub.write_bytes(b"epub")
        pdf.write_bytes(b"pdf")

        class Result:
            book_id = "filename-title-book"
            title = meta.title
            author = meta.author
            chapter_count = 1
            source_text_path = source
            markdown_path = markdown
            epub_path = epub
            pdf_path = pdf
            created_at = "2026-06-17T16:30:00"

        return Result()

    monkeypatch.setattr(app_module, "build_book", fake_build_book)
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
            sess["_csrf_token"] = "filename-publish-token"
        res = client.post(
            "/publish",
            data={
                "title": "",
                "author": "기혜경",
                "csrf_token": "filename-publish-token",
                "source": (BytesIO(b"%PDF-1.4"), "개인정보보호 교육자료.PDF"),
            },
            content_type="multipart/form-data",
        )

    assert res.status_code == 302
    assert captured["title"] == "개인정보보호 교육자료"


def test_cover_route_backfills_existing_korean_book(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    book_dir = app_module.BOOK_DIR / "legacy-book"
    book_dir.mkdir(parents=True)
    manifest_path = book_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "book_id": "legacy-book",
                "title": "한글 표지 자동 생성",
                "author": "기혜경",
                "chapter_count": 1,
                "created_at": "2026-07-11T10:00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_create_cover(meta, output_path):
        captured["title"] = meta.title
        captured["author"] = meta.author
        output_path.write_bytes(b"\x89PNG\r\n\x1a\ncover")
        return output_path

    monkeypatch.setattr(app_module, "create_cover", fake_create_cover)
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
        response = client.get("/covers/legacy-book")

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert captured == {"title": "한글 표지 자동 생성", "author": "기혜경"}
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["cover_path"].endswith("/legacy-book/cover.png")


def test_cover_backfill_does_not_overwrite_newer_commerce_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    book_dir = app_module.BOOK_DIR / "stale-cover-book"
    book_dir.mkdir(parents=True)
    manifest_path = book_dir / "manifest.json"
    original = {
        "book_id": "stale-cover-book",
        "title": "표지와 판매설정 동시 저장",
        "author": "기혜경",
        "created_at": "2026-07-11T20:00:00",
        "commerce": {
            "sale_status": "published",
            "access": "paid",
            "price_krw": 10000,
            "sample_pages": 5,
            "consultation_benefit": app_module.DEFAULT_COMMERCE["consultation_benefit"],
        },
    }
    manifest_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    stale = app_module.read_manifest("stale-cover-book")

    newer = json.loads(manifest_path.read_text(encoding="utf-8"))
    newer["commerce"]["price_krw"] = 29000
    newer["commerce"]["sample_pages"] = 9
    manifest_path.write_text(json.dumps(newer, ensure_ascii=False), encoding="utf-8")

    def fake_create_cover(_meta, output_path):
        output_path.write_bytes(b"png")

    monkeypatch.setattr(app_module, "create_cover", fake_create_cover)
    app_module.ensure_book_cover(stale)

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["commerce"]["price_krw"] == 29000
    assert saved["commerce"]["sample_pages"] == 9
    assert saved["cover_path"].endswith("/stale-cover-book/cover.png")


def test_password_reset_cooldown_is_shared_on_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "FORGOT_STATE_DIR", tmp_path / "password-reset")
    monkeypatch.setattr(app_module, "FORGOT_COOLDOWN_SECONDS", 180)

    assert app_module.claim_forgot_request("한글사용자", now=1_000)
    assert not app_module.claim_forgot_request("한글사용자", now=1_100)
    assert app_module.claim_forgot_request("다른사용자", now=1_100)

    app_module.release_forgot_request("한글사용자")
    assert app_module.claim_forgot_request("한글사용자", now=1_101)


def test_admin_can_edit_frontend_branding(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SITE_SETTINGS_PATH", tmp_path / "site-settings.json")
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "SITE_EDITOR_USERS", {"admin", "khg334"})
    app_module.BOOK_DIR.mkdir()
    payload = {
        "name": "나의 한글 전자책 서재",
        "name_en": "MY DIGITAL EDITIONS",
        "brand_mark": "책",
        "header_title": "나의 상단 전자책 바",
        "header_subtitle": "MY TOP LIBRARY",
        "hero_kicker": "PRIVATE LIBRARY",
        "hero_title": "문장에서 책으로,",
        "hero_title_accent": "오늘 바로.",
        "hero_description": "관리자가 직접 바꾼 한글 메인 소개 문구입니다.",
        "footer_title": "나의 하단 전자책 바",
        "footer_tagline": "좋은 문장을 오래 간직합니다",
        "accent_color": "#12b8a6",
        "primary_color": "#2459d3",
    }

    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "admin"
            sess["_csrf_token"] = "test-csrf-token"
        response = client.post("/settings/appearance", data={**payload, "csrf_token": "test-csrf-token"})
        home = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings#appearance-settings")
    assert "나의 한글 전자책 서재".encode() in home.data
    assert "문장에서 책으로".encode() in home.data
    saved = json.loads(app_module.SITE_SETTINGS_PATH.read_text(encoding="utf-8"))
    assert {key: saved[key] for key in payload} == payload
    assert saved["upload_limit_mb"] == "100"
    assert saved["ai_cover_enabled"] == "1"


def test_non_admin_cannot_edit_frontend_branding(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SITE_SETTINGS_PATH", tmp_path / "site-settings.json")
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "SITE_EDITOR_USERS", {"admin"})
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "reader"
        response = client.post("/settings/appearance", data=app_module.DEFAULT_SITE_SETTINGS)

    assert response.status_code == 403
    assert not app_module.SITE_SETTINGS_PATH.exists()


def test_admin_frontend_editor_requires_csrf(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SITE_SETTINGS_PATH", tmp_path / "site-settings.json")
    monkeypatch.setattr(app_module, "ADMIN_USER", "admin")
    monkeypatch.setattr(app_module, "SITE_EDITOR_USERS", {"admin"})
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "admin"
            sess["_csrf_token"] = "expected-token"
        response = client.post("/settings/appearance", data=app_module.DEFAULT_SITE_SETTINGS)

    assert response.status_code == 400
    assert not app_module.SITE_SETTINGS_PATH.exists()


def test_editor_can_clear_optional_header_and_footer_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SITE_SETTINGS_PATH", tmp_path / "site-settings.json")
    monkeypatch.setattr(app_module, "SITE_EDITOR_USERS", {"khg334"})
    payload = {**app_module.DEFAULT_SITE_SETTINGS, "header_subtitle": "", "footer_tagline": ""}

    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
            sess["_csrf_token"] = "optional-copy-token"
        response = client.post(
            "/settings/appearance",
            data={**payload, "csrf_token": "optional-copy-token"},
        )

    assert response.status_code == 302
    saved = app_module.load_site_settings()
    assert saved["header_subtitle"] == ""
    assert saved["footer_tagline"] == ""


def test_allowed_site_editor_sees_header_footer_menu(monkeypatch):
    monkeypatch.setattr(app_module, "SITE_EDITOR_USERS", {"khg334"})
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
        response = client.get("/settings")

    assert response.status_code == 200
    assert "상단 바 설정".encode() in response.data
    assert "하단 바 설정".encode() in response.data


def test_default_allowed_email_user_can_view_and_update_all_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SITE_SETTINGS_PATH", tmp_path / "site-settings.json")

    assert "reset98@gmail.com" in app_module.ALLOWED_USERS
    assert app_module.ALLOWED_USERS <= app_module.SITE_EDITOR_USERS

    csrf = "reset98-settings-token"
    appearance = {
        **app_module.DEFAULT_SITE_SETTINGS,
        "header_title": "리셋 전자책 스튜디오",
        "footer_title": "리셋 디지털 라이브러리",
    }
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "reset98@gmail.com"
            sess["_csrf_token"] = csrf

        settings_page = client.get("/settings")
        publishing_response = client.post(
            "/settings/publishing",
            data={
                "csrf_token": csrf,
                "upload_limit_mb": "140",
                "ai_cover_enabled": "1",
                "ai_cover_model": "gpt-image-2",
                "ai_cover_quality": "medium",
            },
        )
        appearance_response = client.post(
            "/settings/appearance",
            data={**appearance, "csrf_token": csrf},
        )

    assert settings_page.status_code == 200
    assert b'action="/settings/publishing"' in settings_page.data
    assert b'action="/settings/appearance"' in settings_page.data
    assert publishing_response.status_code == 302
    assert appearance_response.status_code == 302
    saved = app_module.load_site_settings()
    assert saved["upload_limit_mb"] == "140"
    assert saved["ai_cover_enabled"] == "1"
    assert saved["header_title"] == "리셋 전자책 스튜디오"
    assert saved["footer_title"] == "리셋 디지털 라이브러리"


def test_pdf_reader_uses_page_thumbnails_and_original_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    book_dir = app_module.BOOK_DIR / "pdf-book"
    book_dir.mkdir(parents=True)
    source = book_dir / "source.txt"
    pdf = book_dir / "원본.pdf"
    source.write_text("한글 PDF 본문", encoding="utf-8")
    pdf.write_bytes(b"%PDF-1.7\nfixture")
    manifest = {
        "book_id": "pdf-book",
        "title": "원본 한글 PDF",
        "author": "기혜경",
        "chapter_count": 3,
        "created_at": "2026-07-11T12:00:00",
        "source_path": str(source),
        "pdf_path": str(pdf),
    }
    (book_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(app_module, "pdf_page_count", lambda path: 3)
    rendered = book_dir / "page-2-thumb.png"
    rendered.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    captured = {}

    def fake_render(path, cache_dir, page_number, variant):
        captured.update(page_number=page_number, variant=variant)
        return rendered

    monkeypatch.setattr(app_module, "render_pdf_page", fake_render)
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
        reader = client.get("/books/pdf-book/read")
        page = client.get("/books/pdf-book/pdf/pages/2?variant=thumb")
        original = client.get("/books/pdf-book/pdf")

        monkeypatch.setattr(app_module, "PDF_THUMBNAIL_LIMIT", 2)
        limited_reader = client.get("/books/pdf-book/read")

    assert reader.status_code == 200
    assert b'id="pdfReader"' in reader.data
    assert "3페이지로 이동".encode() in reader.data
    assert b"variant=thumb" in reader.data
    assert page.status_code == 200
    assert page.mimetype == "image/png"
    assert "private" in page.headers["Cache-Control"]
    assert captured == {"page_number": 2, "variant": "thumb"}
    assert original.status_code == 200
    assert original.mimetype == "application/pdf"
    assert "private" in original.headers["Cache-Control"]
    assert limited_reader.data.count(b'class="pdf-thumbnail-button') == 2
    assert "앞 2페이지의 썸네일".encode() in limited_reader.data
    assert b'data-page-count="3"' in limited_reader.data


def test_ai_cover_draft_is_reused_as_book_cover_epub_and_thumbnail(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(app_module, "COVER_DRAFT_DIR", tmp_path / "cover-drafts")
    monkeypatch.setattr(app_module, "SITE_SETTINGS_PATH", tmp_path / "site-settings.json")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    app_module.BOOK_DIR.mkdir()
    app_module.UPLOAD_DIR.mkdir()

    calls = []

    def fake_ai_background(meta, text, output_path, **kwargs):
        calls.append({"title": meta.title, "text": text, "model": kwargs["model"]})
        Image.new("RGB", (1024, 1536), (36, 96, 122)).save(output_path, format="PNG")
        return Path(output_path)

    monkeypatch.setattr(app_module, "generate_ai_cover_background", fake_ai_background)
    metadata = {
        "title": "바다와 기억의 지도",
        "author": "기혜경",
        "subtitle": "마음을 건너는 기록",
        "publisher": "혜경 전자책 스튜디오",
        "description": "바다를 따라 삶의 기억을 복원하는 에세이",
    }

    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
            sess["_csrf_token"] = "ai-cover-token"
        preview_response = client.post(
            "/cover-drafts",
            data={
                **metadata,
                "csrf_token": "ai-cover-token",
                "source": (BytesIO("제 1 장\n바다와 기억에 관한 원고입니다.".encode()), "바다의 기록.txt"),
            },
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )
        assert preview_response.status_code == 200
        preview_payload = preview_response.get_json()
        preview_image = client.get(preview_payload["cover_url"])
        preview_bytes = preview_image.data

        publish_response = client.post(
            "/publish",
            data={
                **metadata,
                "csrf_token": "ai-cover-token",
                "draft_token": preview_payload["draft_token"],
                "cover_token": preview_payload["cover_token"],
            },
        )
        assert publish_response.status_code == 302
        book_id = publish_response.headers["Location"].rstrip("/").split("/")[-1]
        thumbnail = client.get(f"/covers/{book_id}")
        library = client.get("/")

    assert preview_payload["mode"] == "ai"
    assert calls and calls[0]["title"] == metadata["title"]
    assert "바다와 기억" in calls[0]["text"]
    assert calls[0]["model"] == "gpt-image-2"
    with Image.open(BytesIO(preview_bytes)) as cover:
        assert cover.size == (1200, 1600)
        assert cover.info["Title"] == metadata["title"]

    manifest = json.loads((app_module.BOOK_DIR / book_id / "manifest.json").read_text(encoding="utf-8"))
    final_cover = Path(manifest["cover_path"]).read_bytes()
    assert manifest["cover_mode"] == "ai"
    assert final_cover == preview_bytes
    assert thumbnail.data == final_cover
    assert f'/covers/{book_id}'.encode() in library.data
    with zipfile.ZipFile(manifest["epub_path"]) as epub:
        assert epub.read("OEBPS/images/cover.png") == final_cover
    assert not (app_module.COVER_DRAFT_DIR / preview_payload["draft_token"]).exists()


def test_direct_publish_generates_ai_cover_once_and_embeds_same_cover(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(app_module, "COVER_DRAFT_DIR", tmp_path / "cover-drafts")
    monkeypatch.setattr(app_module, "SITE_SETTINGS_PATH", tmp_path / "site-settings.json")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    app_module.BOOK_DIR.mkdir()
    app_module.UPLOAD_DIR.mkdir()

    calls = []

    def fake_ai_background(meta, text, output_path, **kwargs):
        calls.append(
            {
                "title": meta.title,
                "text": text,
                "model": kwargs["model"],
                "quality": kwargs["quality"],
            }
        )
        Image.new("RGB", (1024, 1536), (28, 74, 112)).save(output_path, format="PNG")
        return Path(output_path)

    monkeypatch.setattr(app_module, "generate_ai_cover_background", fake_ai_background)
    metadata = {
        "title": "기억을 걷는 밤",
        "author": "기혜경",
        "subtitle": "도시와 마음의 기록",
        "publisher": "혜경 전자책 스튜디오",
        "description": "밤의 도시를 걸으며 오래된 기억을 되짚는 에세이",
    }

    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
            sess["_csrf_token"] = "direct-publish-token"
        response = client.post(
            "/publish",
            data={
                **metadata,
                "csrf_token": "direct-publish-token",
                "source": (
                    BytesIO("제 1 장\n밤의 도시와 오래된 기억을 걷는 이야기입니다.".encode()),
                    "기억의 밤.txt",
                ),
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 302
    assert len(calls) == 1
    assert calls[0]["title"] == metadata["title"]
    assert "밤의 도시" in calls[0]["text"]
    assert calls[0]["model"] == "gpt-image-2"
    assert calls[0]["quality"] == "medium"

    book_id = response.headers["Location"].rstrip("/").split("/")[-1]
    manifest = json.loads((app_module.BOOK_DIR / book_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cover_mode"] == "ai"
    final_cover = Path(manifest["cover_path"]).read_bytes()
    with zipfile.ZipFile(manifest["epub_path"]) as epub:
        assert epub.read("OEBPS/images/cover.png") == final_cover


def test_publishing_settings_change_upload_limit_and_ai_options(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SITE_SETTINGS_PATH", tmp_path / "site-settings.json")
    monkeypatch.setattr(app_module, "SITE_EDITOR_USERS", {"khg334"})

    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
            sess["_csrf_token"] = "publishing-settings-token"
        response = client.post(
            "/settings/publishing",
            data={
                "csrf_token": "publishing-settings-token",
                "upload_limit_mb": "125",
                "ai_cover_model": "gpt-image-1-mini",
                "ai_cover_quality": "low",
            },
        )
        home = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings#publishing-settings")
    saved = app_module.load_site_settings()
    assert saved["upload_limit_mb"] == "125"
    assert saved["ai_cover_enabled"] == "0"
    assert saved["ai_cover_model"] == "gpt-image-1-mini"
    assert saved["ai_cover_quality"] == "low"
    assert "최대 125MB".encode() in home.data


def test_publish_requires_csrf(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    app_module.BOOK_DIR.mkdir()
    app_module.UPLOAD_DIR.mkdir()
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
            sess["_csrf_token"] = "expected"
        response = client.post(
            "/publish",
            data={"source": (BytesIO(b"text"), "원고.txt")},
            content_type="multipart/form-data",
        )
    assert response.status_code == 400


def test_publish_upload_area_exposes_drag_and_drop_copy():
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
        response = client.get("/")

    assert response.status_code == 200
    assert b"data-upload-dropzone" in response.data
    assert "파일을 끌어 놓거나 선택하세요".encode() in response.data
    assert b"data-file-input" in response.data
    assert b'aria-busy="false"' in response.data
    assert b"data-publish-submit" in response.data
    assert b"data-publish-submit-label" in response.data
    assert b"data-publishing-progress" in response.data
    assert "원고를 업로드하고 출판본을 만들고 있습니다".encode() in response.data


def _write_ready_cover_draft(root: Path, token: str, *, username: str = "khg334") -> Path:
    draft_root = root / token
    draft_root.mkdir(parents=True)
    (draft_root / "source.txt").write_text("제 1 장\n안전한 한글 원고", encoding="utf-8")
    (draft_root / "extracted.txt").write_text("제 1 장\n안전한 한글 원고", encoding="utf-8")
    app_module.write_json_atomic(
        draft_root / "draft.json",
        {
            "username": username,
            "created_at": app_module.time.time(),
            "updated_at": app_module.time.time(),
            "original_filename": "원고.txt",
            "source_file": "source.txt",
            "generation": 1,
            "state": "ready",
        },
    )
    return draft_root


def _login_for_draft_test(client, csrf: str = "draft-guard-token") -> None:
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["username"] = "khg334"
        sess["_csrf_token"] = csrf


def test_cover_draft_returns_busy_for_user_and_draft_locks(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "COVER_DRAFT_DIR", tmp_path / "cover-drafts")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    token = "a" * 32
    _write_ready_cover_draft(app_module.COVER_DRAFT_DIR, token)

    with app_module.app.test_client() as client:
        _login_for_draft_test(client)
        with app_module.try_advisory_lock(app_module.cover_draft_user_lock_path("khg334")) as held:
            assert held is not None
            user_busy = client.post(
                "/cover-drafts",
                data={
                    "csrf_token": "draft-guard-token",
                    "source": (BytesIO(b"text"), "new.txt"),
                },
                content_type="multipart/form-data",
                headers={"Accept": "application/json"},
            )
        with app_module.try_advisory_lock(app_module.cover_draft_token_lock_path(token)) as held:
            assert held is not None
            draft_busy = client.post(
                "/cover-drafts",
                data={"csrf_token": "draft-guard-token", "draft_token": token},
                headers={"Accept": "application/json"},
            )

    assert user_busy.status_code == 429
    assert draft_busy.status_code == 409
    assert "다른 표지" in user_busy.get_json()["error"]
    assert "이미 처리" in draft_busy.get_json()["error"]


def test_concurrent_draft_publish_is_rejected_before_build(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "COVER_DRAFT_DIR", tmp_path / "cover-drafts")
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    token = "b" * 32
    draft_root = _write_ready_cover_draft(app_module.COVER_DRAFT_DIR, token)
    called = []
    monkeypatch.setattr(app_module, "build_book", lambda *_args, **_kwargs: called.append(True))

    with app_module.app.test_client() as client:
        _login_for_draft_test(client)
        with app_module.try_advisory_lock(app_module.cover_draft_token_lock_path(token)) as held:
            assert held is not None
            response = client.post(
                "/publish",
                data={"csrf_token": "draft-guard-token", "draft_token": token},
            )

    assert response.status_code == 302
    assert not called
    assert json.loads((draft_root / "draft.json").read_text(encoding="utf-8"))["state"] == "ready"


def test_failed_draft_publish_restores_ready_and_hides_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "COVER_DRAFT_DIR", tmp_path / "cover-drafts")
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    token = "c" * 32
    draft_root = _write_ready_cover_draft(app_module.COVER_DRAFT_DIR, token)
    observed_states = []

    def failing_build(*_args, **_kwargs):
        payload = json.loads((draft_root / "draft.json").read_text(encoding="utf-8"))
        observed_states.append(payload["state"])
        raise RuntimeError("내부 경로 /srv/private/원고.txt")

    monkeypatch.setattr(app_module, "build_book", failing_build)
    with app_module.app.test_client() as client:
        _login_for_draft_test(client)
        response = client.post(
            "/publish",
            data={"csrf_token": "draft-guard-token", "draft_token": token},
            follow_redirects=True,
        )

    restored = json.loads((draft_root / "draft.json").read_text(encoding="utf-8"))
    assert response.status_code == 200
    assert observed_states == ["publishing"]
    assert restored["state"] == "ready"
    assert "전자책을 생성하지 못했습니다".encode() in response.data
    assert b"/srv/private" not in response.data


def test_draft_publish_rejects_symlink_source(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "COVER_DRAFT_DIR", tmp_path / "cover-drafts")
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    token = "d" * 32
    draft_root = _write_ready_cover_draft(app_module.COVER_DRAFT_DIR, token)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("외부 비밀 원고", encoding="utf-8")
    (draft_root / "source.txt").unlink()
    (draft_root / "source.txt").symlink_to(outside)
    called = []
    monkeypatch.setattr(app_module, "build_book", lambda *_args, **_kwargs: called.append(True))

    with app_module.app.test_client() as client:
        _login_for_draft_test(client)
        response = client.post(
            "/publish",
            data={"csrf_token": "draft-guard-token", "draft_token": token},
        )

    assert response.status_code == 302
    assert not called
    assert outside.read_text(encoding="utf-8") == "외부 비밀 원고"


def test_cover_draft_count_and_storage_limits(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "COVER_DRAFT_DIR", tmp_path / "cover-drafts")
    monkeypatch.setattr(app_module, "COVER_DRAFT_MAX_PER_USER", 1)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    token = "e" * 32
    _write_ready_cover_draft(app_module.COVER_DRAFT_DIR, token)

    with app_module.app.test_client() as client:
        _login_for_draft_test(client)
        count_limited = client.post(
            "/cover-drafts",
            data={
                "csrf_token": "draft-guard-token",
                "source": (BytesIO(b"another"), "another.txt"),
            },
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )

    assert count_limited.status_code == 429

    monkeypatch.setattr(app_module, "COVER_DRAFT_MAX_PER_USER", 4)
    monkeypatch.setattr(app_module, "cover_draft_user_byte_limit", lambda _upload_limit: 1)
    monkeypatch.setattr(app_module, "COVER_DRAFT_DIR", tmp_path / "small-cover-drafts")
    with app_module.app.test_client() as client:
        _login_for_draft_test(client, csrf="storage-token")
        storage_limited = client.post(
            "/cover-drafts",
            data={
                "csrf_token": "storage-token",
                "source": (BytesIO(b"too-large-for-temporary-space"), "large.txt"),
            },
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )

    assert storage_limited.status_code == 429
    assert not list((tmp_path / "small-cover-drafts").glob("[0-9a-f]" * 32))


def test_ai_cover_hourly_quota_stops_additional_generation(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "COVER_DRAFT_DIR", tmp_path / "cover-drafts")
    monkeypatch.setattr(app_module, "SITE_SETTINGS_PATH", tmp_path / "site-settings.json")
    monkeypatch.setattr(app_module, "AI_COVER_HOURLY_QUOTA", 1)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = []

    def fake_ai_background(_meta, _text, output_path, **_kwargs):
        calls.append(True)
        Image.new("RGB", (1024, 1536), (34, 82, 104)).save(output_path, format="PNG")

    monkeypatch.setattr(app_module, "generate_ai_cover_background", fake_ai_background)
    with app_module.app.test_client() as client:
        _login_for_draft_test(client, csrf="quota-token")
        first = client.post(
            "/cover-drafts",
            data={
                "csrf_token": "quota-token",
                "title": "첫 표지",
                "source": (BytesIO("첫 번째 원고".encode()), "first.txt"),
            },
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )
        second = client.post(
            "/cover-drafts",
            data={
                "csrf_token": "quota-token",
                "title": "두 번째 표지",
                "source": (BytesIO("두 번째 원고".encode()), "second.txt"),
            },
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert len(calls) == 1
    assert "시간당 1회" in second.get_json()["error"]


def test_cover_draft_manuscript_error_does_not_expose_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "COVER_DRAFT_DIR", tmp_path / "cover-drafts")
    monkeypatch.setattr(
        app_module,
        "extract_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("/private/path/secret.pdf")),
    )
    with app_module.app.test_client() as client:
        _login_for_draft_test(client, csrf="safe-error-token")
        response = client.post(
            "/cover-drafts",
            data={
                "csrf_token": "safe-error-token",
                "source": (BytesIO(b"broken"), "broken.pdf"),
            },
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 422
    assert "/private/path" not in response.get_json()["error"]
    assert "파일 형식과 손상 여부" in response.get_json()["error"]


def _write_html_book_manifest(monkeypatch, tmp_path, book_id="html-book"):
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    book_dir = app_module.BOOK_DIR / book_id
    html_root = book_dir / "html"
    html_root.mkdir(parents=True)
    index = html_root / "index.html"
    index.write_text(
        """<!doctype html><html lang="ko"><head><meta charset="utf-8">
        <link rel="stylesheet" href="styles.css"></head><body>
        <h1>기혜경의 관상 톡</h1><main id="pages">하루의 운기를 보는 관상부위</main>
        <script src="reader.js"></script></body></html>""",
        encoding="utf-8",
    )
    (html_root / "styles.css").write_text("body{font-family:sans-serif}", encoding="utf-8")
    (html_root / "reader.js").write_text(
        "fetch('ocr_pages.json').then(r=>r.json()).then(p=>document.body.dataset.pages=p.length)",
        encoding="utf-8",
    )
    (html_root / "ocr_pages.json").write_text(
        json.dumps([{"page": 1, "title": "인당"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (html_root / "관상톡_OCR_전체텍스트.md").write_text("# 관상 톡\n한글 OCR 본문", encoding="utf-8")
    archive = book_dir / "관상톡-html.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in html_root.iterdir():
            output.write(path, path.name)
    source = book_dir / "source.txt"
    markdown = book_dir / "book.md"
    epub = book_dir / "book.epub"
    pdf = book_dir / "book.pdf"
    source.write_text("하루의 운기를 보는 관상부위", encoding="utf-8")
    markdown.write_text("# 관상 톡", encoding="utf-8")
    epub.write_bytes(b"epub")
    pdf.write_bytes(b"pdf")
    manifest = {
        "book_id": book_id,
        "title": "기혜경의 관상 톡",
        "author": "기혜경",
        "chapter_count": 91,
        "created_at": "2026-07-11T14:38:00",
        "publication_type": "html",
        "has_html": True,
        "html_path": str(index),
        "html_root": str(html_root),
        "html_entry": "index.html",
        "html_archive_path": str(archive),
        "html_content_version": "a" * 32,
        "source_path": str(source),
        "markdown_path": str(markdown),
        "epub_path": str(epub),
        "pdf_path": str(pdf),
    }
    (book_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest


def test_html_manifest_and_detail_omit_unavailable_generated_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    book_dir = app_module.BOOK_DIR / "html-without-pdf"
    html_root = book_dir / "html"
    html_root.mkdir(parents=True)
    index = html_root / "index.html"
    archive = book_dir / "관상톡.zip"
    source = book_dir / "source.txt"
    markdown = book_dir / "관상톡.md"
    cover = book_dir / "cover.png"
    epub = book_dir / "관상톡.epub"
    index.write_text("<h1>기혜경의 관상 톡</h1>", encoding="utf-8")
    archive.write_bytes(b"zip")
    source.write_text("하루의 운기", encoding="utf-8")
    markdown.write_text("# 하루의 운기", encoding="utf-8")
    cover.write_bytes(b"png")
    epub.write_bytes(b"epub")

    class Result:
        book_id = "html-without-pdf"
        title = "기혜경의 관상 톡"
        author = "기혜경"
        chapter_count = 91
        source_text_path = source
        markdown_path = markdown
        cover_path = cover
        epub_path = None
        pdf_path = None
        created_at = "2026-07-11T16:11:20"
        cover_mode = "ai"
        publication_type = "html"
        html_path = index
        html_archive_path = archive

    app_module.write_manifest(Result())
    manifest = json.loads((book_dir / "manifest.json").read_text(encoding="utf-8"))

    assert "pdf_path" not in manifest
    assert "epub_path" not in manifest

    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
        detail = client.get("/books/html-without-pdf")
        missing_epub = client.get("/download/html-without-pdf/epub")
        missing_pdf = client.get("/download/html-without-pdf/pdf")

    assert detail.status_code == 200
    assert b'class="asset-card pdf"' not in detail.data
    assert b'class="asset-card epub"' not in detail.data
    assert b'/download/html-without-pdf/pdf' not in detail.data
    assert b'/download/html-without-pdf/epub' not in detail.data
    assert "HTML · ZIP".encode() in detail.data
    assert missing_epub.status_code == 404
    assert missing_pdf.status_code == 404


def _html_capability(manifest: dict, *, now: int | None = None) -> tuple[int, str, str]:
    issued_at = int(app_module.time.time()) if now is None else now
    expires_at = issued_at + app_module.HTML_CONTENT_TOKEN_TTL_SECONDS
    content_version = app_module.html_content_version(manifest)
    assert content_version is not None
    token = app_module.html_content_token(manifest["book_id"], expires_at, content_version)
    route = f"/html-content/{manifest['book_id']}/{expires_at}/{token}"
    return expires_at, token, route


def test_html_reader_uses_separate_origin_and_strict_iframe_sandbox(monkeypatch, tmp_path):
    manifest = _write_html_book_manifest(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "HTML_CONTENT_ORIGIN", "https://html.epub.xsw.kr")

    with app_module.app.test_client() as client:
        unauthenticated = client.get(f"/books/{manifest['book_id']}/html")
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
        preferred = client.get(f"/books/{manifest['book_id']}/read")
        wrapper = client.get(f"/books/{manifest['book_id']}/html")

    assert unauthenticated.status_code == 302
    assert "/login" in unauthenticated.headers["Location"]
    assert preferred.status_code == 302
    assert preferred.headers["Location"].endswith(f"/books/{manifest['book_id']}/html")
    assert wrapper.status_code == 200
    assert b'sandbox="allow-scripts"' in wrapper.data
    assert b"allow-same-origin" not in wrapper.data
    assert b"allow-forms" not in wrapper.data
    assert b"allow-popups" not in wrapper.data
    assert b"allow-top-navigation" not in wrapper.data
    assert b"allow-downloads" not in wrapper.data
    assert b"https://html.epub.xsw.kr/html-content/" in wrapper.data
    assert "기혜경의 관상 톡".encode() in wrapper.data
    assert "private" in wrapper.headers["Cache-Control"]
    assert "no-store" in wrapper.headers["Cache-Control"]
    assert wrapper.headers["Referrer-Policy"] == "no-referrer"
    assert wrapper.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"


def test_html_content_capability_host_and_security_headers(monkeypatch, tmp_path):
    manifest = _write_html_book_manifest(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "HTML_CONTENT_ORIGIN", "https://html.epub.xsw.kr")
    _expires_at, _token, route = _html_capability(manifest)
    base = "https://html.epub.xsw.kr"
    path = f"{route}/index.html"

    with app_module.app.test_client() as client:
        # content 호스트는 세션 쿠키 없이 capability만으로 읽을 수 있다.
        response = client.get(path, base_url=base)
        json_asset = client.get(
            f"{route}/ocr_pages.json",
            base_url=base,
        )

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert "기혜경의 관상 톡".encode() in response.data
    csp = response.headers["Content-Security-Policy"]
    assert "sandbox allow-scripts" in csp
    assert "frame-ancestors https://epub.xsw.kr" in csp
    assert "form-action 'none'" in csp
    assert "object-src 'none'" in csp
    assert "allow-same-origin" not in csp
    assert response.headers["Access-Control-Allow-Origin"] == "null"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
    assert "no-store" in response.headers["Cache-Control"]
    assert "X-Frame-Options" not in response.headers
    assert json_asset.status_code == 200
    assert json_asset.mimetype == "application/json"
    assert json_asset.get_json()[0]["title"] == "인당"


def test_html_content_transcodes_cp949_html_and_json_to_utf8(monkeypatch, tmp_path):
    manifest = _write_html_book_manifest(monkeypatch, tmp_path, book_id="legacy-korean-html")
    html_root = Path(manifest["html_root"])
    (html_root / "index.html").write_bytes(
        '<!doctype html><html><head><meta charset="euc-kr"></head><body>하루의 운기와 인당</body></html>'.encode(
            "cp949"
        )
    )
    (html_root / "ocr_pages.json").write_bytes(
        json.dumps([{"page": 1, "title": "하루의 운기"}], ensure_ascii=False).encode("cp949")
    )
    _expires_at, _token, route = _html_capability(manifest)

    with app_module.app.test_client() as client:
        html_response = client.get(f"{route}/index.html", base_url="https://html.epub.xsw.kr")
        json_response = client.get(f"{route}/ocr_pages.json", base_url="https://html.epub.xsw.kr")

    assert html_response.status_code == 200
    assert html_response.content_type == "text/html; charset=utf-8"
    assert "하루의 운기와 인당" in html_response.get_data(as_text=True)
    assert json_response.status_code == 200
    assert json_response.content_type == "application/json; charset=utf-8"
    assert json_response.get_json()[0]["title"] == "하루의 운기"


def test_html_content_rejects_wrong_host_token_paths_and_book_root_files(monkeypatch, tmp_path):
    manifest = _write_html_book_manifest(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "HTML_CONTENT_ORIGIN", "https://html.epub.xsw.kr")
    expires_at, _token, route = _html_capability(manifest)

    with app_module.app.test_client() as client:
        wrong_host = client.get(f"{route}/index.html", base_url="https://epub.xsw.kr")
        wrong_token = client.get(
            f"/html-content/{manifest['book_id']}/{expires_at}/{'0' * 64}/index.html",
            base_url="https://html.epub.xsw.kr",
        )
        backslash = client.get(
            f"{route}/images%5c..%5cmanifest.json",
            base_url="https://html.epub.xsw.kr",
        )
        internal_manifest = client.get(
            f"{route}/manifest.json",
            base_url="https://html.epub.xsw.kr",
        )
        archive = client.get(
            f"{route}/../관상톡-html.zip",
            base_url="https://html.epub.xsw.kr",
        )

    assert wrong_host.status_code == 404
    assert wrong_token.status_code == 404
    assert backslash.status_code == 404
    assert internal_manifest.status_code == 404
    assert archive.status_code == 404
    assert wrong_token.headers["Access-Control-Allow-Origin"] == "null"


def test_html_content_capability_expires_is_bounded_and_is_version_bound(monkeypatch, tmp_path):
    manifest = _write_html_book_manifest(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "HTML_CONTENT_ORIGIN", "https://html.epub.xsw.kr")
    now = 2_000_000_000
    version = app_module.html_content_version(manifest)
    assert version is not None

    expired_at = now - 1
    expired_token = app_module.html_content_token(manifest["book_id"], expired_at, version)
    too_far_at = now + app_module.HTML_CONTENT_TOKEN_MAX_TTL_SECONDS + 1
    too_far_token = app_module.html_content_token(manifest["book_id"], too_far_at, version)
    valid_at = now + app_module.HTML_CONTENT_TOKEN_TTL_SECONDS
    valid_token = app_module.html_content_token(manifest["book_id"], valid_at, version)

    assert not app_module.valid_html_content_token(
        manifest["book_id"], expired_at, expired_token, manifest, now=now
    )
    assert not app_module.valid_html_content_token(
        manifest["book_id"], too_far_at, too_far_token, manifest, now=now
    )
    assert app_module.valid_html_content_token(
        manifest["book_id"], valid_at, valid_token, manifest, now=now
    )

    manifest["html_content_version"] = "b" * 32
    (Path(manifest["html_root"]).parent / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    assert not app_module.valid_html_content_token(
        manifest["book_id"], valid_at, valid_token, manifest, now=now
    )


def test_html_content_origin_must_be_a_distinct_host(monkeypatch, tmp_path):
    manifest = _write_html_book_manifest(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "PUBLIC_ORIGIN", "https://epub.xsw.kr")
    monkeypatch.setattr(app_module, "HTML_CONTENT_ORIGIN", "https://epub.xsw.kr:8443")
    _expires_at, _token, route = _html_capability(manifest)

    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
        wrapper = client.get(f"/books/{manifest['book_id']}/html")
        content = client.get(f"{route}/index.html", base_url="https://epub.xsw.kr:8443")

    assert not app_module.html_content_origin_is_isolated()
    assert wrapper.status_code == 503
    assert content.status_code == 404


def test_html_capability_access_log_environment_is_redacted(monkeypatch, tmp_path):
    manifest = _write_html_book_manifest(monkeypatch, tmp_path)
    _expires_at, token, route = _html_capability(manifest)

    with app_module.app.test_request_context(f"{route}/index.html"):
        app_module.request.url_rule = next(
            rule for rule in app_module.app.url_map.iter_rules() if rule.endpoint == "html_book_asset"
        )
        environment = app_module.request.environ
        environment["RAW_URI"] = f"{route}/index.html"
        environment["REQUEST_URI"] = f"{route}/index.html"
        response = app_module.redact_html_capability_from_access_log(app_module.app.response_class())

        assert response.status_code == 200
        assert token not in environment["RAW_URI"]
        assert environment["RAW_URI"] == "/html-content/[capability-redacted]"
        assert environment["PATH_INFO"] == "/html-content/[capability-redacted]"
        assert environment["QUERY_STRING"] == ""


def test_manifest_book_id_must_match_requested_directory(monkeypatch, tmp_path):
    manifest = _write_html_book_manifest(monkeypatch, tmp_path, book_id="requested-book")
    manifest["book_id"] = "different-book"
    manifest_path = app_module.BOOK_DIR / "requested-book" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    assert app_module.read_manifest("requested-book") is None

    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
        detail = client.get("/books/requested-book")
        archive = client.get("/download/requested-book/html")

    assert detail.status_code == 404
    assert archive.status_code == 404


def test_html_archive_download_is_explicit_and_authenticated(monkeypatch, tmp_path):
    manifest = _write_html_book_manifest(monkeypatch, tmp_path)
    expected = Path(manifest["html_archive_path"]).read_bytes()

    with app_module.app.test_client() as client:
        anonymous = client.get(f"/download/{manifest['book_id']}/html")
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
        archive = client.get(f"/download/{manifest['book_id']}/html")
        unknown = client.get(f"/download/{manifest['book_id']}/html_path")

    assert anonymous.status_code == 302
    assert archive.status_code == 200
    assert archive.data == expected
    assert archive.mimetype == "application/zip"
    assert "attachment" in archive.headers["Content-Disposition"]
    assert unknown.status_code == 404


def test_logout_is_post_only_and_csrf_protected():
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
            sess["_csrf_token"] = "logout-token"
        get_response = client.get("/logout")
        invalid = client.post("/logout", data={"csrf_token": "wrong"})
        with client.session_transaction() as sess:
            assert sess.get("authenticated") is True
        valid = client.post("/logout", data={"csrf_token": "logout-token"})
        with client.session_transaction() as sess:
            authenticated = sess.get("authenticated")

    assert get_response.status_code == 405
    assert invalid.status_code == 400
    assert valid.status_code == 302
    assert valid.headers["Location"].endswith("/login")
    assert authenticated is None


def test_login_redirect_accepts_only_safe_local_paths(monkeypatch):
    monkeypatch.setattr(app_module, "verify_user", lambda username, password: True)

    with app_module.app.test_client() as client:
        external = client.post(
            "/login?next=https://attacker.example/phishing",
            data={"username": "khg334", "password": "correct"},
        )
        scheme_relative = client.post(
            "/login?next=%2F%2Fattacker.example/phishing",
            data={"username": "khg334", "password": "correct"},
        )
        encoded_backslash = client.post(
            "/login?next=%2F%255c%255cattacker.example/phishing",
            data={"username": "khg334", "password": "correct"},
        )
        local = client.post(
            "/login?next=%2Fbooks%2Fsafe-book%3Fpage%3D2",
            data={"username": "khg334", "password": "correct"},
        )

    assert external.headers["Location"].endswith("/")
    assert scheme_relative.headers["Location"].endswith("/")
    assert encoded_backslash.headers["Location"].endswith("/")
    assert local.headers["Location"].endswith("/books/safe-book?page=2")


def test_change_password_requires_csrf(monkeypatch):
    changed_passwords = []
    monkeypatch.setattr(app_module, "verify_user", lambda username, password: True)
    monkeypatch.setattr(
        app_module,
        "set_user_password",
        lambda username, password: changed_passwords.append((username, password)) or True,
    )
    payload = {
        "current_password": "current-password",
        "new_password": "new-password",
        "confirm_password": "new-password",
    }

    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
            sess["_csrf_token"] = "change-password-token"
        missing = client.post("/change-password", data=payload)
        wrong = client.post(
            "/change-password",
            data={**payload, "csrf_token": "wrong-token"},
        )
        valid = client.post(
            "/change-password",
            data={**payload, "csrf_token": "change-password-token"},
        )

    assert missing.status_code == 400
    assert wrong.status_code == 400
    assert valid.status_code == 302
    assert valid.headers["Location"].endswith("/settings")
    assert changed_passwords == [("khg334", "new-password")]


def test_html_zip_validation_message_is_returned_for_cover_draft(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "COVER_DRAFT_DIR", tmp_path / "cover-drafts")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    broken_archive = BytesIO()
    with zipfile.ZipFile(broken_archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("readme.txt", "index가 없는 HTML 책")
    broken_archive.seek(0)

    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"
            sess["_csrf_token"] = "html-error-token"
        response = client.post(
            "/cover-drafts",
            data={
                "csrf_token": "html-error-token",
                "source": (broken_archive, "관상톡.zip"),
            },
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 422
    assert "index.html" in response.get_json()["error"]
