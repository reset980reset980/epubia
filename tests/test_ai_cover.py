from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from ai_cover import (
    AICoverError,
    build_cover_prompt,
    generate_ai_cover_background,
    sample_book_content,
)


@dataclass
class CoverMeta:
    title: str = "별빛 아래 마음의 지도"
    subtitle: str = "상실에서 회복으로 가는 기록"
    author: str = "기혜경"
    publisher: str = "혜경 전자책 스튜디오"
    description: str = "삶의 전환점을 지나며 내면의 방향을 다시 찾는 에세이"


def png_base64(size: tuple[int, int] = (64, 96)) -> str:
    buffer = BytesIO()
    Image.new("RGB", size, (28, 74, 122)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class FakeImages:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.images = FakeImages(response=response, error=error)


def test_sample_book_content_is_bounded_to_opening_only():
    manuscript = "도입부-" + ("가" * 7_000) + "중심부-" + ("나" * 7_000) + "결말부-" + ("다" * 7_000)

    sampled = sample_book_content(manuscript, max_chars=1_200)

    assert len(sampled) <= 1_200
    assert sampled.startswith("도입부-")
    assert "중심부" not in sampled
    assert "결말부" not in sampled
    assert "중략" not in sampled


def test_sample_book_content_normalizes_short_manuscript():
    assert sample_book_content("  첫 문단  \r\n\r\n\r\n 둘째 문단 \t ") == "첫 문단\n\n둘째 문단"
    assert sample_book_content("본문", max_chars=0) == ""


def test_build_cover_prompt_prefers_description_and_text_exclusions():
    prompt = build_cover_prompt(CoverMeta(), "도입과 결말을 잇는 회복의 상징", variation=2)

    assert "별빛 아래 마음의 지도" in prompt
    assert "기혜경" in prompt
    assert "삶의 전환점을 지나며 내면의 방향을 다시 찾는 에세이" in prompt
    assert "도입과 결말을 잇는 회복의 상징" not in prompt
    assert "Publisher-provided book description" in prompt
    assert "central subject" in prompt
    assert "emotional tone" in prompt
    assert "symbolic imagery" in prompt
    assert "Variation 2" in prompt
    assert "Do not render any text" in prompt
    assert "logos" in prompt
    assert "watermarks" in prompt
    assert "Korean title" in prompt
    assert "negative space" in prompt


def test_build_cover_prompt_uses_only_opening_when_description_is_empty():
    manuscript = "책의 시작-" + ("앞부분 " * 600) + "책의 결말-비밀"

    prompt = build_cover_prompt(CoverMeta(description=""), manuscript, variation=0)

    assert "Opening manuscript excerpt" in prompt
    assert "책의 시작" in prompt
    assert "책의 결말-비밀" not in prompt
    assert "앞부분" in prompt


def test_generate_ai_cover_background_calls_sdk_and_atomically_saves_png(tmp_path: Path):
    response = SimpleNamespace(data=[SimpleNamespace(b64_json=png_base64())])
    client = FakeClient(response=response)
    output = tmp_path / "nested" / "ai-background.png"

    result = generate_ai_cover_background(
        CoverMeta(description=""),
        "책의 시작\n" + ("대표 내용 " * 3_000) + "\n책의 끝",
        output,
        api_key="test-key",
        quality="high",
        variation=3,
        client=client,
    )

    assert result == output
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(output) as saved:
        assert saved.format == "PNG"
        assert saved.size == (64, 96)
        assert saved.mode == "RGB"

    assert len(client.images.calls) == 1
    call = client.images.calls[0]
    assert call["model"] == "gpt-image-2"
    assert call["size"] == "1024x1536"
    assert call["quality"] == "high"
    assert call["output_format"] == "png"
    assert call["n"] == 1
    assert "책의 시작" in call["prompt"]
    assert "책의 끝" not in call["prompt"]
    assert len(call["prompt"]) < 6_000
    assert "Variation 3" in call["prompt"]
    assert not list(output.parent.glob(".ai-background.png.*.tmp"))


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (SimpleNamespace(data=[]), "응답이 비어"),
        (SimpleNamespace(data=[SimpleNamespace(b64_json="%%%not-base64%%%")]), "해석하지 못"),
        (
            SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(b"not a png").decode("ascii"))]
            ),
            "PNG",
        ),
    ],
)
def test_generate_ai_cover_background_rejects_bad_responses(
    tmp_path: Path, response, message: str
):
    output = tmp_path / "cover.png"

    with pytest.raises(AICoverError, match=message):
        generate_ai_cover_background(
            CoverMeta(), "본문", output, api_key="test-key", client=FakeClient(response=response)
        )

    assert not output.exists()


@pytest.mark.parametrize("api_key", ["", "   ", None])
def test_generate_ai_cover_background_requires_api_key(tmp_path: Path, api_key):
    client = FakeClient(response=SimpleNamespace(data=[SimpleNamespace(b64_json=png_base64())]))

    with pytest.raises(AICoverError, match="API 키"):
        generate_ai_cover_background(
            CoverMeta(), "본문", tmp_path / "cover.png", api_key=api_key, client=client
        )

    assert client.images.calls == []


def test_api_failure_does_not_expose_secret_or_provider_response(tmp_path: Path):
    api_key = "sk-sensitive-value"
    provider_detail = "raw-provider-secret-response"
    client = FakeClient(error=RuntimeError(f"{provider_detail}: {api_key}"))

    with pytest.raises(AICoverError) as captured:
        generate_ai_cover_background(
            CoverMeta(), "본문", tmp_path / "cover.png", api_key=api_key, client=client
        )

    message = str(captured.value)
    assert "OpenAI 표지 이미지 생성에 실패" in message
    assert api_key not in message
    assert provider_detail not in message
