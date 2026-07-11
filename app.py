from __future__ import annotations

import fcntl
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import smtplib
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from ai_cover import AICoverError, generate_ai_cover_background
from html_book import HTMLBookError
from ebook_pipeline import (
    BookMeta,
    build_book,
    create_cover,
    display_title_from_filename,
    extract_text,
    safe_filename,
    split_chapters,
)
from pdf_rendering import PDFRenderingError, pdf_page_count, render_pdf_page


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# 프로젝트 .env에서 외부 연동 경로를 재정의할 수 있도록, 경로 계산은
# load_dotenv() 뒤에 수행한다.
YOUTUBE_SHORTS_ROOT = Path(os.getenv("YOUTUBE_SHORTS_ROOT", "/home/reset980/youtube_shorts"))
JARVIS_ROOT = Path(os.getenv("JARVIS_BOT_ROOT", "/home/reset980/Jarvis_bot"))
load_dotenv(YOUTUBE_SHORTS_ROOT / ".env")
load_dotenv(JARVIS_ROOT / ".env")  # NAVER_MAIL_USER / NAVER_MAIL_PASS (메일 발송용)

APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
UPLOAD_DIR = ROOT / "workspace" / "uploads"
BOOK_DIR = ROOT / "workspace" / "books"
SITE_SETTINGS_PATH = ROOT / "workspace" / "site-settings.json"
COVER_DRAFT_DIR = ROOT / "workspace" / "tmp" / "cover-drafts"
DEFAULT_UPLOAD_MB = 100
MIN_UPLOAD_MB = 10
MAX_CONFIG_UPLOAD_MB = 500
MAX_UPLOAD_BYTES = DEFAULT_UPLOAD_MB * 1024 * 1024
PUBLIC_ORIGIN = os.getenv("EPUBIA_PUBLIC_ORIGIN", "https://epub.xsw.kr").rstrip("/")
HTML_CONTENT_ORIGIN = os.getenv("EPUBIA_HTML_CONTENT_ORIGIN", "https://html.epub.xsw.kr").rstrip("/")
SAJU_LIBRARY_ORIGIN = os.getenv("SAJU_LIBRARY_ORIGIN", "https://saju.xsw.kr").rstrip("/")
MAX_PUBLIC_OCR_JSON_BYTES = 48 * 1024 * 1024
MAX_PUBLIC_READER_PAGES = 2_000
PUBLIC_SCAN_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
EBOOK_ACCESS_SECRET_FILE = Path(
    os.getenv("EBOOK_ACCESS_SECRET_FILE", "/home/reset980/.config/epubia-commerce/access-secret")
)
EBOOK_ACCESS_NONCE_DIR = ROOT / "workspace" / "tmp" / "ebook-access-nonces"
EBOOK_ACCESS_TICKET_MAX_TTL_SECONDS = 120
EBOOK_READER_GRANT_TTL_SECONDS = 4 * 60 * 60
EBOOK_READER_COOKIE = "epubia_reader_grant"
HTML_CONTENT_TOKEN_DEFAULT_TTL_SECONDS = 12 * 60 * 60
HTML_CONTENT_TOKEN_MIN_TTL_SECONDS = 5 * 60
HTML_CONTENT_TOKEN_MAX_TTL_SECONDS = 24 * 60 * 60
try:
    HTML_CONTENT_TOKEN_TTL_SECONDS = int(
        os.getenv(
            "EPUBIA_HTML_CONTENT_TOKEN_TTL_SECONDS",
            str(HTML_CONTENT_TOKEN_DEFAULT_TTL_SECONDS),
        )
    )
except ValueError:
    HTML_CONTENT_TOKEN_TTL_SECONDS = HTML_CONTENT_TOKEN_DEFAULT_TTL_SECONDS
HTML_CONTENT_TOKEN_TTL_SECONDS = max(
    HTML_CONTENT_TOKEN_MIN_TTL_SECONDS,
    min(HTML_CONTENT_TOKEN_TTL_SECONDS, HTML_CONTENT_TOKEN_MAX_TTL_SECONDS),
)
COVER_DRAFT_TTL_SECONDS = 6 * 60 * 60
try:
    COVER_DRAFT_MAX_PER_USER = int(os.getenv("EPUBIA_COVER_DRAFT_MAX_PER_USER", "4"))
except ValueError:
    COVER_DRAFT_MAX_PER_USER = 4
COVER_DRAFT_MAX_PER_USER = max(1, min(COVER_DRAFT_MAX_PER_USER, 20))
try:
    AI_COVER_HOURLY_QUOTA = int(os.getenv("EPUBIA_AI_COVER_HOURLY_QUOTA", "20"))
except ValueError:
    AI_COVER_HOURLY_QUOTA = 20
