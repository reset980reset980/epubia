from io import BytesIO

import app as app_module


def test_publish_accepts_korean_pdf_filename_and_reader(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BOOK_DIR", tmp_path / "books")
    monkeypatch.setattr(app_module, "UPLOAD_DIR", tmp_path / "uploads")
    app_module.BOOK_DIR.mkdir()
    app_module.UPLOAD_DIR.mkdir()

    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["username"] = "khg334"

        def fake_build_book(upload_path, meta, output_root):
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
