from __future__ import annotations

from io import BytesIO
from pathlib import Path
import stat
import struct
import unicodedata
import warnings
import zipfile

import pytest

import html_book as html_book_module
from html_book import (
    HTMLBookError,
    HTMLBookLimits,
    extract_html_book,
)


def write_zip(path: Path, files: dict[str, bytes | str]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            payload = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(name, payload)
    return path


def write_zip_entries(
    path: Path,
    entries: list[tuple[zipfile.ZipInfo | str, bytes | str]],
) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for info, content in entries:
            payload = content.encode("utf-8") if isinstance(content, str) else content
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Duplicate name:.*", category=UserWarning)
                archive.writestr(info, payload)
    return path


def test_secure_default_limits_match_html_publication_policy():
    limits = HTMLBookLimits()

    assert limits.max_files == 2_000
    assert limits.max_members == 4_000
    assert limits.max_path_bytes == 240
    assert limits.max_path_depth == 20
    assert limits.max_file_bytes == 50 * 1024 * 1024
    assert limits.max_text_file_bytes == 10 * 1024 * 1024
    assert limits.max_total_bytes == 300 * 1024 * 1024
    assert limits.max_compression_ratio == 100.0


def mark_first_member_encrypted(path: Path) -> None:
    payload = bytearray(path.read_bytes())
    local = payload.index(b"PK\x03\x04")
    central = payload.index(b"PK\x01\x02")
    local_flags = struct.unpack_from("<H", payload, local + 6)[0] | 0x1
    central_flags = struct.unpack_from("<H", payload, central + 8)[0] | 0x1
    struct.pack_into("<H", payload, local + 6, local_flags)
    struct.pack_into("<H", payload, central + 8, central_flags)
    path.write_bytes(payload)


def test_extracts_root_html_book_and_collects_visible_korean_text(tmp_path: Path):
    source = write_zip(
        tmp_path / "관상록.zip",
        {
            "index.html": """<!doctype html><html lang=\"ko\"><head>
                <title>검색용 제목</title><style>.x { color: red }</style>
                <script>숨겨진 스크립트 문장</script></head><body>
                <h1>기혜경의 관상 톡</h1><p>하루의 운기를 보는 관상부위 인당</p>
                <div hidden>숨겨진 본문</div></body></html>""",
            "chapters/01.html": "<main><h2>인당은 하루의 일진을 보여주는 거울</h2></main>",
            "assets/book.css": "body { font-family: serif; }",
            "assets/search.js": "console.log('search');",
            "ocr_pages.json": '{"pages": 91}',
            "images/page-001.webp": b"RIFF-not-a-real-image",
            "__MACOSX/._index.html": b"ignored metadata",
            "__MACOSX/readme.exe": b"ignored metadata",
        },
    )
    destination = tmp_path / "published-html"

    result = extract_html_book(source, destination)

    assert result.root == destination.resolve()
    assert result.entrypoint == (destination / "index.html").resolve()
    assert result.file_count == 6
    assert result.total_bytes == sum(
        path.stat().st_size for path in destination.rglob("*") if path.is_file()
    )
    assert "기혜경의 관상 톡" in result.visible_text
    assert "하루의 운기를 보는 관상부위 인당" in result.visible_text
    assert "인당은 하루의 일진을 보여주는 거울" in result.visible_text
    assert "검색용 제목" not in result.visible_text
    assert "숨겨진 스크립트 문장" not in result.visible_text
    assert "숨겨진 본문" not in result.visible_text
    assert not (destination / "__MACOSX").exists()


def test_extracts_single_wrapper_folder_without_preserving_wrapper(tmp_path: Path):
    source = write_zip(
        tmp_path / "wrapped.zip",
        {
            "관상록_웹전환본/index.html": "<h1>91쪽 스캔 자료 웹 전환본</h1>",
            "관상록_웹전환본/text/page-001.html": "<p>첫 페이지 한글 본문</p>",
            "관상록_웹전환본/images/page-001.jpg": b"jpeg-placeholder",
        },
    )

    result = extract_html_book(source, tmp_path / "book")

    assert result.entrypoint.name == "index.html"
    assert (result.root / "index.html").is_file()
    assert (result.root / "text/page-001.html").is_file()
    assert not (result.root / "관상록_웹전환본").exists()
    assert "첫 페이지 한글 본문" in result.visible_text


def test_visible_text_limit_is_enforced_across_html_files(tmp_path: Path):
    source = write_zip(
        tmp_path / "bounded.zip",
        {
            "index.html": "<p>첫 페이지 본문입니다.</p>",
            "page-002.html": "<p>둘째 페이지의 아주 긴 본문입니다.</p>",
        },
    )

    result = extract_html_book(
        source,
        tmp_path / "book",
        limits=HTMLBookLimits(max_visible_text_chars=15),
    )

    assert len(result.visible_text) <= 15
    assert result.visible_text.startswith("첫 페이지")


@pytest.mark.parametrize(
    "malicious_name",
    [
        "../outside.js",
        "assets/../../outside.css",
        "/absolute.js",
        "C:/windows/system.js",
        "C:relative-system.js",
        "C:\\windows\\system.js",
        "assets\\..\\outside.js",
        "assets//ambiguous.js",
    ],
)
def test_rejects_path_traversal_and_absolute_path_variants(
    tmp_path: Path, malicious_name: str
):
    source = write_zip_entries(
        tmp_path / "malicious.zip",
        [("index.html", "<h1>안전한 표지</h1>"), (malicious_name, "bad")],
    )
    destination = tmp_path / "book"

    with pytest.raises(HTMLBookError, match="경로"):
        extract_html_book(source, destination)

    assert not destination.exists()
    assert not (tmp_path / "outside.js").exists()
    assert not (tmp_path / "outside.css").exists()


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR])
def test_rejects_links_and_other_special_file_types(tmp_path: Path, kind: int):
    special = zipfile.ZipInfo("assets/special.js")
    special.create_system = 3
    special.external_attr = (kind | 0o777) << 16
    source = write_zip_entries(
        tmp_path / "special.zip",
        [("index.html", "<h1>책</h1>"), (special, "../index.html")],
    )
    destination = tmp_path / "book"

    with pytest.raises(HTMLBookError, match="링크|특수"):
        extract_html_book(source, destination)

    assert not destination.exists()


