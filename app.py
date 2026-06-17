from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename

from ebook_pipeline import BookMeta, build_book, safe_filename, split_chapters


ROOT = Path(__file__).resolve().parent
YOUTUBE_SHORTS_ROOT = Path(os.getenv("YOUTUBE_SHORTS_ROOT", "/home/reset980/youtube_shorts"))
load_dotenv(ROOT / ".env")
load_dotenv(YOUTUBE_SHORTS_ROOT / ".env")

APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
UPLOAD_DIR = ROOT / "workspace" / "uploads"
BOOK_DIR = ROOT / "workspace" / "books"
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
ALLOWED_USERS = {
    value.strip()
    for value in os.getenv("EPUBIA_ALLOWED_USERS", "khg334,khg334@hanmail.net,reset98@gmail.com,admin").split(",")
    if value.strip()
}
ADMIN_USER = os.getenv("YOUTUBE_SHORTS_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("YOUTUBE_SHORTS_ADMIN_PASSWORD", "")
USERS_PATH = YOUTUBE_SHORTS_ROOT / "data" / "users.json"


app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("EPUBIA_SECRET_KEY") or os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


def read_users() -> dict:
    try:
        payload = json.loads(USERS_PATH.read_text(encoding="utf-8"))
        users = payload.get("users", {})
        return users if isinstance(users, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        app.logger.warning("사용자 파일 읽기 실패: %s", exc)
        return {}


def password_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()


def verify_user(username: str, password: str) -> bool:
    if username not in ALLOWED_USERS:
        return False
    if ADMIN_PASSWORD and username == ADMIN_USER and hmac.compare_digest(password, ADMIN_PASSWORD):
        return True
    user = read_users().get(username)
    if not user or user.get("status") != "approved":
        return False
    salt = user.get("salt", "")
    digest = user.get("password_hash", "")
    if not salt or not digest:
        return False
    return hmac.compare_digest(password_hash(password, salt), digest)


def login_required():
    if not session.get("authenticated"):
        return redirect(url_for("login", next=request.path))
    return None


@app.before_request
def protect_pages():
    if request.endpoint in {"login", "static", "health"}:
        return None
    return login_required()


@app.context_processor
def inject_globals():
    return {
        "app_version": APP_VERSION,
        "current_user": session.get("username", ""),
    }


@app.get("/health")
def health():
    return {"status": "ok", "version": APP_VERSION}


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if verify_user(username, password):
            session.clear()
            session["authenticated"] = True
            session["username"] = username
            return redirect(request.args.get("next") or url_for("index"))
        error = "아이디 또는 비밀번호가 올바르지 않거나 전자책 권한이 없습니다."
    return render_template("login.html", error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
def index():
    books = list_books()
    return render_template("index.html", books=books)


@app.post("/publish")
def publish():
    source = request.files.get("source")
    if not source or not source.filename:
        flash("업로드할 PDF, TXT, Markdown 파일을 선택해주세요.", "error")
        return redirect(url_for("index"))

    original_filename = source.filename or ""
    ext = Path(original_filename).suffix.lower()
    if ext not in {".pdf", ".txt", ".md", ".markdown"}:
        flash("PDF, TXT, Markdown 파일만 업로드할 수 있습니다.", "error")
        return redirect(url_for("index"))

    safe_stem = secure_filename(Path(original_filename).stem) or safe_filename(Path(original_filename).stem)
    upload_name = f"{secrets.token_hex(8)}-{safe_stem or 'source'}{ext}"
    upload_path = UPLOAD_DIR / upload_name
    source.save(upload_path)

    meta = BookMeta(
        title=request.form.get("title", "").strip(),
        subtitle=request.form.get("subtitle", "").strip(),
        author=request.form.get("author", "").strip() or "기혜경",
        publisher=request.form.get("publisher", "").strip() or "혜경 전자책 스튜디오",
        description=request.form.get("description", "").strip(),
    )
    try:
        result = build_book(upload_path, meta, BOOK_DIR)
    except Exception as exc:
        upload_path.unlink(missing_ok=True)
        flash(f"전자책 생성 실패: {exc}", "error")
        return redirect(url_for("index"))

    write_manifest(result)
    flash("전자책이 생성되었습니다. EPUB/PDF/Markdown을 내려받을 수 있습니다.", "success")
    return redirect(url_for("book_detail", book_id=result.book_id))


@app.get("/books/<book_id>")
def book_detail(book_id: str):
    manifest = read_manifest(book_id)
    if not manifest:
        abort(404)
    return render_template("book.html", book=manifest)


@app.get("/books/<book_id>/read")
def read_book(book_id: str):
    manifest = read_manifest(book_id)
    if not manifest:
        abort(404)
    source_path = Path(manifest["source_path"])
    if not source_path.exists() or BOOK_DIR not in source_path.resolve().parents:
        abort(404)
    text = source_path.read_text(encoding="utf-8")
    chapters = split_chapters(text)
    pages, chapter_starts = build_reader_pages(manifest, chapters)
    return render_template("reader.html", book=manifest, chapters=chapters, pages=pages, chapter_starts=chapter_starts)


@app.get("/download/<book_id>/<kind>")
def download(book_id: str, kind: str):
    manifest = read_manifest(book_id)
    if not manifest or kind not in {"epub", "pdf", "markdown", "source"}:
        abort(404)
    path = Path(manifest[f"{kind}_path"])
    if not path.exists() or BOOK_DIR not in path.resolve().parents:
        abort(404)
    return send_file(path, as_attachment=True)


@app.get("/help")
def help_page():
    return render_template("help.html")


@app.get("/settings")
def settings():
    return render_template(
        "settings.html",
        allowed_users=", ".join(sorted(ALLOWED_USERS)),
        upload_mb=MAX_UPLOAD_BYTES // 1024 // 1024,
        book_dir=str(BOOK_DIR),
        legacy_repo="https://github.com/hojel/epubia",
    )


def manifest_path(book_id: str) -> Path:
    return BOOK_DIR / book_id / "manifest.json"


def write_manifest(result) -> None:
    payload = {
        "book_id": result.book_id,
        "title": result.title,
        "author": result.author,
        "chapter_count": result.chapter_count,
        "created_at": result.created_at,
        "source_path": str(result.source_text_path),
        "markdown_path": str(result.markdown_path),
        "epub_path": str(result.epub_path),
        "pdf_path": str(result.pdf_path),
    }
    manifest_path(result.book_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_manifest(book_id: str) -> dict | None:
    if not book_id or "/" in book_id or ".." in book_id:
        return None
    path = manifest_path(book_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_books() -> list[dict]:
    books: list[dict] = []
    for path in sorted(BOOK_DIR.glob("*/manifest.json"), reverse=True):
        try:
            books.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return books[:30]


def split_paragraph_for_pages(paragraph: str, max_chars: int = 520) -> list[str]:
    paragraph = " ".join(paragraph.split())
    if len(paragraph) <= max_chars:
        return [paragraph]
    sentences = [part.strip() for part in paragraph.replace("다. ", "다.\n").replace("요. ", "요.\n").splitlines()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if current and len(current) + len(sentence) > max_chars:
            chunks.append(current.strip())
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current.strip())
    if not chunks:
        chunks = [paragraph[i:i + max_chars] for i in range(0, len(paragraph), max_chars)]
    return chunks


def build_reader_pages(book: dict, chapters) -> tuple[list[dict], list[dict]]:
    pages = [
        {
            "kind": "cover",
            "title": book["title"],
            "author": book["author"],
            "meta": f"{book.get('chapter_count', 0)}장 · {book.get('created_at', '')}",
            "paragraphs": [],
            "chapter": 0,
        }
    ]
    chapter_starts: list[dict] = []
    target_chars = 860
    for chapter_index, chapter in enumerate(chapters, start=1):
        chapter_starts.append({"index": chapter_index, "title": chapter.title, "page": len(pages)})
        page_paragraphs: list[str] = []
        page_chars = 0
        first_page = True
        paragraphs = []
        for raw in chapter.body.split("\n\n"):
            raw = raw.strip()
            if raw:
                paragraphs.extend(split_paragraph_for_pages(raw))
        for paragraph in paragraphs:
            if page_paragraphs and page_chars + len(paragraph) > target_chars:
                pages.append(
                    {
                        "kind": "chapter",
                        "title": chapter.title if first_page else "",
                        "runningTitle": chapter.title,
                        "paragraphs": page_paragraphs,
                        "chapter": chapter_index,
                    }
                )
                first_page = False
                page_paragraphs = []
                page_chars = 0
            page_paragraphs.append(paragraph)
            page_chars += len(paragraph)
        if page_paragraphs or first_page:
            pages.append(
                {
                    "kind": "chapter",
                    "title": chapter.title if first_page else "",
                    "runningTitle": chapter.title,
                    "paragraphs": page_paragraphs,
                    "chapter": chapter_index,
                }
            )
    return pages, chapter_starts


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5010"))
    app.run(host="0.0.0.0", port=port, debug=False)
