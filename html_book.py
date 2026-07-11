from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import unicodedata
import zipfile


class HTMLBookError(ValueError):
    """An HTML book archive cannot be published safely."""


@dataclass(frozen=True)
class HTMLBookLimits:
    max_files: int = 2_000
    max_members: int = 4_000
    max_path_bytes: int = 240
    max_path_depth: int = 20
    max_file_bytes: int = 50 * 1024 * 1024
    max_text_file_bytes: int = 10 * 1024 * 1024
    max_total_bytes: int = 300 * 1024 * 1024
    max_compression_ratio: float = 100.0
    stream_chunk_bytes: int = 64 * 1024
    max_visible_text_chars: int = 20_000

    def __post_init__(self) -> None:
        positive_fields = (
            self.max_files,
            self.max_members,
            self.max_path_bytes,
            self.max_path_depth,
            self.max_file_bytes,
            self.max_text_file_bytes,
            self.max_total_bytes,
            self.max_compression_ratio,
            self.stream_chunk_bytes,
        )
        if any(value <= 0 for value in positive_fields):
            raise ValueError("HTML 전자책 안전 제한값은 0보다 커야 합니다.")
        if self.max_visible_text_chars < 0:
            raise ValueError("HTML 본문 추출 글자 수는 음수일 수 없습니다.")


@dataclass(frozen=True)
class ExtractedHTMLBook:
    root: Path
    entrypoint: Path
    visible_text: str
    file_count: int
    total_bytes: int


_ALLOWED_EXTENSIONS = frozenset(
    {
        ".html",
        ".css",
        ".js",
        ".mjs",
        ".json",
        ".txt",
        ".md",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
    }
)

_TEXT_EXTENSIONS = frozenset({".html", ".css", ".js", ".mjs", ".json", ".txt", ".md"})
_SUPPORTED_COMPRESSION_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


@dataclass(frozen=True)
class _ArchiveMember:
    info: zipfile.ZipInfo
    path: PurePosixPath
    is_directory: bool


class _VisibleTextParser(HTMLParser):
    _NON_VISIBLE_TAGS = frozenset({"head", "script", "style", "template", "noscript"})
    _VOID_TAGS = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self._open_tags: list[tuple[str, bool]] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attr_map = {name.casefold(): value or "" for name, value in attrs}
        style = re.sub(r"\s+", "", attr_map.get("style", "").casefold())
        hidden = (
            tag in self._NON_VISIBLE_TAGS
            or "hidden" in attr_map
            or attr_map.get("aria-hidden", "").casefold() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )
        if tag in self._VOID_TAGS:
            return
        self._open_tags.append((tag, hidden))
        if hidden:
            self._hidden_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        matching_index = next(
            (
                index
                for index in range(len(self._open_tags) - 1, -1, -1)
                if self._open_tags[index][0] == tag
            ),
            None,
        )
        if matching_index is None:
            return
        closing = self._open_tags[matching_index:]
        del self._open_tags[matching_index:]
        self._hidden_depth -= sum(1 for _, hidden in closing if hidden)

    def handle_data(self, data: str) -> None:
        if self._hidden_depth == 0 and data.strip():
            self.parts.append(data)


def _decode_html(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _normalize_text_asset(payload: bytes) -> bytes:
    if b"\x00" in payload:
        raise HTMLBookError(
            "HTML 전자책의 텍스트 파일 인코딩은 UTF-8, CP949 또는 EUC-KR이어야 합니다."
        )
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return payload.decode(encoding).encode("utf-8")
        except UnicodeDecodeError:
            continue
    raise HTMLBookError(
        "HTML 전자책의 텍스트 파일 인코딩은 UTF-8, CP949 또는 EUC-KR이어야 합니다."
    )


def _visible_text(html: bytes) -> str:
    parser = _VisibleTextParser()
    parser.feed(_decode_html(html))
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def _normalized_path_key(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(unicodedata.normalize("NFKC", part).casefold() for part in parts)


def _validated_member(info: zipfile.ZipInfo) -> _ArchiveMember | None:
    name = info.filename
    if name == "__MACOSX" or name.startswith("__MACOSX/"):
        return None
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name)
    ):
        raise HTMLBookError("ZIP 내부에 안전하지 않은 파일 경로가 있습니다.")

    is_directory = info.is_dir()
    plain_name = name[:-1] if is_directory and name.endswith("/") else name
    parts = tuple(plain_name.split("/"))
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise HTMLBookError("ZIP 내부에 안전하지 않은 파일 경로가 있습니다.")
    if any(any(ord(character) < 32 for character in part) for part in parts):
        raise HTMLBookError("ZIP 내부에 안전하지 않은 파일 경로가 있습니다.")

    if info.flag_bits & 0x1:
        raise HTMLBookError("암호화된 ZIP 파일은 출판할 수 없습니다.")

    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        if file_type == stat.S_IFLNK:
            raise HTMLBookError("ZIP 내부의 심볼릭 링크는 사용할 수 없습니다.")
        raise HTMLBookError("ZIP 내부의 특수 파일은 사용할 수 없습니다.")
    if is_directory and file_type not in {0, stat.S_IFDIR}:
        raise HTMLBookError("ZIP 내부에 안전하지 않은 파일 경로가 있습니다.")
    if not is_directory and file_type == stat.S_IFDIR:
        raise HTMLBookError("ZIP 내부에 안전하지 않은 파일 경로가 있습니다.")

    path = PurePosixPath(*parts)
    if not is_directory and path.suffix.casefold() not in _ALLOWED_EXTENSIONS:
        raise HTMLBookError("ZIP에 허용되지 않는 파일 형식이 포함되어 있습니다.")
    return _ArchiveMember(info=info, path=path, is_directory=is_directory)