def test_rejects_encrypted_zip_member_with_safe_error(tmp_path: Path):
    source = write_zip(tmp_path / "encrypted.zip", {"index.html": "<h1>책</h1>"})
    mark_first_member_encrypted(source)

    with pytest.raises(HTMLBookError, match="암호") as captured:
        extract_html_book(source, tmp_path / "book")

    assert "password" not in str(captured.value).casefold()
    assert not (tmp_path / "book").exists()


@pytest.mark.parametrize(
    "duplicate_names",
    [
        ("assets/app.js", "assets/app.js"),
        ("assets/App.js", "assets/app.js"),
        ("assets/한글.js", f"assets/{unicodedata.normalize('NFD', '한글')}.js"),
    ],
)
def test_rejects_duplicate_or_ambiguous_normalized_paths(
    tmp_path: Path, duplicate_names: tuple[str, str]
):
    first, second = duplicate_names
    source = write_zip_entries(
        tmp_path / "duplicate.zip",
        [
            ("index.html", "<h1>책</h1>"),
            (first, "first"),
            (second, "second"),
        ],
    )

    with pytest.raises(HTMLBookError, match="중복"):
        extract_html_book(source, tmp_path / "book")

    assert not (tmp_path / "book").exists()


@pytest.mark.parametrize(
    "blocked_name",
    [
        "server.php",
        "server.py",
        "run.exe",
        "command.sh",
        "archive.zip",
        "drawing.svg",
        "document.xml",
        "chapter.xhtml",
        "module.wasm",
        "extensionless",
        "double.html.php",
    ],
)
def test_rejects_unapproved_file_extensions(tmp_path: Path, blocked_name: str):
    source = write_zip(
        tmp_path / "blocked.zip",
        {"index.html": "<h1>책</h1>", blocked_name: "unsafe"},
    )

    with pytest.raises(HTMLBookError, match="허용되지 않는 파일 형식"):
        extract_html_book(source, tmp_path / "book")

    assert not (tmp_path / "book").exists()


def test_requires_root_or_single_wrapper_index_and_leaves_no_partial_files(tmp_path: Path):
    missing = write_zip(tmp_path / "missing.zip", {"chapter.html": "<p>본문</p>"})
    ambiguous = write_zip(
        tmp_path / "ambiguous.zip",
        {"wrapper/index.html": "<p>본문</p>", "other/readme.txt": "extra"},
    )

    with pytest.raises(HTMLBookError, match="index.html"):
        extract_html_book(missing, tmp_path / "missing-book")
    with pytest.raises(HTMLBookError, match="index.html"):
        extract_html_book(ambiguous, tmp_path / "ambiguous-book")

    assert not (tmp_path / "missing-book").exists()
    assert not (tmp_path / "ambiguous-book").exists()


def test_rejects_more_than_one_index_entrypoint(tmp_path: Path):
    source = write_zip(
        tmp_path / "multiple-indexes.zip",
        {
            "index.html": "<h1>책</h1>",
            "nested/index.html": "<h2>중복 시작 페이지</h2>",
        },
    )

    with pytest.raises(HTMLBookError, match="정확히 하나"):
        extract_html_book(source, tmp_path / "book")

    assert not (tmp_path / "book").exists()