AI_COVER_HOURLY_QUOTA = max(1, min(AI_COVER_HOURLY_QUOTA, 100))
COVER_DRAFT_STORAGE_FLOOR_BYTES = 512 * 1024 * 1024
AI_COVER_QUOTA_WINDOW_SECONDS = 60 * 60
AI_COVER_MODELS = ("gpt-image-2", "gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini")
AI_COVER_QUALITIES = ("low", "medium", "high")
HTML_CONTENT_EXTENSIONS = {
    ".css",
    ".gif",
    ".htm",
    ".html",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".otf",
    ".png",
    ".txt",
    ".ttf",
    ".webp",
    ".woff",
    ".woff2",
}
HTML_CONTENT_MIMETYPES = {
    ".css": "text/css; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}
HTML_CONTENT_TEXT_EXTENSIONS = frozenset(HTML_CONTENT_MIMETYPES)
HTML_CONTENT_TEXT_MAX_BYTES = 10 * 1024 * 1024
try:
    PDF_THUMBNAIL_LIMIT = int(os.getenv("EPUBIA_PDF_THUMBNAIL_LIMIT", "1000"))
except ValueError:
    PDF_THUMBNAIL_LIMIT = 1000
PDF_THUMBNAIL_LIMIT = max(100, min(PDF_THUMBNAIL_LIMIT, 5000))
ALLOWED_USERS = {
    value.strip()
    for value in os.getenv("EPUBIA_ALLOWED_USERS", "khg334,khg334@hanmail.net,reset98@gmail.com,admin").split(",")
    if value.strip()
}
ADMIN_USER = os.getenv("YOUTUBE_SHORTS_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("YOUTUBE_SHORTS_ADMIN_PASSWORD", "")
USERS_PATH = YOUTUBE_SHORTS_ROOT / "data" / "users.json"
_site_editor_env = os.getenv("EPUBIA_SITE_EDITORS", "")
SITE_EDITOR_USERS = (
    {value.strip() for value in _site_editor_env.split(",") if value.strip()}
    if _site_editor_env.strip()
    # 별도 편집자 목록을 설정하지 않은 단일 운영 환경에서는 로그인 허용
    # 계정 모두가 프론트/출판 설정을 관리할 수 있게 한다. 운영에서 권한을
    # 좁히려면 EPUBIA_SITE_EDITORS를 명시하면 된다.
    else set(ALLOWED_USERS) | {ADMIN_USER}
)

MIN_PASSWORD_LENGTH = 8
# 비밀번호 찾기 메일 발송 대상. 아이디에 '@'가 있으면 그대로 이메일로 사용하고,
# 그 외 아이디는 아래 매핑으로 등록 이메일을 찾는다. (공유 users.json 스키마는 건드리지 않음)
EMAIL_MAP = {}
for pair in os.getenv("EPUBIA_EMAIL_MAP", "khg334:khg334@hanmail.net").split(","):
    if ":" in pair:
        key, _, val = pair.partition(":")
        if key.strip() and val.strip():
            EMAIL_MAP[key.strip()] = val.strip()

SMTP_HOST = os.getenv("NAVER_SMTP_HOST", "smtp.naver.com")
SMTP_PORT = int(os.getenv("NAVER_SMTP_PORT", "587"))
SMTP_USER = os.getenv("NAVER_MAIL_USER", "")
SMTP_PASS = os.getenv("NAVER_MAIL_PASS", "")

# /forgot 남용 방지: 동일 아이디에 대한 재설정 요청 쿨다운(초)
FORGOT_COOLDOWN_SECONDS = int(os.getenv("EPUBIA_FORGOT_COOLDOWN", "180"))
FORGOT_STATE_DIR = ROOT / "workspace" / "tmp" / "password-reset"

DEFAULT_SITE_SETTINGS = {
    "name": "혜경 전자책 스튜디오",
    "name_en": "HYE GYEONG EDITIONS",
    "brand_mark": "冊",
    "header_title": "혜경 전자책 스튜디오",
    "header_subtitle": "HYE GYEONG EDITIONS",
    "hero_kicker": "PRIVATE DIGITAL PRESS",
    "hero_title": "원고에서 서가까지,",
    "hero_title_accent": "한 번에.",
    "hero_description": "PDF를 올리면 본문을 정리하고 장을 나눠, 읽기 좋은 EPUB과 PDF를 바로 만듭니다. 당신의 문장이 머물 완성된 자리를 준비하세요.",
    "footer_title": "혜경 전자책 스튜디오",
    "footer_tagline": "원고를 읽히는 책으로",
    "accent_color": "#49d3c4",
    "primary_color": "#356de8",
    "upload_limit_mb": str(DEFAULT_UPLOAD_MB),
    "ai_cover_enabled": "1",
    "ai_cover_model": "gpt-image-2",
    "ai_cover_quality": "medium",
}
SITE_SETTING_LIMITS = {
    "name": 40,
    "name_en": 60,
    "brand_mark": 2,
    "header_title": 40,
    "header_subtitle": 60,
    "hero_kicker": 60,
    "hero_title": 60,
    "hero_title_accent": 60,
    "hero_description": 240,
    "footer_title": 40,
    "footer_tagline": 80,
}
OPTIONAL_SITE_SETTINGS = {"header_subtitle", "footer_tagline"}

# 책 파일과 판매 정책은 같은 manifest.json에 보관하되, 기존 출판본에는
# commerce 키가 없으므로 절대로 자동 공개하지 않는다. 읽을 때만 아래의
# 안전한 기본값으로 정규화하고, 관리자가 저장한 순간부터 명시적으로 기록한다.
DEFAULT_COMMERCE = {
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
SALE_STATUSES = frozenset({"private", "published", "archived"})
BOOK_ACCESS_TYPES = frozenset({"free", "paid"})
CONSULTATION_BENEFIT_TYPES = frozenset({"none", "percent", "fixed"})
MAX_BOOK_PRICE_KRW = 100_000_000
MAX_SAMPLE_PAGES = 500
MAX_BENEFIT_VALUE_KRW = 10_000_000
MAX_BENEFIT_USES = 100
MAX_BENEFIT_VALID_DAYS = 3650


class AICoverQuotaExceeded(RuntimeError):
    pass


def session_secret() -> str:
    configured = os.getenv("EPUBIA_SECRET_KEY") or os.getenv("FLASK_SECRET_KEY")
    if configured:
        return configured

    # 개발/단일 서버에서도 Gunicorn worker마다 서로 다른 키가 생기지 않도록
    # git에서 제외되는 로컬 파일에 한 번만 생성한다. 운영에서는 .env 설정 권장.
    secret_path = ROOT / "workspace" / "tmp" / ".session-secret"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = secret_path.read_text(encoding="utf-8").strip()
        if value:
            return value
    except FileNotFoundError:
        pass

    value = secrets.token_hex(32)
    try:
        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
        return value
    except FileExistsError:
        return secret_path.read_text(encoding="utf-8").strip()


def ebook_access_secret() -> bytes | None:
    """Load the key shared with 운명서재 without committing it to either app."""

    configured = os.getenv("EBOOK_ACCESS_SECRET", "").strip()
    if configured:
        encoded = configured.encode("utf-8")
        return encoded if len(encoded) >= 32 else None

    secret_path = Path(os.getenv("EBOOK_ACCESS_SECRET_FILE", str(EBOOK_ACCESS_SECRET_FILE)))
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(secret_path.parent, 0o700)
        try:
            value = secret_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            value = secrets.token_urlsafe(32)
            try:
                fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(value + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                value = secret_path.read_text(encoding="utf-8").strip()
        os.chmod(secret_path, 0o600)
    except OSError:
        return None
    encoded = value.encode("utf-8")
    return encoded if len(encoded) >= 32 else None


def base64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def base64url_decode(payload: str, *, max_bytes: int = 4096) -> bytes | None:
    if not payload or len(payload) > max_bytes * 2 or not re.fullmatch(r"[A-Za-z0-9_-]+", payload):
        return None
    try:
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    except (ValueError, TypeError):
        return None
    return decoded if len(decoded) <= max_bytes else None


def signed_access_payload(encoded_payload: str, secret: bytes, *, purpose: str = "") -> str:
    message = f"{purpose}{encoded_payload}".encode("ascii")
    return base64url_encode(hmac.new(secret, message, hashlib.sha256).digest())


def parse_ebook_access_payload(
    token: str,
    *,
    purpose: str = "",
    now: int | None = None,
    max_ttl_seconds: int,
) -> dict | None:
    if not isinstance(token, str) or len(token) > 8192:
        return None
    parts = token.split(".")
    if len(parts) != 2:
        return None
    encoded_payload, supplied_signature = parts
    secret = ebook_access_secret()
    if secret is None or not re.fullmatch(r"[A-Za-z0-9_-]{43}", supplied_signature or ""):
        return None
    expected_signature = signed_access_payload(encoded_payload, secret, purpose=purpose)
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    decoded = base64url_decode(encoded_payload)
    if decoded is None:
        return None
    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    book_id = payload.get("bookId")
    user_id = payload.get("userId")
    expires_at = payload.get("exp")
    nonce = payload.get("nonce")
    current = int(time.time()) if now is None else int(now)
    if (
        not isinstance(book_id, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", book_id)
        or ".." in book_id
        or isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id < 1
        or payload.get("scope") != "full"
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or expires_at <= current
        or expires_at > current + max_ttl_seconds
        or not isinstance(nonce, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", nonce)
    ):
        return None
    return payload


def verify_ebook_access_ticket(ticket: str, *, now: int | None = None) -> dict | None:
    # Node's ticket signer intentionally signs the raw base64url payload without
    # a prefix; keep this wire contract stable between the two applications.
    return parse_ebook_access_payload(
        ticket,
        now=now,
        max_ttl_seconds=EBOOK_ACCESS_TICKET_MAX_TTL_SECONDS,
    )


def consume_ebook_access_nonce(payload: dict, *, now: int | None = None) -> bool:
    """Atomically make a short-lived Saju ticket single-use across workers."""

    current = int(time.time()) if now is None else int(now)
    try:
        EBOOK_ACCESS_NONCE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(EBOOK_ACCESS_NONCE_DIR, 0o700)
    except OSError:
        return False

    # Bounded opportunistic cleanup keeps this tiny replay cache from growing.
    try:
        for stale in list(EBOOK_ACCESS_NONCE_DIR.glob("*.used"))[:32]:
            try:
                expires_at = int(stale.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                continue
            if expires_at <= current:
                stale.unlink(missing_ok=True)
    except OSError:
        pass

    material = f"{payload['bookId']}\x1f{payload['userId']}\x1f{payload['nonce']}".encode("utf-8")
    marker = EBOOK_ACCESS_NONCE_DIR / f"{hashlib.sha256(material).hexdigest()}.used"
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(str(payload["exp"]))
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        marker.unlink(missing_ok=True)
        return False
    return True


def create_ebook_reader_grant(ticket_payload: dict, *, now: int | None = None) -> str | None:
    secret = ebook_access_secret()
    if secret is None:
        return None
    current = int(time.time()) if now is None else int(now)
    grant = {
        "bookId": ticket_payload["bookId"],
        "userId": ticket_payload["userId"],
        "scope": "full",
        "exp": current + EBOOK_READER_GRANT_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(16),
    }
    encoded = base64url_encode(json.dumps(grant, separators=(",", ":")).encode("utf-8"))
    return f"{encoded}.{signed_access_payload(encoded, secret, purpose='reader-grant:')}"


def valid_ebook_reader_grant(book_id: str, *, now: int | None = None) -> bool:
    token = request.cookies.get(EBOOK_READER_COOKIE, "")
    payload = parse_ebook_access_payload(
        token,
        purpose="reader-grant:",
        now=now,
        max_ttl_seconds=EBOOK_READER_GRANT_TTL_SECONDS,
    )
    return bool(payload and payload["bookId"] == book_id)


def valid_hex_color(value: str) -> bool:
    if len(value) not in {4, 7} or not value.startswith("#"):
        return False
    try:
        int(value[1:], 16)
    except ValueError:
        return False
    return True


def load_site_settings() -> dict[str, str]:
    settings = DEFAULT_SITE_SETTINGS.copy()
    try:
        payload = json.loads(SITE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return settings
    if not isinstance(payload, dict):
        return settings
    for key, limit in SITE_SETTING_LIMITS.items():
        value = payload.get(key)
        if isinstance(value, str) and (value.strip() or key in OPTIONAL_SITE_SETTINGS):
            settings[key] = value.strip()[:limit]
    for key in ("accent_color", "primary_color"):
        value = payload.get(key)
        if isinstance(value, str) and valid_hex_color(value.strip()):
            settings[key] = value.strip().lower()
    try:
        upload_limit = int(str(payload.get("upload_limit_mb", DEFAULT_UPLOAD_MB)))
    except (TypeError, ValueError):
        upload_limit = DEFAULT_UPLOAD_MB
    settings["upload_limit_mb"] = str(max(MIN_UPLOAD_MB, min(upload_limit, MAX_CONFIG_UPLOAD_MB)))
    settings["ai_cover_enabled"] = "1" if str(payload.get("ai_cover_enabled", "1")).lower() in {"1", "true", "yes", "on"} else "0"
    model = str(payload.get("ai_cover_model", "gpt-image-2")).strip()
    settings["ai_cover_model"] = model if model in AI_COVER_MODELS else "gpt-image-2"
    quality = str(payload.get("ai_cover_quality", "medium")).strip().lower()
    settings["ai_cover_quality"] = quality if quality in AI_COVER_QUALITIES else "medium"
    return settings


def configured_upload_mb(settings: dict[str, str] | None = None) -> int:
    values = settings or load_site_settings()
    try:
        value = int(values.get("upload_limit_mb", str(DEFAULT_UPLOAD_MB)))
    except (TypeError, ValueError):
        value = DEFAULT_UPLOAD_MB
    return max(MIN_UPLOAD_MB, min(value, MAX_CONFIG_UPLOAD_MB))


def configured_upload_bytes(settings: dict[str, str] | None = None) -> int:
    return configured_upload_mb(settings) * 1024 * 1024


def ai_cover_enabled(settings: dict[str, str] | None = None) -> bool:
    values = settings or load_site_settings()
    return values.get("ai_cover_enabled", "1") == "1"


def save_site_settings(settings: dict[str, str]) -> None:
    write_json_atomic(SITE_SETTINGS_PATH, settings)


def is_admin_user() -> bool:
    return bool(session.get("authenticated")) and session.get("username") in SITE_EDITOR_USERS


def bounded_manifest_int(value, default: int, minimum: int, maximum: int) -> int:
    """Return a bounded integer from untrusted legacy manifest data."""

    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if minimum <= parsed <= maximum else default


def normalize_commerce(value) -> dict:
    """Normalize legacy/malformed commerce metadata without making it public."""

    source = value if isinstance(value, dict) else {}
    status = source.get("sale_status", source.get("status", "private"))
    status = status if status in SALE_STATUSES else "private"
    access = source.get("access", "free")
    access = access if access in BOOK_ACCESS_TYPES else "free"
    price = bounded_manifest_int(source.get("price_krw"), 0, 0, MAX_BOOK_PRICE_KRW)
    sample_pages = bounded_manifest_int(source.get("sample_pages"), 5, 1, MAX_SAMPLE_PAGES)

    raw_benefit = source.get("consultation_benefit")
    benefit_source = raw_benefit if isinstance(raw_benefit, dict) else {}
    benefit_type = benefit_source.get("type", "none")
    if benefit_type not in CONSULTATION_BENEFIT_TYPES:
        benefit_type = "none"
    benefit_value_limit = 100 if benefit_type == "percent" else MAX_BENEFIT_VALUE_KRW
    benefit_value = bounded_manifest_int(
        benefit_source.get("value"),
        0,
        0,
        benefit_value_limit,
    )
    if benefit_type == "none" or benefit_value == 0:
        benefit_type = "none"
        benefit_value = 0

    # An inconsistent paid legacy record must never become a zero-price public item.
    if access == "free":
        price = 0
    elif price < 100:
        status = "private"

    normalized = {
        "sale_status": status,
        "access": access,
        "price_krw": price,
        "sample_pages": sample_pages,
        "consultation_benefit": {
            "type": benefit_type,
            "value": benefit_value,
            "max_discount_krw": bounded_manifest_int(
                benefit_source.get("max_discount_krw"),
                10000,
                0,
                MAX_BENEFIT_VALUE_KRW,
            ),
            "max_uses": bounded_manifest_int(
                benefit_source.get("max_uses"),
                1,
                1,
                MAX_BENEFIT_USES,
            ),
            "valid_days": bounded_manifest_int(
                benefit_source.get("valid_days"),
                90,
                1,
                MAX_BENEFIT_VALID_DAYS,
            ),
        },
    }
    previous_status = source.get("sale_status_before_archive")
    if status == "archived" and previous_status in {"private", "published"}:
        normalized["sale_status_before_archive"] = previous_status
    return normalized


class CommerceValidationError(ValueError):
    pass


def required_form_int(name: str, label: str, minimum: int, maximum: int) -> int:
    raw = request.form.get(name, "").strip()
    if not re.fullmatch(r"0|[1-9][0-9]*", raw):
        raise CommerceValidationError(f"{label}은 숫자로 입력해 주세요.")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise CommerceValidationError(f"{label}은 {minimum:,}~{maximum:,} 사이로 입력해 주세요.")
    return value


def commerce_from_form(current: dict | None = None) -> dict:
    sale_status = request.form.get("sale_status", "").strip()
    access = request.form.get("access", "").strip()
    benefit_type = request.form.get("benefit_type", "").strip()
    if sale_status not in SALE_STATUSES:
        raise CommerceValidationError("판매 상태를 다시 선택해 주세요.")
    if access not in BOOK_ACCESS_TYPES:
        raise CommerceValidationError("무료 또는 유료 판매 방식을 선택해 주세요.")
    if benefit_type not in CONSULTATION_BENEFIT_TYPES:
        raise CommerceValidationError("상담 혜택 방식을 다시 선택해 주세요.")

    price = required_form_int("price_krw", "판매 가격", 0, MAX_BOOK_PRICE_KRW)
    if access == "paid" and price < 100:
        raise CommerceValidationError("유료 책은 결제 가능한 100원 이상의 판매 가격이 필요합니다.")
    if access == "free":
        price = 0
    sample_pages = required_form_int("sample_pages", "무료 샘플 쪽수", 1, MAX_SAMPLE_PAGES)

    benefit_limit = 100 if benefit_type == "percent" else MAX_BENEFIT_VALUE_KRW
    benefit_value = required_form_int("benefit_value", "상담 혜택 값", 0, benefit_limit)
    if benefit_type != "none" and benefit_value == 0:
        raise CommerceValidationError("상담 혜택을 사용할 때는 1 이상의 할인 값을 입력해 주세요.")
    if benefit_type == "none":
        benefit_value = 0

    commerce = {
        "sale_status": sale_status,
        "access": access,
        "price_krw": price,
        "sample_pages": sample_pages,
        "consultation_benefit": {
            "type": benefit_type,
            "value": benefit_value,
            "max_discount_krw": required_form_int(
                "benefit_max_discount_krw",
                "상담 할인 상한",
                0,
                MAX_BENEFIT_VALUE_KRW,
            ),
            "max_uses": required_form_int(
                "benefit_max_uses",
                "상담 혜택 사용 횟수",
                1,
                MAX_BENEFIT_USES,
            ),
            "valid_days": required_form_int(
                "benefit_valid_days",
                "상담 혜택 유효기간",
                1,
                MAX_BENEFIT_VALID_DAYS,
            ),
        },
    }
    previous = normalize_commerce(current).get("sale_status", "private")
    if sale_status == "archived":
        if previous == "archived":
            previous = normalize_commerce(current).get("sale_status_before_archive", "private")
        commerce["sale_status_before_archive"] = previous if previous in {"private", "published"} else "private"
    return commerce


def csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app = Flask(__name__)
app.config["SECRET_KEY"] = session_secret()
# 설정 화면에서 10~500MB 사이로 바꿀 수 있으므로 Flask 자체 상한은
# multipart 여유분을 포함한 절대 상한으로 두고, 실제 값은 before_request에서 검사한다.
app.config["MAX_CONTENT_LENGTH"] = (MAX_CONFIG_UPLOAD_MB + 1) * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "1").lower() in {"1", "true", "yes", "on"}


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


def set_user_password(username: str, new_password: str) -> bool:
    """users.json의 해당 사용자 비밀번호를 새 salt/hash로 갱신(원자적 쓰기). 성공 시 True."""
    try:
        payload = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        app.logger.warning("비밀번호 변경용 사용자 파일 읽기 실패: %s", exc)
        return False
    users = payload.get("users")
    if not isinstance(users, dict) or username not in users:
        return False
    salt = secrets.token_hex(16)
    users[username]["salt"] = salt
    users[username]["password_hash"] = password_hash(new_password, salt)
    tmp = USERS_PATH.with_suffix(USERS_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, USERS_PATH)
    return True


def resolve_email(username: str) -> str:
    """아이디에 대응하는 등록 이메일. 없으면 빈 문자열."""
    if "@" in username:
        return username
    return EMAIL_MAP.get(username, "")


def generate_temp_password(length: int = 10) -> str:
    # 혼동되는 문자(0/O, 1/l/I) 제외한 영숫자
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def claim_forgot_request(username: str, now: float | None = None) -> bool:
    """모든 Gunicorn worker가 공유하는 파일 기반 비밀번호 재설정 쿨다운."""
    now = time.time() if now is None else now
    FORGOT_STATE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()
    stamp_path = FORGOT_STATE_DIR / f"{digest}.stamp"
    lock_path = FORGOT_STATE_DIR / ".lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            last = float(stamp_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            last = 0
        if now - last < FORGOT_COOLDOWN_SECONDS:
            return False
        stamp_path.write_text(str(now), encoding="utf-8")
        stamp_path.chmod(0o600)
        return True


def release_forgot_request(username: str) -> None:
    """메일 발송이나 비밀번호 저장 실패 시 쿨다운 예약을 취소한다."""
    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()
    stamp_path = FORGOT_STATE_DIR / f"{digest}.stamp"
    lock_path = FORGOT_STATE_DIR / ".lock"
    FORGOT_STATE_DIR.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        stamp_path.unlink(missing_ok=True)


def send_temp_password_email(to_email: str, temp_password: str) -> bool:
    """등록 이메일로 임시 비밀번호 안내 메일 발송. 성공 시 True."""
    if not (SMTP_USER and SMTP_PASS and to_email):
        app.logger.warning("메일 자격증명/수신자 누락 — 발송 생략")
        return False
    subject = "[혜경 전자책 스튜디오] 임시 비밀번호 안내"
    body = (
        "안녕하세요, 혜경 전자책 스튜디오입니다.\n\n"
        "요청하신 임시 비밀번호를 안내드립니다.\n\n"
        f"    임시 비밀번호: {temp_password}\n\n"
        "위 비밀번호로 로그인하신 뒤, 설정 화면에서 새 비밀번호로 변경해 주세요.\n"
        "본인이 요청하지 않았다면 이 메일을 무시하셔도 됩니다.\n\n"
        "감사합니다.\n"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = SMTP_USER
    msg["To"] = to_email
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
        try:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [to_email], msg.as_string())
        finally:
            server.quit()
        return True
    except Exception as exc:
        app.logger.warning("임시 비밀번호 메일 발송 실패: %s", exc)
        return False


def login_return_target() -> str:
    if request.method in {"GET", "HEAD"}:
        return request.path
    if request.endpoint in {"update_book_commerce", "archive_book", "restore_book"}:
        return url_for("settings") + "#book-commerce"
    if request.endpoint == "update_publishing_settings":
        return url_for("settings") + "#publishing-settings"
    if request.endpoint == "update_appearance":
        return url_for("settings") + "#appearance-settings"
    if request.endpoint in {"publish", "create_cover_draft"}:
        return url_for("index") + "#publish-workbench"
    return url_for("index")


def login_required():
    if not session.get("authenticated"):
        return redirect(url_for("login", next=login_return_target()))
    return None


def upload_too_large_response():
    upload_mb = configured_upload_mb()
    message = f"원고 파일은 현재 설정된 {upload_mb}MB 이하만 업로드할 수 있습니다. 설정에서 한도를 변경할 수 있습니다."
    if request.endpoint == "create_cover_draft" or request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": False, "error": message}), 413
    flash(message, "error")
    return redirect(url_for("index") + "#publish-workbench")


@app.before_request
def protect_pages():
    if request.endpoint in {
        "index",
        "login",
        "static",
        "health",
        "forgot",
        "html_book_asset",
        "catalog_books",
        "catalog_book",
        "catalog_book_cover",
        "public_book_sample",
        "public_book_sample_page",
        "exchange_ebook_access",
        "public_full_book",
        "public_full_book_page",
    }:
        return None
    auth_response = login_required()
    if auth_response is not None:
        return auth_response
    if request.method == "POST" and request.endpoint in {"publish", "create_cover_draft"}:
        # multipart 경계값과 일반 필드에 약간의 여유를 둔다.
        if request.content_length and request.content_length > configured_upload_bytes() + 1024 * 1024:
            return upload_too_large_response()
    return None


@app.after_request
def redact_html_capability_from_access_log(response):
    """Keep expiring HTML capabilities out of Gunicorn's request-line log.

    Gunicorn builds the access-log atoms after the WSGI application returns,
    so replacing only the logging-related environment values here does not
    affect Flask routing or the response sent to the browser.
    """

    if session.get("authenticated") and response.mimetype == "text/html":
        # Protected pages must never reappear from the browser's back/forward
        # cache after logout. They also vary by the signed session cookie.
        response.cache_control.private = True
        response.cache_control.no_store = True
        response.cache_control.max_age = 0
        response.vary.add("Cookie")

    if request.endpoint == "html_book_asset":
        redacted_path = "/html-content/[capability-redacted]"
        request.environ["RAW_URI"] = redacted_path
        request.environ["REQUEST_URI"] = redacted_path
        request.environ["PATH_INFO"] = redacted_path
        request.environ["QUERY_STRING"] = ""
    elif request.endpoint == "exchange_ebook_access":
        redacted_path = "/library/access?[ticket-redacted]"
        request.environ["RAW_URI"] = redacted_path
        request.environ["REQUEST_URI"] = redacted_path
        request.environ["PATH_INFO"] = "/library/access"
        request.environ["QUERY_STRING"] = ""
    return response


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_error):
    return upload_too_large_response()


@app.context_processor
def inject_globals():
    return {
        "app_version": APP_VERSION,
        "current_user": session.get("username", ""),
        "is_admin": is_admin_user(),
        "site": load_site_settings(),
        "csrf_token": csrf_token() if session.get("authenticated") else "",
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
            return redirect(safe_local_next_url(request.args.get("next", "")) or url_for("index"))
        error = "아이디 또는 비밀번호가 올바르지 않거나 전자책 권한이 없습니다."
    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    if not valid_csrf_submission():
        abort(400)
    session.clear()
    return redirect(url_for("login"))


@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        # 사용자 열거 방지: 결과와 무관하게 항상 같은 안내를 표시한다.
        generic = "등록된 계정이면 해당 이메일로 임시 비밀번호를 보냈습니다. 메일함을 확인해 주세요."
        email = resolve_email(username)
        valid = username in ALLOWED_USERS and bool(read_users().get(username)) and bool(email)
        if valid and claim_forgot_request(username):
            temp_password = generate_temp_password()
            # 발송 성공 후에만 비밀번호를 저장한다(메일 실패 시 잠김 방지).
            if not (send_temp_password_email(email, temp_password) and set_user_password(username, temp_password)):
                release_forgot_request(username)
        flash(generic, "success")
        return redirect(url_for("login"))
    return render_template("forgot.html")


@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    username = session.get("username", "")
    if request.method == "POST":
        if not valid_csrf_submission():
            abort(400)
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not verify_user(username, current):
            flash("현재 비밀번호가 올바르지 않습니다.", "error")
        elif len(new) < MIN_PASSWORD_LENGTH:
            flash(f"새 비밀번호는 최소 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다.", "error")
        elif new != confirm:
            flash("새 비밀번호 확인이 일치하지 않습니다.", "error")
        elif new == current:
            flash("새 비밀번호가 현재 비밀번호와 동일합니다.", "error")
        elif set_user_password(username, new):
            flash("비밀번호를 변경했습니다. (YouTube Shorts 로그인에도 동일하게 적용됩니다)", "success")
            return redirect(url_for("settings"))
        else:
            flash("비밀번호 변경에 실패했습니다. 잠시 후 다시 시도해 주세요.", "error")
    return redirect(url_for("settings"))


@app.get("/")
def index():
    publisher_mode = bool(session.get("authenticated"))
    all_books = list_books(limit=None)
    if publisher_mode:
        books = all_books[:30]
    else:
        books = [
            public_library_card_payload(book)
            for book in all_books
            if book["commerce"]["sale_status"] == "published"
        ]
    site_settings = load_site_settings()
    upload_mb = configured_upload_mb(site_settings)
    response = app.make_response(
        render_template(
            "index.html",
            books=books,
            publisher_mode=publisher_mode,
            upload_mb=upload_mb,
            upload_max_bytes=upload_mb * 1024 * 1024,
            ai_cover_enabled=ai_cover_enabled(site_settings),
            ai_cover_available=bool(os.getenv("OPENAI_API_KEY")),
        )
    )
    # The same URL serves a public catalog or a private publisher workbench
    # depending on the authenticated session. Never let a shared proxy reuse
    # one viewer's representation for another viewer.
    response.cache_control.private = True
    response.cache_control.no_store = True
    response.cache_control.max_age = 0
    response.vary.add("Cookie")
    return response


def public_library_card_payload(manifest: dict) -> dict:
    """Return only fields that are safe to render in the anonymous library."""

    commerce = normalize_commerce(manifest.get("commerce"))
    book_id = str(manifest["book_id"])
    created_at = str(manifest.get("created_at") or "")
    return {
        "book_id": book_id,
        "title": str(manifest.get("title") or "제목 없는 책"),
        "author": str(manifest.get("author") or "저자 미상"),
        "description": str(manifest.get("description") or ""),
        "chapter_count": bounded_manifest_int(manifest.get("chapter_count"), 0, 0, 100_000),
        "created_at": created_at,
        "is_new": bool(manifest.get("is_new")),
        "commerce": {
            "access": commerce["access"],
            "price_krw": commerce["price_krw"],
            "sample_pages": commerce["sample_pages"],
        },
        "cover_url": url_for("catalog_book_cover", book_id=book_id),
        "sample_url": url_for("public_book_sample", book_id=book_id),
        "purchase_url": (
            f"{configured_origin(SAJU_LIBRARY_ORIGIN, 'https://saju.xsw.kr')}"
            f"/books/{quote(book_id, safe='')}"
        ),
    }


def valid_csrf_submission() -> bool:
    supplied = request.form.get("csrf_token", "")
    expected = session.get("_csrf_token", "")
    return bool(supplied and expected and hmac.compare_digest(supplied, expected))


def book_meta_from_form(original_filename: str = "") -> BookMeta:
    fallback_title = display_title_from_filename(Path(original_filename)) if original_filename else "제목 없는 책"
    return BookMeta(
        title=request.form.get("title", "").strip() or fallback_title,
        subtitle=request.form.get("subtitle", "").strip(),
        author=request.form.get("author", "").strip() or "기혜경",
        publisher=request.form.get("publisher", "").strip() or load_site_settings()["name"],
        description=request.form.get("description", "").strip(),
    )


def cover_draft_lock_dir() -> Path:
    return COVER_DRAFT_DIR / ".locks"


def cover_draft_user_key(username: str) -> str:
    return hashlib.sha256(username.encode("utf-8")).hexdigest()


def cover_draft_user_lock_path(username: str) -> Path:
    return cover_draft_lock_dir() / f"user-{cover_draft_user_key(username)}.lock"


def cover_draft_token_lock_path(token: str) -> Path:
    return cover_draft_lock_dir() / f"draft-{token}.lock"


@contextmanager
def try_advisory_lock(path: Path):
    """여러 Gunicorn worker가 공유하는 nonblocking 파일 락."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    locked = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
            return
        locked = True
        yield handle
    finally:
        if locked:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def cleanup_cover_drafts(now: float | None = None) -> None:
    """만료된 초안만 제거하며, 현재 생성/출판 중인 초안은 건드리지 않는다."""
    now = time.time() if now is None else now
    try:
        entries = list(COVER_DRAFT_DIR.iterdir())
    except FileNotFoundError:
        return
    for path in entries:
        if path.name == ".locks":
            continue
        try:
            if path.is_symlink():
                path.unlink(missing_ok=True)
                continue
            if not path.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", path.name):
                continue
            expired = now - path.stat().st_mtime > COVER_DRAFT_TTL_SECONDS
        except OSError:
            continue
        if not expired:
            continue
        with try_advisory_lock(cover_draft_token_lock_path(path.name)) as lock:
            if lock is None:
                continue
            try:
                still_expired = now - path.stat().st_mtime > COVER_DRAFT_TTL_SECONDS
            except OSError:
                continue
            if still_expired and path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)


def cover_draft_root(token: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return None
    candidate = COVER_DRAFT_DIR / token
    try:
        if candidate.is_symlink() or not candidate.is_dir():
            return None
        base = COVER_DRAFT_DIR.resolve()
        root = candidate.resolve(strict=True)
    except OSError:
        return None
    if root.parent != base:
        return None
    return root


def safe_cover_draft_file(root: Path, name: str) -> Path | None:
    """초안 루트 바로 아래의 일반 파일만 허용한다(경로 이탈/심볼릭 링크 차단)."""
    if not name or Path(name).name != name or "/" in name or "\\" in name:
        return None
    candidate = root / name
    try:
        if root.is_symlink() or candidate.is_symlink():
            return None
        root_resolved = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if resolved.parent != root_resolved or not resolved.is_file():
        return None
    return resolved


def read_cover_draft(token: str, *, require_owner: bool = True) -> tuple[Path, dict] | None:
    root = cover_draft_root(token)
    if root is None:
        return None
    manifest = safe_cover_draft_file(root, "draft.json")
    if manifest is None:
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if require_owner and payload.get("username") != session.get("username"):
        return None
    try:
        created_at = float(payload.get("created_at", 0))
    except (TypeError, ValueError):
        return None
    if created_at <= 0 or time.time() - created_at > COVER_DRAFT_TTL_SECONDS:
        return None
    return root, payload


def cover_draft_disk_usage(root: Path) -> int:
    total = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if not entry.is_symlink() and entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def user_cover_draft_usage(username: str) -> tuple[int, int]:
    count = 0
    total = 0
    try:
        entries = list(COVER_DRAFT_DIR.iterdir())
    except FileNotFoundError:
        return count, total
    for entry in entries:
        if not re.fullmatch(r"[0-9a-f]{32}", entry.name):
            continue
        draft = read_cover_draft(entry.name, require_owner=False)
        if draft is None:
            continue
        root, payload = draft
        if payload.get("username") != username:
            continue
        count += 1
        total += cover_draft_disk_usage(root)
    return count, total


def cover_draft_user_byte_limit(upload_limit: int) -> int:
    # TXT/Markdown은 원본과 추출문이 함께 보관되므로 최대 원고 두 배와
    # 표지/메타데이터 여유를 보장한다.
    return max(COVER_DRAFT_STORAGE_FLOOR_BYTES, upload_limit * 2 + 32 * 1024 * 1024)


def claim_ai_cover_quota_locked(username: str, now: float | None = None) -> bool:
    """사용자 락을 잡은 상태에서 시간당 AI 생성 횟수를 원자적으로 예약한다."""
    now = time.time() if now is None else now
    state_path = cover_draft_lock_dir() / f"quota-{cover_draft_user_key(username)}.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        raw_timestamps = payload.get("timestamps", []) if isinstance(payload, dict) else []
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        raw_timestamps = []
    timestamps: list[float] = []
    for value in raw_timestamps:
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            continue
        if 0 <= now - timestamp < AI_COVER_QUOTA_WINDOW_SECONDS:
            timestamps.append(timestamp)
    if len(timestamps) >= AI_COVER_HOURLY_QUOTA:
        return False
    timestamps.append(now)
    write_json_atomic(state_path, {"timestamps": timestamps})
    return True


def cover_meta_signature(meta: BookMeta) -> str:
    payload = {
        "title": meta.title,
        "subtitle": meta.subtitle,
        "author": meta.author,
        "publisher": meta.publisher,
        "description": meta.description,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_ai_or_template_cover(
    meta: BookMeta,
    extracted_text: str,
    output_path: Path,
    *,
    variation: int = 0,
) -> str:
    settings = load_site_settings()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not ai_cover_enabled(settings) or not api_key:
        create_cover(meta, output_path)
        return "template"

    artwork_path = output_path.with_name(f".{output_path.stem}-ai-art-{secrets.token_hex(4)}.png")
    try:
        generate_ai_cover_background(
            meta,
            extracted_text,
            artwork_path,
            api_key=api_key,
            model=settings["ai_cover_model"],
            quality=settings["ai_cover_quality"],
            timeout=180,
            variation=variation,
        )
        create_cover(meta, output_path, background_path=artwork_path)
        return "ai"
    except AICoverError as exc:
        app.logger.warning("AI 표지 생성 실패(%s)", type(exc).__name__)
        create_cover(meta, output_path)
        return "template"
    except Exception as exc:
        app.logger.warning("AI 표지 처리 실패(%s)", type(exc).__name__)
        create_cover(meta, output_path)
        return "template"
    finally:
        artwork_path.unlink(missing_ok=True)


@app.post("/cover-drafts")
def create_cover_draft():
    if not valid_csrf_submission():
        return jsonify({"ok": False, "error": "요청 확인 정보가 만료되었습니다. 화면을 새로고침해 주세요."}), 400

    cleanup_cover_drafts()
    token = request.form.get("draft_token", "").strip()
    username = str(session.get("username", ""))
    if token:
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            return jsonify({"ok": False, "error": "표지 초안이 만료되었습니다. 원고를 다시 선택해 주세요."}), 410
        with try_advisory_lock(cover_draft_token_lock_path(token)) as draft_lock:
            if draft_lock is None:
                return jsonify({"ok": False, "error": "이 표지 초안을 이미 처리하고 있습니다. 잠시 후 다시 시도해 주세요."}), 409
            with try_advisory_lock(cover_draft_user_lock_path(username)) as user_lock:
                if user_lock is None:
                    return jsonify({"ok": False, "error": "다른 표지를 생성하고 있습니다. 완료된 뒤 다시 시도해 주세요."}), 429
                return create_cover_draft_locked(token)

    with try_advisory_lock(cover_draft_user_lock_path(username)) as user_lock:
        if user_lock is None:
            return jsonify({"ok": False, "error": "다른 표지를 생성하고 있습니다. 완료된 뒤 다시 시도해 주세요."}), 429
        return create_cover_draft_locked("")


def create_cover_draft_locked(token: str):
    upload_limit = configured_upload_bytes()
    draft = read_cover_draft(token) if token else None
    new_draft = draft is None
    username = str(session.get("username", ""))
    draft_count, existing_usage = user_cover_draft_usage(username)
    user_byte_limit = cover_draft_user_byte_limit(upload_limit)

    if token and draft is None:
        return jsonify({"ok": False, "error": "표지 초안이 만료되었습니다. 원고를 다시 선택해 주세요."}), 410

    if draft is None:
        if draft_count >= COVER_DRAFT_MAX_PER_USER or existing_usage >= user_byte_limit:
            return jsonify(
                {"ok": False, "error": "보관 중인 표지 초안이 많습니다. 기존 초안을 출판하거나 잠시 후 다시 시도해 주세요."}
            ), 429
        source = request.files.get("source")
        if not source or not source.filename:
            return jsonify({"ok": False, "error": "표지를 만들 원고 파일을 먼저 선택해 주세요."}), 400
        original_filename = source.filename or ""
        extension = Path(original_filename).suffix.lower()
        if extension not in {".pdf", ".txt", ".md", ".markdown", ".zip"}:
            return jsonify({"ok": False, "error": "PDF, EPUB용 TXT·Markdown, HTML 책 ZIP 원고만 표지를 만들 수 있습니다."}), 400
        token = secrets.token_hex(16)
        root = COVER_DRAFT_DIR / token
        root.mkdir(parents=True, exist_ok=False)
        source_path = root / f"source{extension}"
        try:
            source.save(source_path)
            if source_path.stat().st_size > upload_limit:
                shutil.rmtree(root, ignore_errors=True)
                return upload_too_large_response()
            if existing_usage + cover_draft_disk_usage(root) > user_byte_limit:
                shutil.rmtree(root, ignore_errors=True)
                return jsonify(
                    {"ok": False, "error": "임시 표지 작업 공간이 가득 찼습니다. 기존 초안을 출판한 뒤 다시 시도해 주세요."}
                ), 429
            extracted_text = extract_text(source_path, upload_limit)
            (root / "extracted.txt").write_text(extracted_text, encoding="utf-8")
            if existing_usage + cover_draft_disk_usage(root) > user_byte_limit:
                shutil.rmtree(root, ignore_errors=True)
                return jsonify(
                    {"ok": False, "error": "임시 표지 작업 공간이 가득 찼습니다. 기존 초안을 출판한 뒤 다시 시도해 주세요."}
                ), 429
        except HTMLBookError as exc:
            shutil.rmtree(root, ignore_errors=True)
            return jsonify({"ok": False, "error": str(exc)}), 422
        except Exception as exc:
            shutil.rmtree(root, ignore_errors=True)
            app.logger.warning("표지 초안 원고 처리 실패(%s)", type(exc).__name__)
            return jsonify({"ok": False, "error": "원고를 읽지 못했습니다. 파일 형식과 손상 여부를 확인해 주세요."}), 422
        payload = {
            "username": username,
            "created_at": time.time(),
            "original_filename": original_filename,
            "source_file": source_path.name,
            "generation": 0,
        }
    else:
        root, payload = draft
        if payload.get("state", "ready") != "ready":
            return jsonify({"ok": False, "error": "이 표지 초안을 출판하고 있습니다. 완료될 때까지 기다려 주세요."}), 409
        if existing_usage > user_byte_limit:
            return jsonify(
                {"ok": False, "error": "임시 표지 작업 공간이 가득 찼습니다. 기존 초안을 출판한 뒤 다시 시도해 주세요."}
            ), 429
        original_filename = str(payload.get("original_filename", "원고.pdf"))
        source_name = str(payload.get("source_file", ""))
        if not re.fullmatch(r"source\.(?:pdf|txt|md|markdown|zip)", source_name):
            return jsonify({"ok": False, "error": "임시 원고를 확인할 수 없습니다. 원고를 다시 선택해 주세요."}), 410
        source_path = safe_cover_draft_file(root, source_name)
        extracted_path = safe_cover_draft_file(root, "extracted.txt")
        if source_path is None or extracted_path is None:
            return jsonify({"ok": False, "error": "임시 원고를 확인할 수 없습니다. 원고를 다시 선택해 주세요."}), 410
        try:
            extracted_text = extracted_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return jsonify({"ok": False, "error": "표지 초안이 만료되었습니다. 원고를 다시 선택해 주세요."}), 410

    meta = book_meta_from_form(original_filename)
    try:
        generation = int(payload.get("generation", 0)) + 1
    except (TypeError, ValueError):
        generation = 1
    settings = load_site_settings()
    will_call_ai = ai_cover_enabled(settings) and bool(os.getenv("OPENAI_API_KEY", "").strip())
    if will_call_ai and not claim_ai_cover_quota_locked(username):
        if new_draft:
            shutil.rmtree(root, ignore_errors=True)
        return jsonify(
            {"ok": False, "error": f"AI 표지는 계정당 시간당 {AI_COVER_HOURLY_QUOTA}회까지 만들 수 있습니다. 잠시 후 다시 시도해 주세요."}
        ), 429

    cover_path = root / "cover.png"
    try:
        mode = create_ai_or_template_cover(meta, extracted_text, cover_path, variation=generation)
        payload.update(
            {
                "generation": generation,
                "cover_signature": cover_meta_signature(meta),
                "cover_mode": mode,
                "state": "ready",
                "updated_at": time.time(),
            }
        )
        write_json_atomic(root / "draft.json", payload)
    except Exception as exc:
        if new_draft:
            shutil.rmtree(root, ignore_errors=True)
        app.logger.warning("표지 초안 생성 실패(%s)", type(exc).__name__)
        return jsonify({"ok": False, "error": "표지를 완성하지 못했습니다. 잠시 후 다시 시도해 주세요."}), 500
    message = (
        "책 소개 또는 원고 앞부분의 주제와 분위기를 반영한 AI 표지가 완성되었습니다."
        if mode == "ai"
        else "AI 연결을 사용할 수 없어 한글 안전 기본 표지를 준비했습니다. 출판은 계속할 수 있습니다."
    )
    return jsonify(
        {
            "ok": True,
            "draft_token": token,
            "cover_token": token,
            "cover_url": url_for("cover_draft_image", token=token, v=generation),
            "mode": mode,
            "message": message,
            "title": meta.title,
            "new_draft": new_draft,
        }
    )


@app.get("/cover-drafts/<token>/cover")
def cover_draft_image(token: str):
    draft = read_cover_draft(token)
    if draft is None:
        abort(404)
    root, _payload = draft
    path = safe_cover_draft_file(root, "cover.png")
    if path is None:
        abort(404)
    response = send_file(path, mimetype="image/png", conditional=True, max_age=0)
    response.cache_control.public = False
    response.cache_control.private = True
    response.cache_control.no_cache = True
    return response


@app.post("/publish")
def publish():
    if not valid_csrf_submission():
        abort(400)

    cleanup_cover_drafts()
    draft_token = request.form.get("draft_token", "").strip()
    username = str(session.get("username", ""))
    if draft_token:
        if not re.fullmatch(r"[0-9a-f]{32}", draft_token):
            flash("임시 원고가 만료되었습니다. 원고 파일을 다시 선택해 주세요.", "error")
            return redirect(url_for("index") + "#publish-workbench")
        with try_advisory_lock(cover_draft_token_lock_path(draft_token)) as draft_lock:
            if draft_lock is None:
                flash("이 초안을 이미 출판하고 있습니다. 완료될 때까지 기다려 주세요.", "error")
                return redirect(url_for("index") + "#publish-workbench")
            with try_advisory_lock(cover_draft_user_lock_path(username)) as user_lock:
                if user_lock is None:
                    flash("다른 표지 또는 출판 작업이 진행 중입니다. 완료된 뒤 다시 시도해 주세요.", "error")
                    return redirect(url_for("index") + "#publish-workbench")
                return publish_locked(draft_token)

    with try_advisory_lock(cover_draft_user_lock_path(username)) as user_lock:
        if user_lock is None:
            flash("다른 표지 또는 출판 작업이 진행 중입니다. 완료된 뒤 다시 시도해 주세요.", "error")
            return redirect(url_for("index") + "#publish-workbench")
        return publish_locked("")


def publish_locked(draft_token: str):
    upload_limit = configured_upload_bytes()
    upload_path: Path | None = None
    draft_root: Path | None = None
    draft_payload: dict = {}
    extracted_text_override: str | None = None
    prepared_cover_path: Path | None = None
    prepared_cover_mode = "ai"
    draft = read_cover_draft(draft_token) if draft_token else None

    if draft_token and draft is None:
        flash("임시 원고가 만료되었습니다. 원고 파일을 다시 선택해 주세요.", "error")
        return redirect(url_for("index") + "#publish-workbench")

    if draft is not None:
        draft_root, draft_payload = draft
        if draft_payload.get("state", "ready") != "ready":
            flash("이 초안은 이미 출판 처리 중입니다. 완료될 때까지 기다려 주세요.", "error")
            return redirect(url_for("index") + "#publish-workbench")
        original_filename = str(draft_payload.get("original_filename", "원고.pdf"))
        source_name = str(draft_payload.get("source_file", ""))
        if not re.fullmatch(r"source\.(?:pdf|txt|md|markdown|zip)", source_name):
            flash("임시 원고가 만료되었습니다. 원고 파일을 다시 선택해 주세요.", "error")
            return redirect(url_for("index") + "#publish-workbench")
        candidate = safe_cover_draft_file(draft_root, source_name)
        if candidate is None:
            flash("임시 원고가 만료되었습니다. 원고 파일을 다시 선택해 주세요.", "error")
            return redirect(url_for("index") + "#publish-workbench")
        upload_path = candidate
        extracted_path = safe_cover_draft_file(draft_root, "extracted.txt")
        if extracted_path is not None:
            try:
                extracted_text_override = extracted_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                extracted_text_override = None
    else:
        source = request.files.get("source")
        if not source or not source.filename:
            flash("업로드할 PDF, TXT, Markdown 또는 HTML 책 ZIP 파일을 선택해주세요.", "error")
            return redirect(url_for("index") + "#publish-workbench")
        original_filename = source.filename or ""
        extension = Path(original_filename).suffix.lower()
        if extension not in {".pdf", ".txt", ".md", ".markdown", ".zip"}:
            flash("PDF, TXT, Markdown 또는 HTML 책 ZIP 파일만 업로드할 수 있습니다.", "error")
            return redirect(url_for("index") + "#publish-workbench")
        safe_stem = secure_filename(Path(original_filename).stem) or safe_filename(Path(original_filename).stem)
        upload_name = f"{secrets.token_hex(8)}-{safe_stem or 'source'}{extension}"
        upload_path = UPLOAD_DIR / upload_name
        try:
            source.save(upload_path)
        except Exception as exc:
            upload_path.unlink(missing_ok=True)
            app.logger.warning("출판 원고 저장 실패(%s)", type(exc).__name__)
            flash("원고를 저장하지 못했습니다. 파일을 다시 선택해 주세요.", "error")
            return redirect(url_for("index") + "#publish-workbench")
        if upload_path.stat().st_size > upload_limit:
            upload_path.unlink(missing_ok=True)
            return upload_too_large_response()

    meta = book_meta_from_form(original_filename)
    if draft is not None and request.form.get("cover_token", "").strip() == draft_token:
        if draft_payload.get("cover_signature") == cover_meta_signature(meta):
            candidate_cover = safe_cover_draft_file(draft_root, "cover.png")
            if candidate_cover is not None:
                prepared_cover_path = candidate_cover
                mode = str(draft_payload.get("cover_mode", "ai"))
                prepared_cover_mode = mode if mode in {"ai", "template"} else "template"

    settings = load_site_settings()
    cover_creator = None
    if ai_cover_enabled(settings):
        if os.getenv("OPENAI_API_KEY", "").strip():
            username = str(session.get("username", ""))

            def quota_guarded_cover_creator(meta, extracted_text, output_path, *, variation=0):
                if not claim_ai_cover_quota_locked(username):
                    raise AICoverQuotaExceeded
                return create_ai_or_template_cover(
                    meta,
                    extracted_text,
                    output_path,
                    variation=variation,
                )

            cover_creator = quota_guarded_cover_creator
        else:
            cover_creator = create_ai_or_template_cover

    draft_claimed = False
    if draft_root is not None:
        draft_payload["state"] = "publishing"
        draft_payload["updated_at"] = time.time()
        try:
            write_json_atomic(draft_root / "draft.json", draft_payload)
            draft_claimed = True
        except Exception as exc:
            app.logger.warning("출판 초안 상태 저장 실패(%s)", type(exc).__name__)
            flash("출판 준비 상태를 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.", "error")
            return redirect(url_for("index") + "#publish-workbench")

    try:
        result = build_book(
            upload_path,
            meta,
            BOOK_DIR,
            prepared_cover_path=prepared_cover_path,
            prepared_cover_mode=prepared_cover_mode,
            cover_creator=cover_creator,
            extracted_text_override=extracted_text_override,
            max_source_bytes=upload_limit,
        )
        write_manifest(result, meta)
    except Exception as exc:
        if draft_root is None and upload_path is not None:
            upload_path.unlink(missing_ok=True)
        if draft_claimed and draft_root is not None and draft_root.is_dir():
            draft_payload["state"] = "ready"
            draft_payload["updated_at"] = time.time()
            try:
                write_json_atomic(draft_root / "draft.json", draft_payload)
            except Exception as restore_exc:
                app.logger.warning("출판 초안 상태 복구 실패(%s)", type(restore_exc).__name__)
        app.logger.warning("전자책 생성 실패(%s)", type(exc).__name__)
        if isinstance(exc, AICoverQuotaExceeded):
            flash(f"AI 표지는 계정당 시간당 {AI_COVER_HOURLY_QUOTA}회까지 만들 수 있습니다. 잠시 후 다시 시도해 주세요.", "error")
        elif isinstance(exc, HTMLBookError):
            flash(str(exc), "error")
        else:
            flash("전자책을 생성하지 못했습니다. 원고를 확인한 뒤 다시 시도해 주세요.", "error")
        return redirect(url_for("index") + "#publish-workbench")
    if draft_root is not None:
        shutil.rmtree(draft_root, ignore_errors=True)
    elif upload_path is not None:
        upload_path.unlink(missing_ok=True)

    cover_notice = "AI 표지와 서재 썸네일까지" if getattr(result, "cover_mode", "template") == "ai" else "한글 표지와 서재 썸네일까지"
    flash(f"전자책이 생성되었습니다. {cover_notice} 함께 완성했습니다.", "success")
    return redirect(url_for("book_detail", book_id=result.book_id))


@app.get("/books/<book_id>")
def book_detail(book_id: str):
    manifest = read_manifest(book_id)
    if not manifest:
        abort(404)
    book = dict(manifest)
    book.update(
        {
            "has_html_reader": html_book_paths(manifest) is not None,
            "has_html_archive": safe_book_asset(manifest, "html_archive_path") is not None,
            "has_epub": safe_book_asset(manifest, "epub_path") is not None,
            "has_pdf": safe_book_asset(manifest, "pdf_path") is not None,
            "has_markdown": safe_book_asset(manifest, "markdown_path") is not None,
            "has_source": safe_book_asset(manifest, "source_path") is not None,
        }
    )
    return render_template("book.html", book=book)


def render_isolated_html_reader(manifest: dict, *, ttl_seconds: int | None = None):
    """Issue an isolated HTML capability after the caller authorizes reading."""

    book_id = str(manifest.get("book_id") or "")
    html_paths = html_book_paths(manifest)
    if html_paths is None:
        abort(404)
    if not html_content_origin_is_isolated():
        app.logger.error("HTML 전자책 콘텐츠 호스트가 프론트 호스트와 분리되지 않았습니다.")
        abort(503)
    _html_root, _html_entry, entry_relative = html_paths
    content_version = html_content_version(manifest)
    if content_version is None:
        abort(404)
    capability_ttl = HTML_CONTENT_TOKEN_TTL_SECONDS if ttl_seconds is None else max(
        HTML_CONTENT_TOKEN_MIN_TTL_SECONDS,
        min(int(ttl_seconds), HTML_CONTENT_TOKEN_MAX_TTL_SECONDS),
    )
    expires_at = int(time.time()) + capability_ttl
    token = html_content_token(book_id, expires_at, content_version)
    content_path = url_for(
        "html_book_asset",
        book_id=book_id,
        expires_at=expires_at,
        token=token,
        asset_path=entry_relative.as_posix(),
    )
    response = app.make_response(
        render_template(
            "html_reader.html",
            book=manifest,
            html_src=f"{configured_origin(HTML_CONTENT_ORIGIN, 'https://html.epub.xsw.kr')}{content_path}",
        )
    )
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@app.get("/books/<book_id>/html")
def read_html_book(book_id: str):
    """로그인한 독자에게만 격리 HTML 리더 셸을 제공한다."""

    manifest = read_manifest(book_id)
    if not manifest:
        abort(404)
    return render_isolated_html_reader(manifest)


@app.get("/books/<book_id>/read")
def read_book(book_id: str):
    manifest = read_manifest(book_id)
    if not manifest:
        abort(404)
    if manifest.get("publication_type") == "html" and html_book_paths(manifest) is not None:
        return redirect(url_for("read_html_book", book_id=book_id))
    pdf_path = safe_book_asset(manifest, "pdf_path")
    if pdf_path:
        try:
            page_count = pdf_page_count(pdf_path)
        except PDFRenderingError as exc:
            app.logger.warning("PDF 리더 초기화 실패(%s): %s", book_id, exc)
        else:
            if page_count > 0:
                return render_template(
                    "pdf_reader.html",
                    book=manifest,
                    page_count=page_count,
                    thumbnail_page_count=min(page_count, PDF_THUMBNAIL_LIMIT),
                )

    source_path = Path(manifest["source_path"])
    if not source_path.exists() or BOOK_DIR not in source_path.resolve().parents:
        abort(404)
    text = source_path.read_text(encoding="utf-8")
    chapters = split_chapters(text)
    pages, chapter_starts = build_reader_pages(manifest, chapters)
    return render_template("reader.html", book=manifest, chapters=chapters, pages=pages, chapter_starts=chapter_starts)


@app.get(
    "/html-content/<book_id>/<int:expires_at>/<token>/",
    defaults={"asset_path": ""},
)
@app.get("/html-content/<book_id>/<int:expires_at>/<token>/<path:asset_path>")
def html_book_asset(book_id: str, expires_at: int, token: str, asset_path: str):
    """세션과 분리된 전용 호스트에서 capability로만 HTML 책 파일을 제공한다."""
    if not is_html_content_host():
        return html_content_error(404)
    manifest = read_manifest(book_id)
    if not valid_html_content_token(book_id, expires_at, token, manifest):
        return html_content_error(404)
    path = safe_html_content_asset(manifest, asset_path)
    if path is None:
        return html_content_error(404)

    suffix = path.suffix.casefold()
    mimetype = HTML_CONTENT_MIMETYPES.get(suffix)
    if suffix in HTML_CONTENT_TEXT_EXTENSIONS:
        try:
            payload = path.read_bytes()
        except OSError:
            return html_content_error(404)
        if len(payload) > HTML_CONTENT_TEXT_MAX_BYTES:
            return html_content_error(422)
        decoded = decode_html_text_asset(payload)
        if decoded is None:
            return html_content_error(422)
        response = app.response_class(decoded, status=200, content_type=mimetype)
    else:
        mimetype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = send_file(path, mimetype=mimetype, conditional=False, max_age=0)
    return apply_html_content_headers(response)


@app.get("/books/<book_id>/pdf")
def view_original_pdf(book_id: str):
    manifest = read_manifest(book_id)
    path = safe_book_asset(manifest, "pdf_path") if manifest else None
    if not manifest or not path:
        abort(404)
    filename = f"{safe_filename(str(manifest.get('title') or 'book'))}.pdf"
    response = send_file(
        path,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=filename,
        conditional=True,
    )
    response.cache_control.public = False
    response.cache_control.private = True
    return response


@app.get("/books/<book_id>/pdf/pages/<int:page_number>")
def pdf_page_image(book_id: str, page_number: int):
    manifest = read_manifest(book_id)
    path = safe_book_asset(manifest, "pdf_path") if manifest else None
    if not manifest or not path:
        abort(404)
    variant = request.args.get("variant", "screen")
    try:
        image_path = render_pdf_page(path, path.parent / ".pdf-page-cache", page_number, variant)
    except ValueError:
        abort(404)
    except PDFRenderingError as exc:
        app.logger.warning("PDF 페이지 렌더링 실패(%s/%s): %s", book_id, page_number, exc)
        abort(422)
    response = send_file(image_path, mimetype="image/png", conditional=True, max_age=86_400)
    response.cache_control.public = False
    response.cache_control.private = True
    return response


@app.get("/covers/<book_id>")
def book_cover(book_id: str):
    manifest = read_manifest(book_id)
    if not manifest:
        abort(404)
    try:
        path = ensure_book_cover(manifest)
    except Exception as exc:
        app.logger.warning("표지 생성 실패(%s): %s", book_id, exc)
        abort(404)
    response = send_file(path, mimetype="image/png", conditional=True, max_age=3600)
    response.cache_control.public = False
    response.cache_control.private = True
    return response


def catalog_payload(manifest: dict) -> dict:
    """Build the complete public contract from an allowlist, never the manifest."""

    commerce = normalize_commerce(manifest.get("commerce"))
    public_commerce = {
        "sale_status": commerce["sale_status"],
        "access": commerce["access"],
        "price_krw": commerce["price_krw"],
        "sample_pages": commerce["sample_pages"],
        "consultation_benefit": dict(commerce["consultation_benefit"]),
    }
    book_id = str(manifest["book_id"])
    public_origin = configured_origin(PUBLIC_ORIGIN, "https://epub.xsw.kr")
    return {
        "id": book_id,
        "title": str(manifest.get("title") or "제목 없는 책"),
        "author": str(manifest.get("author") or "저자 미상"),
        "description": str(manifest.get("description") or ""),
        "cover_url": f"{public_origin}{url_for('catalog_book_cover', book_id=book_id)}",
        "sample_url": f"{public_origin}/books/{book_id}/sample",
        "format": str(manifest.get("publication_type") or "text"),
        "status": commerce["sale_status"],
        "commerce": public_commerce,
    }


def public_catalog_response(payload, status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "public, max-age=60"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/api/catalog/books")
def catalog_books():
    books = [
        catalog_payload(book)
        for book in list_books(limit=None)
        if book["commerce"]["sale_status"] == "published"
    ]
    return public_catalog_response({"books": books, "count": len(books)})


@app.get("/api/catalog/books/<book_id>")
def catalog_book(book_id: str):
    manifest = read_manifest(book_id)
    if not manifest or manifest["commerce"]["sale_status"] != "published":
        abort(404)
    return public_catalog_response(catalog_payload(manifest))


@app.get("/api/catalog/books/<book_id>/cover")
def catalog_book_cover(book_id: str):
    manifest = read_manifest(book_id)
    # A previously public cover may already be cached/bookmarked. Keeping the
    # cover available for archived owned books does not expose the manuscript;
    # private drafts still fail closed.
    if not manifest or manifest["commerce"]["sale_status"] not in {"published", "archived"}:
        abort(404)
    path = safe_book_asset(manifest, "cover_path")
    if path is None:
        try:
            path = ensure_book_cover(manifest)
        except Exception as exc:
            app.logger.warning("공개 카탈로그 표지 생성 실패(%s): %s", book_id, type(exc).__name__)
            abort(404)
    response = send_file(path, conditional=True, max_age=3600)
    response.cache_control.public = True
    response.cache_control.private = False
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def published_public_manifest(book_id: str) -> dict | None:
    """Return only an explicitly published manifest for anonymous readers."""

    manifest = read_manifest(book_id)
    if not manifest or manifest["commerce"]["sale_status"] != "published":
        return None
    return manifest


def entitled_reader_manifest(book_id: str) -> dict | None:
    """Published and archived books remain readable by existing purchasers."""

    manifest = read_manifest(book_id)
    if not manifest or manifest["commerce"]["sale_status"] not in {"published", "archived"}:
        return None
    return manifest


def safe_ocr_pages_path(manifest: dict) -> tuple[Path, Path] | None:
    """Locate the canonical OCR JSON inside an isolated HTML publication."""

    html_paths = html_book_paths(manifest)
    if html_paths is None:
        return None
    html_root, _html_entry, _entry_relative = html_paths
    candidate = html_root / "ocr_pages.json"
    if candidate.is_symlink():
        return None
    try:
        resolved = candidate.resolve(strict=True)
        size = resolved.stat().st_size
    except OSError:
        return None
    if resolved.parent != html_root or not resolved.is_file() or size > MAX_PUBLIC_OCR_JSON_BYTES:
        return None
    return html_root, resolved


def normalized_ocr_pages(manifest: dict) -> list[dict]:
    """Read the skill-generated OCR contract without trusting paths or markup."""

    located = safe_ocr_pages_path(manifest)
    if located is None:
        return []
    html_root, data_path = located
    try:
        raw_pages = json.loads(data_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(raw_pages, list):
        return []

    pages: list[dict] = []
    seen: set[int] = set()
    for raw in raw_pages[:MAX_PUBLIC_READER_PAGES]:
        if not isinstance(raw, dict):
            continue
        page_number = raw.get("page")
        if isinstance(page_number, bool):
            continue
        try:
            page_number = int(page_number)
        except (TypeError, ValueError, OverflowError):
            continue
        if page_number < 1 or page_number > MAX_PUBLIC_READER_PAGES or page_number in seen:
            continue
        seen.add(page_number)

        title = str(raw.get("title") or f"{page_number}쪽").strip()[:300]
        raw_paragraphs = raw.get("paragraphs")
        paragraphs: list[str] = []
        if isinstance(raw_paragraphs, list):
            for paragraph in raw_paragraphs[:500]:
                if not isinstance(paragraph, str):
                    continue
                cleaned = paragraph.strip()
                if cleaned:
                    paragraphs.append(cleaned[:20_000])
        if not paragraphs and isinstance(raw.get("text"), str):
            paragraphs = [part.strip()[:20_000] for part in raw["text"].split("\n\n") if part.strip()][:500]

        image_path: Path | None = None
        image_value = raw.get("image")
        if isinstance(image_value, str) and image_value and "\\" not in image_value and "\x00" not in image_value:
            relative = PurePosixPath(image_value)
            if (
                not relative.is_absolute()
                and all(part not in {"", ".", ".."} for part in relative.parts)
                and relative.suffix.casefold() in PUBLIC_SCAN_EXTENSIONS
            ):
                candidate = html_root.joinpath(*relative.parts)
                try:
                    resolved = candidate.resolve(strict=True)
                except OSError:
                    resolved = None
                if (
                    resolved is not None
                    and html_root in resolved.parents
                    and resolved.is_file()
                    and not candidate.is_symlink()
                ):
                    image_path = resolved

        pages.append(
            {
                "page": page_number,
                "title": title,
                "paragraphs": paragraphs,
                "image_path": image_path,
                "kind": "ocr",
            }
        )
    pages.sort(key=lambda page: page["page"])
    return pages


def text_public_pages(manifest: dict) -> list[dict]:
    """Fallback for TXT/Markdown/EPUB publications that have extracted text."""

    source_path = safe_book_asset(manifest, "source_path")
    if source_path is None:
        return []
    try:
        if source_path.stat().st_size > MAX_PUBLIC_OCR_JSON_BYTES:
            return []
        text = source_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return []
    chapters = split_chapters(text)
    reader_pages, _starts = build_reader_pages(manifest, chapters)
    pages: list[dict] = []
    for page_number, page in enumerate(reader_pages[1:MAX_PUBLIC_READER_PAGES + 1], start=1):
        pages.append(
            {
                "page": page_number,
                "title": str(page.get("title") or page.get("runningTitle") or f"{page_number}쪽")[:300],
                "paragraphs": [str(value)[:20_000] for value in page.get("paragraphs", []) if str(value).strip()],
                "image_path": None,
                "kind": "text",
            }
        )
    return pages


def public_book_page_source(manifest: dict) -> tuple[str, int, list[dict]]:
    """Return format, total page count, and safe page records."""

    pdf_path = safe_book_asset(manifest, "pdf_path")
    if pdf_path is not None:
        try:
            total = min(pdf_page_count(pdf_path), MAX_PUBLIC_READER_PAGES)
        except PDFRenderingError:
            total = 0
        return "pdf", total, []
    pages = normalized_ocr_pages(manifest)
    if pages:
        return "ocr", len(pages), pages
    pages = text_public_pages(manifest)
    return "text", len(pages), pages


def public_visible_page_count(manifest: dict, total_pages: int) -> int:
    commerce = manifest["commerce"]
    if commerce["access"] == "free":
        return total_pages
    return min(total_pages, commerce["sample_pages"])


def apply_public_reader_headers(response):
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'none'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'self' https://saju.xsw.kr"
    )
    return response


@app.get("/books/<book_id>/sample")
def public_book_sample(book_id: str):
    """Serve a separately constructed preview; never iframe the paid HTML."""

    manifest = published_public_manifest(book_id)
    if manifest is None:
        abort(404)
    if (
        manifest["commerce"]["access"] == "free"
        and manifest.get("publication_type") == "html"
        and html_book_paths(manifest) is not None
    ):
        return render_isolated_html_reader(manifest)
    source_kind, total_pages, source_pages = public_book_page_source(manifest)
    if total_pages < 1:
        abort(404)
    visible_pages = public_visible_page_count(manifest, total_pages)
    if visible_pages < 1:
        abort(404)

    if source_kind == "pdf":
        pages = [
            {
                "page": page_number,
                "title": f"{page_number}쪽",
                "paragraphs": [],
                "kind": "pdf",
                "image_url": url_for("public_book_sample_page", book_id=book_id, page_number=page_number),
            }
            for page_number in range(1, visible_pages + 1)
        ]
        format_label = "PDF 원본"
    else:
        pages = []
        for record in source_pages[:visible_pages]:
            pages.append(
                {
                    "page": record["page"],
                    "title": record["title"],
                    "paragraphs": record["paragraphs"],
                    "kind": record["kind"],
                    "image_url": (
                        url_for("public_book_sample_page", book_id=book_id, page_number=record["page"])
                        if record["image_path"] is not None
                        else ""
                    ),
                }
            )
        format_label = "OCR 원문 대조" if source_kind == "ocr" else "전자책 본문"

    is_full = manifest["commerce"]["access"] == "free"
    response = app.make_response(
        render_template(
            "public_sample.html",
            book=manifest,
            pages=pages,
            total_pages=total_pages,
            visible_pages=visible_pages,
            is_full=is_full,
            format_label=format_label,
            cover_url=url_for("catalog_book_cover", book_id=book_id),
            purchase_url=(
                "" if is_full else f"{configured_origin(SAJU_LIBRARY_ORIGIN, 'https://saju.xsw.kr')}/books/{quote(book_id, safe='')}"
            ),
        )
    )
    return apply_public_reader_headers(response)


@app.get("/books/<book_id>/sample/pages/<int:page_number>")
def public_book_sample_page(book_id: str, page_number: int):
    """Serve only an image belonging to the server-authorized preview range."""

    manifest = published_public_manifest(book_id)
    if manifest is None:
        abort(404)
    source_kind, total_pages, source_pages = public_book_page_source(manifest)
    visible_pages = public_visible_page_count(manifest, total_pages)
    if page_number < 1 or page_number > visible_pages:
        abort(404)

    if source_kind == "pdf":
        pdf_path = safe_book_asset(manifest, "pdf_path")
        if pdf_path is None:
            abort(404)
        try:
            image_path = render_pdf_page(
                pdf_path,
                pdf_path.parent / ".pdf-page-cache",
                page_number,
                "screen",
            )
        except ValueError:
            abort(404)
        except PDFRenderingError as exc:
            app.logger.warning("공개 PDF 샘플 렌더링 실패(%s/%s): %s", book_id, page_number, exc)
            abort(422)
    else:
        record = next((page for page in source_pages[:visible_pages] if page["page"] == page_number), None)
        image_path = record.get("image_path") if record else None
        if not isinstance(image_path, Path):
            abort(404)

    response = send_file(image_path, conditional=False, max_age=0)
    return apply_public_reader_headers(response)


@app.post("/library/access")
def exchange_ebook_access():
    """Exchange a short Saju entitlement ticket for a book-scoped reader cookie."""

    ticket = request.form.get("ticket", "")
    payload = verify_ebook_access_ticket(ticket)
    if payload is None:
        abort(404)
    book_id = payload["bookId"]
    manifest = entitled_reader_manifest(book_id)
    if manifest is None:
        abort(404)
    if not consume_ebook_access_nonce(payload):
        abort(404)
    grant = create_ebook_reader_grant(payload)
    if grant is None:
        abort(503)
    target = url_for("public_full_book", book_id=book_id)
    response = redirect(target)
    response.set_cookie(
        EBOOK_READER_COOKIE,
        grant,
        max_age=EBOOK_READER_GRANT_TTL_SECONDS,
        secure=True,
        httponly=True,
        samesite="Lax",
        path=f"/library/books/{book_id}",
    )
    return apply_public_reader_headers(response)


def paid_reader_is_authorized(manifest: dict) -> bool:
    commerce = manifest["commerce"]
    return (
        commerce["sale_status"] == "published" and commerce["access"] == "free"
    ) or valid_ebook_reader_grant(manifest["book_id"])


@app.get("/library/books/<book_id>/read")
def public_full_book(book_id: str):
    manifest = entitled_reader_manifest(book_id)
    if manifest is None:
        abort(404)
    if not paid_reader_is_authorized(manifest):
        abort(403)
    if manifest.get("publication_type") == "html" and html_book_paths(manifest) is not None:
        return render_isolated_html_reader(manifest, ttl_seconds=EBOOK_READER_GRANT_TTL_SECONDS)
    source_kind, total_pages, source_pages = public_book_page_source(manifest)
    if total_pages < 1:
        abort(404)

    if source_kind == "pdf":
        pages = [
            {
                "page": page_number,
                "title": f"{page_number}쪽",
                "paragraphs": [],
                "kind": "pdf",
                "image_url": url_for("public_full_book_page", book_id=book_id, page_number=page_number),
            }
            for page_number in range(1, total_pages + 1)
        ]
        format_label = "PDF 원본"
    else:
        pages = [
            {
                "page": record["page"],
                "title": record["title"],
                "paragraphs": record["paragraphs"],
                "kind": record["kind"],
                "image_url": (
                    url_for("public_full_book_page", book_id=book_id, page_number=record["page"])
                    if record["image_path"] is not None
                    else ""
                ),
            }
            for record in source_pages
        ]
        format_label = "OCR 원문 대조" if source_kind == "ocr" else "전자책 본문"

    response = app.make_response(
        render_template(
            "public_sample.html",
            book=manifest,
            pages=pages,
            total_pages=total_pages,
            visible_pages=total_pages,
            is_full=True,
            format_label=format_label,
            cover_url=url_for("catalog_book_cover", book_id=book_id),
            purchase_url="",
        )
    )
    return apply_public_reader_headers(response)


@app.get("/library/books/<book_id>/pages/<int:page_number>")
def public_full_book_page(book_id: str, page_number: int):
    manifest = entitled_reader_manifest(book_id)
    if manifest is None:
        abort(404)
    if not paid_reader_is_authorized(manifest):
        abort(403)
    source_kind, total_pages, source_pages = public_book_page_source(manifest)
    if page_number < 1 or page_number > total_pages:
        abort(404)

    if source_kind == "pdf":
        pdf_path = safe_book_asset(manifest, "pdf_path")
        if pdf_path is None:
            abort(404)
        try:
            image_path = render_pdf_page(
                pdf_path,
                pdf_path.parent / ".pdf-page-cache",
                page_number,
                "screen",
            )
        except ValueError:
            abort(404)
        except PDFRenderingError as exc:
            app.logger.warning("구매자 PDF 렌더링 실패(%s/%s): %s", book_id, page_number, exc)
            abort(422)
    else:
        record = next((page for page in source_pages if page["page"] == page_number), None)
        image_path = record.get("image_path") if record else None
        if not isinstance(image_path, Path):
            abort(404)
    response = send_file(image_path, conditional=False, max_age=0)
    return apply_public_reader_headers(response)


@app.get("/download/<book_id>/<kind>")
def download(book_id: str, kind: str):
    manifest = read_manifest(book_id)
    asset_keys = {
        "epub": "epub_path",
        "pdf": "pdf_path",
        "markdown": "markdown_path",
        "source": "source_path",
        "html": "html_archive_path",
    }
    key = asset_keys.get(kind)
    if not manifest or key is None:
        abort(404)
    path = safe_book_asset(manifest, key)
    if path is None:
        abort(404)
    title = safe_filename(str(manifest.get("title") or "book")) or "book"
    suffix = ".zip" if kind == "html" else path.suffix
    filename = f"{title}-html{suffix}" if kind == "html" else f"{title}{suffix}"
    return send_file(path, as_attachment=True, download_name=filename)


@app.get("/help")
def help_page():
    return render_template("help.html")


@app.get("/settings")
def settings():
    if not is_admin_user():
        abort(403)
    site_settings = load_site_settings()
    return render_template(
        "settings.html",
        allowed_users=", ".join(sorted(ALLOWED_USERS)),
        upload_mb=configured_upload_mb(site_settings),
        upload_min_mb=MIN_UPLOAD_MB,
        upload_max_mb=MAX_CONFIG_UPLOAD_MB,
        ai_cover_models=AI_COVER_MODELS,
        ai_cover_qualities=AI_COVER_QUALITIES,
        ai_cover_available=bool(os.getenv("OPENAI_API_KEY")),
        book_dir=str(BOOK_DIR),
        commerce_books=list_books(limit=None),
        legacy_repo="https://github.com/hojel/epubia",
    )


@app.post("/settings/publishing")
def update_publishing_settings():
    if not is_admin_user():
        abort(403)
    if not valid_csrf_submission():
        abort(400)

    try:
        upload_limit = int(request.form.get("upload_limit_mb", ""))
    except ValueError:
        upload_limit = 0
    if not MIN_UPLOAD_MB <= upload_limit <= MAX_CONFIG_UPLOAD_MB:
        flash(f"업로드 한도는 {MIN_UPLOAD_MB}~{MAX_CONFIG_UPLOAD_MB}MB 사이로 입력해 주세요.", "error")
        return redirect(url_for("settings") + "#publishing-settings")

    model = request.form.get("ai_cover_model", "").strip()
    quality = request.form.get("ai_cover_quality", "").strip().lower()
    if model not in AI_COVER_MODELS or quality not in AI_COVER_QUALITIES:
        flash("지원하는 OpenAI 이미지 모델과 품질을 선택해 주세요.", "error")
        return redirect(url_for("settings") + "#publishing-settings")

    current = load_site_settings()
    current["upload_limit_mb"] = str(upload_limit)
    current["ai_cover_enabled"] = "1" if request.form.get("ai_cover_enabled") == "1" else "0"
    current["ai_cover_model"] = model
    current["ai_cover_quality"] = quality
    save_site_settings(current)
    flash("출판 설정을 저장했습니다. 다음 업로드부터 바로 적용됩니다.", "success")
    return redirect(url_for("settings") + "#publishing-settings")


@app.post("/settings/appearance")
def update_appearance():
    if not is_admin_user():
        abort(403)
    supplied_token = request.form.get("csrf_token", "")
    if not supplied_token or not hmac.compare_digest(supplied_token, session.get("_csrf_token", "")):
        abort(400)

    current = load_site_settings()
    for key, limit in SITE_SETTING_LIMITS.items():
        value = request.form.get(key, "").strip()
        if not value and key not in OPTIONAL_SITE_SETTINGS:
            flash("서비스명과 화면 문구는 비워둘 수 없습니다.", "error")
            return redirect(url_for("settings") + "#appearance-settings")
        current[key] = value[:limit]

    for key, label in (("accent_color", "강조색"), ("primary_color", "버튼색")):
        value = request.form.get(key, "").strip()
        if not valid_hex_color(value):
            flash(f"{label}은 #49d3c4 형식의 색상값으로 입력해 주세요.", "error")
            return redirect(url_for("settings") + "#appearance-settings")
        current[key] = value.lower()

    save_site_settings(current)
    flash("프론트 화면 설정을 저장했습니다.", "success")
    return redirect(url_for("settings") + "#appearance-settings")


def commerce_settings_redirect():
    return redirect(url_for("settings") + "#book-commerce")


def book_commerce_lock_path(book_id: str) -> Path:
    return manifest_path(book_id).parent / ".commerce.lock"


@app.post("/settings/books/<book_id>/commerce")
def update_book_commerce(book_id: str):
    if not is_admin_user():
        abort(403)
    if not valid_csrf_submission():
        abort(400)

    manifest = read_manifest(book_id)
    if not manifest:
        abort(404)
    try:
        commerce = commerce_from_form(manifest.get("commerce"))
    except CommerceValidationError as exc:
        flash(str(exc), "error")
        return commerce_settings_redirect()

    with try_advisory_lock(book_commerce_lock_path(book_id)) as lock:
        if lock is None:
            flash("다른 관리자가 이 책을 수정하고 있습니다. 잠시 후 다시 시도해 주세요.", "error")
            return commerce_settings_redirect()
        latest = read_manifest(book_id)
        if not latest:
            abort(404)
        latest["commerce"] = commerce
        write_json_atomic(manifest_path(book_id), latest)

    flash(f"‘{manifest.get('title', '전자책')}’ 판매 설정을 저장했습니다.", "success")
    return commerce_settings_redirect()


def change_book_archive_status(book_id: str, *, restore: bool):
    if not is_admin_user():
        abort(403)
    if not valid_csrf_submission():
        abort(400)
    # Validate the identifier before deriving a lock path from it. In
    # particular, ".." must not create a lock outside BOOK_DIR.
    if not read_manifest(book_id):
        abort(404)

    with try_advisory_lock(book_commerce_lock_path(book_id)) as lock:
        if lock is None:
            flash("다른 관리자가 이 책을 수정하고 있습니다. 잠시 후 다시 시도해 주세요.", "error")
            return commerce_settings_redirect()
        manifest = read_manifest(book_id)
        if not manifest:
            abort(404)
        commerce = normalize_commerce(manifest.get("commerce"))
        if restore:
            target = commerce.get("sale_status_before_archive", "private")
            commerce["sale_status"] = target if target in {"private", "published"} else "private"
            commerce.pop("sale_status_before_archive", None)
            notice = "보관을 해제했습니다."
        else:
            current = commerce.get("sale_status", "private")
            if current != "archived":
                commerce["sale_status_before_archive"] = current if current in {"private", "published"} else "private"
            commerce["sale_status"] = "archived"
            notice = "파일은 보존하고 서재에서 보관 처리했습니다."
        manifest["commerce"] = commerce
        write_json_atomic(manifest_path(book_id), manifest)

    flash(f"‘{manifest.get('title', '전자책')}’ {notice}", "success")
    return commerce_settings_redirect()


@app.post("/settings/books/<book_id>/archive")
def archive_book(book_id: str):
    return change_book_archive_status(book_id, restore=False)


@app.post("/settings/books/<book_id>/restore")
def restore_book(book_id: str):
    return change_book_archive_status(book_id, restore=True)


def configured_origin(value: str, fallback: str) -> str:
    """CSP/Host 비교에 쓸 스킴+호스트만 반환한다."""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return fallback
    return f"{parsed.scheme}://{parsed.netloc}"


def safe_local_next_url(value: str | None) -> str:
    """Return a same-site absolute path suitable for a post-login redirect."""

    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate or any(ord(character) < 32 for character in candidate):
        return ""
    try:
        decoded = unquote(candidate)
        parsed = urlsplit(candidate)
    except (TypeError, ValueError, UnicodeError):
        return ""
    if (
        parsed.scheme
        or parsed.netloc
        or not parsed.path.startswith("/")
        or candidate.startswith("//")
        or decoded.startswith("//")
        or "\\" in decoded
        or any(ord(character) < 32 for character in decoded)
    ):
        return ""
    return candidate


def html_content_origin_is_isolated() -> bool:
    """Fail closed if a configuration typo collapses the HTML trust boundary."""

    public = urlsplit(configured_origin(PUBLIC_ORIGIN, "https://epub.xsw.kr"))
    content = urlsplit(configured_origin(HTML_CONTENT_ORIGIN, "https://html.epub.xsw.kr"))
    public_host = (public.hostname or "").rstrip(".").casefold()
    content_host = (content.hostname or "").rstrip(".").casefold()
    return bool(public_host and content_host and public_host != content_host)


def is_html_content_host() -> bool:
    if not html_content_origin_is_isolated():
        return False
    expected = urlsplit(configured_origin(HTML_CONTENT_ORIGIN, "https://html.epub.xsw.kr")).netloc
    return bool(expected) and request.host.casefold() == expected.casefold()


def html_content_version(manifest: dict | None) -> str | None:
    """Return the stable publication version bound into HTML capabilities."""

    if not manifest:
        return None
    configured = manifest.get("html_content_version")
    if isinstance(configured, str) and re.fullmatch(r"[0-9a-f]{32,64}", configured):
        return configured

    # Legacy manifests predate the explicit random version. Bind their tokens
    # to the entry/archive metadata so replacing the published files revokes an
    # already issued capability without hashing a large archive on every asset.
    html_paths = html_book_paths(manifest)
    if html_paths is None:
        return None
    html_root, html_entry, entry_relative = html_paths
    try:
        entry_stat = html_entry.stat()
    except OSError:
        return None
    material = [
        str(manifest.get("book_id", "")),
        entry_relative.as_posix(),
        str(entry_stat.st_size),
        str(entry_stat.st_mtime_ns),
        str(html_root),
    ]
    archive = safe_book_asset(manifest, "html_archive_path")
    if archive is not None:
        try:
            archive_stat = archive.stat()
        except OSError:
            return None
        material.extend((str(archive_stat.st_size), str(archive_stat.st_mtime_ns)))
    return hashlib.sha256("\x1f".join(material).encode("utf-8")).hexdigest()


def html_content_token(book_id: str, expires_at: int, content_version: str) -> str:
    secret = app.config["SECRET_KEY"]
    if not isinstance(secret, bytes):
        secret = str(secret).encode("utf-8")
    message = f"html-book:v2:{book_id}:{content_version}:{expires_at}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def valid_html_content_token(
    book_id: str,
    expires_at: int,
    supplied: str,
    manifest: dict | None,
    *,
    now: int | None = None,
) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", book_id or "") or ".." in book_id:
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", supplied or ""):
        return False
    if not isinstance(expires_at, int):
        return False
    current_time = int(time.time()) if now is None else int(now)
    if expires_at < current_time or expires_at > current_time + HTML_CONTENT_TOKEN_MAX_TTL_SECONDS:
        return False
    content_version = html_content_version(manifest)
    if content_version is None:
        return False
    expected = html_content_token(book_id, expires_at, content_version)
    return hmac.compare_digest(supplied, expected)


def book_root_for_manifest(manifest: dict | None) -> Path | None:
    if not manifest:
        return None
    book_id = manifest.get("book_id")
    if not isinstance(book_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", book_id) or ".." in book_id:
        return None
    candidate = BOOK_DIR / book_id
    if candidate.is_symlink():
        return None
    try:
        root = candidate.resolve(strict=True)
        library_root = BOOK_DIR.resolve(strict=True)
    except OSError:
        return None
    if root.parent != library_root or not root.is_dir() or root.is_symlink():
        return None
    return root


def html_book_paths(manifest: dict | None) -> tuple[Path, Path, PurePosixPath] | None:
    """검증된 HTML 루트, 진입 파일, 루트 기준 상대 경로를 반환한다."""
    book_root = book_root_for_manifest(manifest)
    if book_root is None or manifest is None:
        return None

    html_path_value = manifest.get("html_path")
    root_value = manifest.get("html_root") or manifest.get("html_root_path")
    entry_value = manifest.get("html_entry")
    configured_candidate = Path(html_path_value) if isinstance(html_path_value, str) and html_path_value else None
    root_candidate = Path(root_value) if isinstance(root_value, str) and root_value else None
    if (configured_candidate is not None and configured_candidate.is_symlink()) or (
        root_candidate is not None and root_candidate.is_symlink()
    ):
        return None
    try:
        configured_path = configured_candidate.resolve(strict=True) if configured_candidate is not None else None
        if root_candidate is not None:
            html_root = root_candidate.resolve(strict=True)
        elif configured_path is not None:
            html_root = configured_path if configured_path.is_dir() else configured_path.parent
        else:
            return None
    except OSError:
        return None

    # HTML 파일은 반드시 해당 책의 전용 하위 폴더에서만 제공한다. 책 루트를
    # 허용하면 manifest.json 같은 내부 메타데이터가 노출될 수 있다.
    if html_root == book_root or book_root not in html_root.parents or not html_root.is_dir() or html_root.is_symlink():
        return None

    if isinstance(entry_value, str) and entry_value:
        if "\\" in entry_value or "\x00" in entry_value:
            return None
        entry_relative = PurePosixPath(entry_value)
        if entry_relative.is_absolute() or any(part in {"", ".", ".."} for part in entry_relative.parts):
            return None
        entry_candidate = html_root.joinpath(*entry_relative.parts)
    elif configured_path is not None and configured_path.is_file():
        try:
            entry_relative = PurePosixPath(configured_path.relative_to(html_root).as_posix())
        except ValueError:
            return None
        entry_candidate = configured_path
    else:
        entry_relative = PurePosixPath("index.html")
        entry_candidate = html_root / "index.html"

    try:
        html_entry = entry_candidate.resolve(strict=True)
    except OSError:
        return None
    if (
        html_root not in html_entry.parents
        or not html_entry.is_file()
        or entry_candidate.is_symlink()
        or html_entry.suffix.casefold() not in {".html", ".htm"}
    ):
        return None
    return html_root, html_entry, entry_relative


def safe_html_content_asset(manifest: dict | None, asset_path: str) -> Path | None:
    html_paths = html_book_paths(manifest)
    if html_paths is None:
        return None
    html_root, _html_entry, entry_relative = html_paths
    if not asset_path:
        relative = entry_relative
    else:
        if "\\" in asset_path or "\x00" in asset_path:
            return None
        relative = PurePosixPath(asset_path)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            return None
    if relative.suffix.casefold() not in HTML_CONTENT_EXTENSIONS:
        return None

    candidate = html_root.joinpath(*relative.parts)
    current = html_root
    try:
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return None
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if html_root not in resolved.parents or not resolved.is_file():
        return None
    return resolved


def decode_html_text_asset(payload: bytes) -> str | None:
    """한국어 로컬 HTML 패키지를 브라우저용 UTF-8로 안전하게 정규화한다."""
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def apply_html_content_headers(response):
    parent_origin = configured_origin(PUBLIC_ORIGIN, "https://epub.xsw.kr")
    response.headers["Content-Security-Policy"] = (
        "sandbox allow-scripts; "
        "default-src 'none'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "media-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "frame-src 'none'; "
        "worker-src 'none'; "
        "form-action 'none'; "
        "base-uri 'none'; "
        f"frame-ancestors {parent_origin}"
    )
    response.headers["Access-Control-Allow-Origin"] = "null"
    response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Permissions-Policy"] = (
        "accelerometer=(), autoplay=(), camera=(), geolocation=(), gyroscope=(), "
        "microphone=(), payment=(), usb=()"
    )
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def html_content_error(status: int):
    response = app.response_class("", status=status, mimetype="text/plain")
    return apply_html_content_headers(response)


def manifest_path(book_id: str) -> Path:
    return BOOK_DIR / book_id / "manifest.json"


def safe_book_asset(manifest: dict | None, key: str) -> Path | None:
    book_root = book_root_for_manifest(manifest)
    if not manifest or book_root is None:
        return None
    value = manifest.get(key)
    if not isinstance(value, str) or not value:
        return None
    candidate = Path(value)
    if candidate.is_symlink():
        return None
    try:
        path = candidate.resolve(strict=True)
    except OSError:
        return None
    if not path.is_file() or book_root not in path.parents or path.is_symlink():
        return None
    return path


def write_manifest(result, meta: BookMeta | None = None) -> None:
    cover_path = getattr(result, "cover_path", manifest_path(result.book_id).parent / "cover.png")
    publication_type = str(getattr(result, "publication_type", "text") or "text")
    payload = {
        "book_id": result.book_id,
        "title": result.title,
        "author": result.author,
        "chapter_count": result.chapter_count,
        "created_at": result.created_at,
        "source_path": str(result.source_text_path),
        "markdown_path": str(result.markdown_path),
        "cover_path": str(cover_path),
        "cover_mode": getattr(result, "cover_mode", "template"),
        "publication_type": publication_type,
        "commerce": normalize_commerce(None),
    }
    for key in ("subtitle", "description", "publisher"):
        value = getattr(meta, key, None) if meta is not None else getattr(result, key, None)
        if value:
            payload[key] = str(value)
    for key in ("epub_path", "pdf_path"):
        value = getattr(result, key, None)
        if value is not None:
            payload[key] = str(value)
    html_path = getattr(result, "html_path", None)
    html_archive_path = getattr(result, "html_archive_path", None)
    if html_path is not None and html_archive_path is not None:
        html_path = Path(html_path)
        html_root = Path(getattr(result, "html_root", None) or html_path.parent)
        html_entry = getattr(result, "html_entry", None)
        if not html_entry:
            try:
                html_entry = html_path.relative_to(html_root).as_posix()
            except ValueError:
                html_entry = html_path.name
        payload.update(
            {
                "publication_type": "html",
                "has_html": True,
                "html_path": str(html_path),
                "html_root": str(html_root),
                "html_entry": str(html_entry),
                "html_archive_path": str(html_archive_path),
                "html_content_version": secrets.token_hex(16),
            }
        )
    write_json_atomic(manifest_path(result.book_id), payload)


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def ensure_book_cover(manifest: dict) -> Path:
    """기존 출판본도 요청 시 한글 안전 표지를 생성해 갤러리에 노출한다."""
    book_id = str(manifest.get("book_id", ""))
    if not book_id or "/" in book_id or "\\" in book_id or ".." in book_id:
        raise ValueError("올바르지 않은 책 식별자입니다.")
    book_dir = manifest_path(book_id).parent
    configured = manifest.get("cover_path")
    if configured:
        configured_path = Path(configured)
        if configured_path.is_file() and book_dir.resolve() in configured_path.resolve().parents:
            return configured_path

    cover_path = book_dir / "cover.png"
    if not cover_path.is_file():
        tmp_cover = book_dir / f".cover-{secrets.token_hex(4)}.png"
        try:
            create_cover(
                BookMeta(
                    title=str(manifest.get("title") or "제목 없는 책"),
                    author=str(manifest.get("author") or "저자 미상"),
                    publisher=str(manifest.get("publisher") or "혜경 전자책 스튜디오"),
                ),
                tmp_cover,
            )
            os.replace(tmp_cover, cover_path)
        finally:
            tmp_cover.unlink(missing_ok=True)

    # Cover generation can race with an administrator changing price/archive
    # state. Persist only onto a freshly re-read manifest under the same lock
    # used by commerce writes so a stale GET can never resurrect old settings.
    with try_advisory_lock(book_commerce_lock_path(book_id)) as lock:
        if lock is not None:
            latest = read_manifest(book_id)
            if latest is not None:
                latest["cover_path"] = str(cover_path)
                write_json_atomic(manifest_path(book_id), latest)
    return cover_path


def read_manifest(book_id: str) -> dict | None:
    if (
        not isinstance(book_id, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", book_id)
        or ".." in book_id
    ):
        return None
    path = manifest_path(book_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("book_id") != book_id:
        return None
    payload["commerce"] = normalize_commerce(payload.get("commerce"))
    return payload


def list_books(limit: int | None = 30) -> list[dict]:
    books: list[dict] = []
    for path in sorted(BOOK_DIR.glob("*/manifest.json"), reverse=True):
        try:
            book = read_manifest(path.parent.name)
            if book is None:
                continue
            created_at = datetime.fromisoformat(str(book.get("created_at", "")))
            book["is_new"] = datetime.now() - created_at <= timedelta(days=14)
            books.append(book)
        except Exception:
            continue
    return books if limit is None else books[: max(0, limit)]


def split_paragraph_for_pages(paragraph: str, max_chars: int = 180) -> list[str]:
    paragraph = " ".join(paragraph.split())
    if len(paragraph) <= max_chars:
        return [paragraph]
    sentences = [part.strip() for part in paragraph.replace("다. ", "다.\n").replace("요. ", "요.\n").splitlines()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(sentence[i:i + max_chars] for i in range(0, len(sentence), max_chars))
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
    target_chars = 180
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