def _preflight(
    archive: zipfile.ZipFile,
    limits: HTMLBookLimits,
) -> tuple[list[_ArchiveMember], str | None]:
    members: list[_ArchiveMember] = []
    known_paths: dict[tuple[str, ...], bool] = {}
    for member_index, info in enumerate(archive.infolist(), start=1):
        if member_index > limits.max_members:
            raise HTMLBookError("HTML 전자책 ZIP의 전체 항목 수가 허용 한도를 초과했습니다.")
        member = _validated_member(info)
        if member is None:
            continue
        try:
            path_size = len(member.path.as_posix().encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise HTMLBookError("ZIP 내부 파일 경로의 문자 인코딩이 올바르지 않습니다.") from exc
        if path_size > limits.max_path_bytes:
            raise HTMLBookError("HTML 전자책 ZIP의 파일 경로가 너무 깁니다.")
        if len(member.path.parts) > limits.max_path_depth:
            raise HTMLBookError("HTML 전자책 ZIP의 폴더 단계가 너무 깊습니다.")
        key = _normalized_path_key(member.path.parts)
        if key in known_paths:
            raise HTMLBookError("ZIP 내부에 중복된 파일 경로가 있습니다.")
        known_paths[key] = member.is_directory
        members.append(member)

    file_keys = {key for key, is_directory in known_paths.items() if not is_directory}
    for key in file_keys:
        if any(key[:index] in file_keys for index in range(1, len(key))):
            raise HTMLBookError("ZIP 내부의 파일과 폴더 경로가 충돌합니다.")

    files = [member for member in members if not member.is_directory]
    if len(files) > limits.max_files:
        raise HTMLBookError("HTML 전자책 ZIP의 파일 수가 허용 한도를 초과했습니다.")
    declared_total = 0
    for member in files:
        file_size = member.info.file_size
        compressed_size = member.info.compress_size
        if member.info.compress_type not in _SUPPORTED_COMPRESSION_METHODS:
            raise HTMLBookError("HTML 전자책 ZIP에서 지원하지 않는 압축 방식이 감지되었습니다.")
        if file_size > limits.max_file_bytes:
            raise HTMLBookError("HTML 전자책 ZIP의 개별 파일 크기가 허용 한도를 초과했습니다.")
        if (
            member.path.suffix.casefold() in _TEXT_EXTENSIONS
            and file_size > limits.max_text_file_bytes
        ):
            raise HTMLBookError("HTML 전자책 ZIP의 텍스트 파일 크기가 허용 한도를 초과했습니다.")
        declared_total += file_size
        if declared_total > limits.max_total_bytes:
            raise HTMLBookError("HTML 전자책 ZIP의 전체 압축 해제 크기가 허용 한도를 초과했습니다.")
        if file_size:
            if compressed_size <= 0 or file_size / compressed_size > limits.max_compression_ratio:
                raise HTMLBookError("HTML 전자책 ZIP에서 비정상적으로 높은 압축률이 감지되었습니다.")

    index_members = [
        member for member in files if member.path.name.casefold() == "index.html"
    ]
    if len(index_members) != 1:
        raise HTMLBookError("HTML 전자책 ZIP에는 index.html이 정확히 하나 필요합니다.")
    index_member = index_members[0]
    root_index = len(index_member.path.parts) == 1
    wrapper: str | None = None
    if not root_index and files:
        top_levels = {member.path.parts[0] for member in files}
        if len(top_levels) == 1:
            candidate = next(iter(top_levels))
            if (
                len(index_member.path.parts) == 2
                and index_member.path.parts[0] == candidate
            ):
                wrapper = candidate
    if not root_index and wrapper is None:
        raise HTMLBookError(
            "HTML 전자책 ZIP의 최상위 또는 단일 폴더 안에 index.html이 필요합니다."
        )
    return members, wrapper


def _collect_visible_text(
    root: Path,
    html_paths: list[Path],
    max_chars: int,
) -> str:
    visible_parts: list[str] = []
    remaining_chars = max_chars
    try:
        for path in html_paths:
            if remaining_chars <= 0:
                break
            text = _visible_text((root / path).read_bytes())
            if not text:
                continue
            separator_size = 1 if visible_parts else 0
            if separator_size >= remaining_chars:
                break
            available = remaining_chars - separator_size
            visible_parts.append(text[:available])
            remaining_chars -= separator_size + min(len(text), available)
    except Exception as exc:
        raise HTMLBookError("HTML 전자책의 본문을 분석하지 못했습니다.") from exc
    return " ".join(visible_parts).rstrip()


def extract_html_book(
    archive_path: str | Path,
    destination: str | Path,
    *,
    limits: HTMLBookLimits | None = None,
) -> ExtractedHTMLBook:
    """Extract an HTML book ZIP and return its entrypoint and visible text."""

    selected_limits = limits or HTMLBookLimits()
    archive_path = Path(archive_path)
    destination = Path(destination).resolve()

    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise HTMLBookError("올바른 HTML 전자책 ZIP 파일이 아닙니다.") from exc

    if destination.exists():
        archive.close()
        raise HTMLBookError("HTML 전자책을 저장할 위치가 이미 존재합니다.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".html-book-", dir=destination.parent))
    extracted_paths: list[Path] = []
    total_bytes = 0
    stored_total_bytes = 0
    entrypoint_relative: Path | None = None
    visible = ""
    try:
        with archive:
            members, wrapper = _preflight(archive, selected_limits)
            for member in members:
                relative_parts = member.path.parts[1:] if wrapper else member.path.parts
                if not relative_parts:
                    continue
                relative = Path(*relative_parts)
                target = staging / relative
                if member.is_directory:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    member_bytes = 0
                    member_limit = min(
                        selected_limits.max_file_bytes,
                        selected_limits.max_text_file_bytes
                        if member.path.suffix.casefold() in _TEXT_EXTENSIONS
                        else selected_limits.max_file_bytes,
                    )
                    with archive.open(member.info, "r") as source, target.open("xb") as output:
                        while True:
                            chunk = source.read(selected_limits.stream_chunk_bytes)
                            if not chunk:
                                break
                            member_bytes += len(chunk)
                            if member_bytes > member_limit:
                                raise HTMLBookError(
                                    "HTML 전자책 ZIP의 개별 파일 크기가 허용 한도를 초과했습니다."
                                )
                            total_bytes += len(chunk)
                            if total_bytes > selected_limits.max_total_bytes:
                                raise HTMLBookError(
                                    "HTML 전자책 ZIP의 전체 압축 해제 크기가 허용 한도를 초과했습니다."
                                )
                            output.write(chunk)
                    if member_bytes != member.info.file_size:
                        raise HTMLBookError("ZIP 파일의 크기 정보가 실제 내용과 일치하지 않습니다.")
                    if member.path.suffix.casefold() in _TEXT_EXTENSIONS:
                        normalized_payload = _normalize_text_asset(target.read_bytes())
                        target.write_bytes(normalized_payload)
                        stored_bytes = len(normalized_payload)
                    else:
                        stored_bytes = member_bytes
                    stored_total_bytes += stored_bytes
                    if stored_total_bytes > selected_limits.max_total_bytes:
                        raise HTMLBookError(
                            "HTML 전자책 ZIP의 전체 압축 해제 크기가 허용 한도를 초과했습니다."
                        )
                except HTMLBookError:
                    raise
                except (RuntimeError, NotImplementedError, zipfile.BadZipFile, OSError) as exc:
                    raise HTMLBookError("ZIP 파일의 내용을 안전하게 읽을 수 없습니다.") from exc
                extracted_paths.append(relative)
                if len(relative.parts) == 1 and relative.name.casefold() == "index.html":
                    entrypoint_relative = relative
        if entrypoint_relative is None:
            raise HTMLBookError("HTML 전자책의 index.html을 찾을 수 없습니다.")
        html_paths = [entrypoint_relative]
        html_paths.extend(
            sorted(
                (
                    path
                    for path in extracted_paths
                    if path != entrypoint_relative and path.suffix.casefold() == ".html"
                ),
                key=lambda path: path.as_posix().casefold(),
            )
        )
        visible = _collect_visible_text(
            staging,
            html_paths,
            selected_limits.max_visible_text_chars,
        )
        try:
            staging.replace(destination)
        except OSError as exc:
            raise HTMLBookError("HTML 전자책을 출판 위치에 저장하지 못했습니다.") from exc
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    entrypoint = destination / entrypoint_relative

    return ExtractedHTMLBook(
        root=destination,
        entrypoint=entrypoint.resolve(),
        visible_text=visible,
        file_count=len(extracted_paths),
        total_bytes=stored_total_bytes,
    )