def test_rejects_file_directory_prefix_collision(tmp_path: Path):
    source = write_zip_entries(
        tmp_path / "collision.zip",
        [
            ("index.html", "<h1>책</h1>"),
            ("assets.js", "not a directory"),
            ("assets.js/app.js", "console.log('x')"),
        ],
    )

    with pytest.raises(HTMLBookError, match="경로"):
        extract_html_book(source, tmp_path / "book")

    assert not (tmp_path / "book").exists()


def test_rejects_archive_over_file_count_limit_and_ignores_macos_metadata(tmp_path: Path):
    source = write_zip(
        tmp_path / "many.zip",
        {
            "index.html": "<h1>책</h1>",
            "one.txt": "1",
            "two.txt": "2",
            "__MACOSX/ignored.exe": "ignored",
        },
    )

    with pytest.raises(HTMLBookError, match="파일 수"):
        extract_html_book(
            source,
            tmp_path / "book",
            limits=HTMLBookLimits(max_files=2),
        )

    assert not (tmp_path / "book").exists()


def test_rejects_archive_over_total_member_limit_including_directories(tmp_path: Path):
    source = write_zip_entries(
        tmp_path / "many-directories.zip",
        [
            ("index.html", "<h1>책</h1>"),
            ("one/", b""),
            ("two/", b""),
            ("three/", b""),
        ],
    )

    with pytest.raises(HTMLBookError, match="전체 항목 수"):
        extract_html_book(
            source,
            tmp_path / "book",
            limits=HTMLBookLimits(max_members=3),
        )

    assert not (tmp_path / "book").exists()


def test_rejects_overlong_and_excessively_deep_member_paths(tmp_path: Path):
    overlong = write_zip(
        tmp_path / "overlong.zip",
        {"index.html": "<h1>책</h1>", f"assets/{'가' * 20}.js": "safe"},
    )
    deep_name = "/".join(["folder"] * 6 + ["reader.js"])
    too_deep = write_zip(
        tmp_path / "too-deep.zip",
        {"index.html": "<h1>책</h1>", deep_name: "safe"},
    )

    with pytest.raises(HTMLBookError, match="경로가 너무 깁니다"):
        extract_html_book(
            overlong,
            tmp_path / "overlong-book",
            limits=HTMLBookLimits(max_path_bytes=40),
        )
    with pytest.raises(HTMLBookError, match="폴더 단계"):
        extract_html_book(
            too_deep,
            tmp_path / "deep-book",
            limits=HTMLBookLimits(max_path_depth=5),
        )

    assert not (tmp_path / "overlong-book").exists()
    assert not (tmp_path / "deep-book").exists()


def test_rejects_member_declared_over_individual_size_limit(tmp_path: Path):
    source = write_zip(
        tmp_path / "large-member.zip",
        {"index.html": "<h1>책</h1>", "large.txt": "가" * 20},
    )

    with pytest.raises(HTMLBookError, match="개별 파일"):
        extract_html_book(
            source,
            tmp_path / "book",
            limits=HTMLBookLimits(max_file_bytes=20),
        )

    assert not (tmp_path / "book").exists()


def test_rejects_declared_total_uncompressed_size_limit(tmp_path: Path):
    source = write_zip(
        tmp_path / "large-total.zip",
        {"index.html": "<h1>책</h1>", "one.txt": "1" * 10, "two.txt": "2" * 10},
    )

    with pytest.raises(HTMLBookError, match="전체 압축 해제 크기"):
        extract_html_book(
            source,
            tmp_path / "book",
            limits=HTMLBookLimits(max_total_bytes=25),
        )

    assert not (tmp_path / "book").exists()


def test_rejects_suspicious_compression_ratio(tmp_path: Path):
    source = write_zip(
        tmp_path / "bomb.zip",
        {"index.html": "<h1>책</h1>", "repeated.txt": "A" * 20_000},
    )

    with pytest.raises(HTMLBookError, match="압축률"):
        extract_html_book(
            source,
            tmp_path / "book",
            limits=HTMLBookLimits(max_compression_ratio=5),
        )

    assert not (tmp_path / "book").exists()


def test_rejects_non_stored_or_deflated_compression_method(tmp_path: Path):
    source = tmp_path / "bzip2.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_BZIP2) as archive:
        archive.writestr("index.html", "<h1>책</h1>")

    with pytest.raises(HTMLBookError, match="압축 방식"):
        extract_html_book(source, tmp_path / "book")

    assert not (tmp_path / "book").exists()


def test_rejects_text_asset_over_separate_text_size_limit(tmp_path: Path):
    source = write_zip(
        tmp_path / "large-script.zip",
        {"index.html": "<h1>책</h1>", "assets/app.js": "x" * 64},
    )

    with pytest.raises(HTMLBookError, match="텍스트 파일"):
        extract_html_book(
            source,
            tmp_path / "book",
            limits=HTMLBookLimits(max_text_file_bytes=32),
        )

    assert not (tmp_path / "book").exists()


