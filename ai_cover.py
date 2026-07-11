from __future__ import annotations

import base64
import binascii
import os
import re
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError


class AICoverError(RuntimeError):
    """A safe, user-facing failure raised while generating an AI cover."""


DEFAULT_OPENING_EXCERPT_CHARS = 2_000
_VARIATION_DIRECTIONS = (
    "상징 하나를 중심으로 정돈된 현대적 에디토리얼 아트",
    "책의 정서를 빛과 공간감으로 표현한 분위기 중심의 장면",
    "핵심 개념을 절제된 기하학과 유기적 형태로 번역한 추상 구성",
    "독자가 발견할 수 있는 은유적 오브제를 활용한 서사적 구성",
)


def _normalize_source_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def sample_book_content(text: str, max_chars: int = DEFAULT_OPENING_EXCERPT_CHARS) -> str:
    """Return only a bounded opening excerpt; never send the middle or ending."""

    if max_chars <= 0:
        return ""

    normalized = _normalize_source_text(text)
    return normalized[:max_chars].rstrip()


def _meta_value(meta: Any, name: str, limit: int) -> str:
    if isinstance(meta, dict):
        value = meta.get(name, "")
    else:
        value = getattr(meta, name, "")
    value = _normalize_source_text(str(value or ""))
    return value[:limit]


def build_cover_prompt(meta: Any, excerpt: str, variation: int = 0) -> str:
    """Build a text-free cover-background prompt grounded in the book content."""

    title = _meta_value(meta, "title", 200) or "제목 미정"
    subtitle = _meta_value(meta, "subtitle", 400) or "없음"
    author = _meta_value(meta, "author", 200) or "미상"
    publisher = _meta_value(meta, "publisher", 200) or "미상"
    description = _meta_value(meta, "description", 2_000)
    if description:
        context_source = "Publisher-provided book description"
        cover_context = description
    else:
        context_source = "Opening manuscript excerpt (maximum 2,000 characters)"
        cover_context = sample_book_content(excerpt) or "원고 내용이 제공되지 않았습니다."

    try:
        variation_index = int(variation)
    except (TypeError, ValueError):
        variation_index = 0
    direction = _VARIATION_DIRECTIONS[variation_index % len(_VARIATION_DIRECTIONS)]

    return f"""Create a premium portrait book-cover BACKGROUND illustration only.

First analyze the supplied Korean book metadata and primary cover context. Infer the central subject, emotional tone, recurring symbols, intended readership, and visual metaphor. Then express that analysis as one coherent, sophisticated cover image.

[Book metadata — reference for meaning only]
Title: {title}
Subtitle: {subtitle}
Author: {author}
Publisher: {publisher}

[Primary cover context]
Source: {context_source}
{cover_context}

[Art direction]
- Variation {variation_index}: {direction}.
- Portrait 2:3 composition designed for a Korean digital book cover.
- Create an original, polished editorial illustration with a clear focal idea, restrained detail, strong hierarchy, and thumbnail readability.
- Reflect the provided context's central theme, emotion, and symbolic imagery rather than making a generic technology image.
- Preserve generous calm negative space in the upper 35% and lower 18% so Korean title, subtitle, author, and publisher can be composited later. Keep essential faces, symbols, and focal objects out of those reserved areas.
- Extend artwork cleanly to every edge; no border, frame, book mockup, device mockup, or 3D book.

[Absolute exclusions]
Do not render any text, letters, Korean or Latin characters, numbers, typography, captions, labels, signs, logos, brand marks, signatures, ISBN/barcodes, UI elements, or watermarks anywhere in the image. The final result must be artwork only; all Korean typography will be added programmatically afterward."""


def _response_base64(response: Any) -> str:
    data = response.get("data") if isinstance(response, dict) else getattr(response, "data", None)
    if not data:
        raise AICoverError("OpenAI 이미지 응답이 비어 있습니다. 잠시 후 다시 시도해주세요.")

    try:
        first = data[0]
    except (IndexError, KeyError, TypeError):
        raise AICoverError("OpenAI 이미지 응답의 형식을 확인할 수 없습니다. 다시 생성해주세요.") from None
    encoded = first.get("b64_json") if isinstance(first, dict) else getattr(first, "b64_json", None)
    if not isinstance(encoded, str) or not encoded.strip():
        raise AICoverError("OpenAI 이미지 응답에 저장할 이미지가 없습니다. 다시 생성해주세요.")
    return encoded.strip()


def _decode_png(encoded: str) -> Image.Image:
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise AICoverError("OpenAI 이미지 응답을 안전하게 해석하지 못했습니다. 다시 생성해주세요.") from None

    if not image_bytes:
        raise AICoverError("OpenAI 이미지 응답에 저장할 이미지가 없습니다. 다시 생성해주세요.")

    try:
        with Image.open(BytesIO(image_bytes)) as verification_image:
            if verification_image.format != "PNG":
                raise AICoverError("생성된 표지 이미지가 올바른 PNG 형식이 아닙니다.")
            verification_image.verify()

        with Image.open(BytesIO(image_bytes)) as source_image:
            source_image.load()
            if source_image.width < 1 or source_image.height < 1:
                raise AICoverError("생성된 표지 이미지의 크기가 올바르지 않습니다.")
            mode = "RGBA" if "A" in source_image.getbands() else "RGB"
            return source_image.convert(mode)
    except AICoverError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise AICoverError("생성된 표지 이미지 파일이 손상되었거나 올바른 PNG가 아닙니다.") from None


def _atomic_save_png(image: Image.Image, output_path: Path) -> Path:
    temporary_path: Path | None = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            image.save(temporary_file, format="PNG", optimize=True)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, output_path)
        return output_path
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise AICoverError("생성된 표지 이미지를 저장하지 못했습니다. 저장 공간과 권한을 확인해주세요.") from None


def generate_ai_cover_background(
    meta: Any,
    text: str,
    output_path: str | Path,
    *,
    api_key: str,
    model: str = "gpt-image-2",
    quality: str = "medium",
    timeout: float = 180,
    variation: int = 0,
    client: Any = None,
) -> Path:
    """Generate and atomically store a validated, text-free portrait PNG background."""

    if not isinstance(api_key, str) or not api_key.strip():
        raise AICoverError("OpenAI API 키가 설정되지 않았습니다. 서버 환경변수를 확인해주세요.")

    if client is None:
        try:
            from openai import OpenAI
        except ImportError:
            raise AICoverError("OpenAI Python SDK가 설치되지 않아 AI 표지를 생성할 수 없습니다.") from None
        try:
            client = OpenAI(api_key=api_key.strip(), timeout=timeout)
        except Exception:
            raise AICoverError("OpenAI 이미지 생성 연결을 준비하지 못했습니다. API 설정을 확인해주세요.") from None

    prompt = build_cover_prompt(meta, text, variation=variation)
    try:
        response = client.images.generate(
            model=model,
            prompt=prompt,
            size="1024x1536",
            quality=quality,
            output_format="png",
            n=1,
        )
    except Exception:
        raise AICoverError(
            "OpenAI 표지 이미지 생성에 실패했습니다. API 키, 결제 한도 또는 네트워크 상태를 확인해주세요."
        ) from None

    encoded = _response_base64(response)
    image = _decode_png(encoded)
    try:
        return _atomic_save_png(image, Path(output_path))
    finally:
        image.close()