def test_streaming_counter_rejects_more_bytes_than_member_header_claims(
    tmp_path: Path, monkeypatch
):
    source = write_zip(
        tmp_path / "lying-header.zip",
        {"index.html": "<h1>책</h1>", "large.txt": "x"},
    )
    original_open = zipfile.ZipFile.open

    def oversized_stream(self, name, mode="r", pwd=None, *, force_zip64=False):
        info = name if isinstance(name, zipfile.ZipInfo) else self.getinfo(name)
        if mode == "r" and info.filename == "large.txt":
            return BytesIO(b"x" * 64)
        return original_open(self, name, mode, pwd, force_zip64=force_zip64)

    monkeypatch.setattr(zipfile.ZipFile, "open", oversized_stream)

    with pytest.raises(HTMLBookError, match="개별 파일"):
        extract_html_book(
            source,
            tmp_path / "book",
            limits=HTMLBookLimits(max_file_bytes=16, stream_chunk_bytes=7),
        )

    assert not (tmp_path / "book").exists()


def test_streaming_counter_rejects_actual_total_over_limit(tmp_path: Path, monkeypatch):
    source = write_zip(
        tmp_path / "lying-total.zip",
        {"index.html": "<h1>책</h1>", "one.txt": "1", "two.txt": "2"},
    )
    original_open = zipfile.ZipFile.open

    def expanded_stream(self, name, mode="r", pwd=None, *, force_zip64=False):
        info = name if isinstance(name, zipfile.ZipInfo) else self.getinfo(name)
        if mode == "r" and info.filename in {"one.txt", "two.txt"}:
            return BytesIO(info.filename.encode("ascii") * 3)
        return original_open(self, name, mode, pwd, force_zip64=force_zip64)

    monkeypatch.setattr(zipfile.ZipFile, "open", expanded_stream)

    with pytest.raises(HTMLBookError, match="전체 압축 해제 크기"):
        extract_html_book(
            source,
            tmp_path / "book",
            limits=HTMLBookLimits(max_total_bytes=20, stream_chunk_bytes=5),
        )

    assert not (tmp_path / "book").exists()


def test_visible_text_parser_handles_void_hidden_tag_and_cp949(tmp_path: Path):
    source = write_zip(
        tmp_path / "legacy-korean.zip",
        {
            "index.html": (
                "<html><head><meta charset='cp949'><title>숨긴 제목</title></head><body>"
                "<input hidden value='비밀'>"
                "<p>인당은 하루의 일진을 보여주는 거울</p></body></html>"
            ).encode("cp949"),
            "assets/search.js": "const label = '본문 검색';".encode("cp949"),
            "ocr_pages.json": '{"title":"기혜경의 관상 톡"}'.encode("cp949"),
        },
    )

    result = extract_html_book(source, tmp_path / "book")

    assert result.visible_text == "인당은 하루의 일진을 보여주는 거울"
    assert "인당은 하루의 일진" in result.entrypoint.read_bytes().decode("utf-8")
    assert "본문 검색" in (result.root / "assets/search.js").read_bytes().decode("utf-8")
    assert "기혜경의 관상 톡" in (result.root / "ocr_pages.json").read_bytes().decode("utf-8")
    assert result.total_bytes == sum(
        path.stat().st_size for path in result.root.rglob("*") if path.is_file()
    )


def test_rejects_unsupported_or_binary_text_asset_encoding(tmp_path: Path):
    source = write_zip(
        tmp_path / "invalid-encoding.zip",
        {"index.html": "<h1>책</h1>", "assets/bad.js": b"\xff\xff\x80\x80"},
    )

    with pytest.raises(HTMLBookError, match="인코딩"):
        extract_html_book(source, tmp_path / "book")

    assert not (tmp_path / "book").exists()


def test_rejects_existing_destination_without_modifying_it(tmp_path: Path):
    source = write_zip(tmp_path / "book.zip", {"index.html": "<h1>책</h1>"})
    destination = tmp_path / "book"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(HTMLBookError, match="이미 존재"):
        extract_html_book(source, destination)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_text_analysis_failure_does_not_publish_partial_destination(
    tmp_path: Path, monkeypatch
):
    source = write_zip(tmp_path / "book.zip", {"index.html": "<h1>책</h1>"})
    destination = tmp_path / "book"

    def fail_analysis(payload: bytes) -> str:
        raise ValueError("raw parser internals")

    monkeypatch.setattr(html_book_module, "_visible_text", fail_analysis)

    with pytest.raises(HTMLBookError, match="본문을 분석") as captured:
        extract_html_book(source, destination)

    assert "raw parser internals" not in str(captured.value)
    assert not destination.exists()
    assert not list(tmp_path.glob(".html-book-*"))
