"""Tests for multimodal classify_article."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ai import classify_article, reset_circuit_breaker


@pytest.fixture(autouse=True)
def _reset_breaker():
    reset_circuit_breaker()


def _make_gemini_response(is_relevant: bool, reason: str):
    """Create a mock Gemini response with JSON text."""
    response = MagicMock()
    response.text = json.dumps({"is_relevant": is_relevant, "reason": reason})
    response.usage_metadata = None
    return response


@pytest.mark.asyncio
async def test_classify_sends_media_bytes():
    """classify_article sends image bytes as Part when media_path is provided."""
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(
        return_value=_make_gemini_response(True, "cool image")
    )

    fake_image = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # fake JPEG bytes

    with patch("src.ai._read_media_file", return_value=fake_image):
        result = await classify_article(
            client,
            "gemini-2.0-flash",
            "Bond of love",
            "Bond of love",
            source_type="reddit",
            media_path="/tmp/fake.jpg",
            media_type="image",
        )

    assert result.is_relevant is True

    # Verify contents was a list with 2 items (Part + prompt string)
    call_kwargs = client.aio.models.generate_content.call_args
    contents = call_kwargs.kwargs.get("contents") or call_kwargs.args[0]
    assert isinstance(contents, list)
    assert len(contents) == 2  # [Part, prompt_str]


@pytest.mark.asyncio
async def test_classify_works_without_media():
    """classify_article still works text-only when no media_path given."""
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(
        return_value=_make_gemini_response(False, "political content")
    )

    result = await classify_article(
        client,
        "gemini-2.0-flash",
        "President signs new law",
        "The president signed...",
        source_type="rss",
    )

    assert result.is_relevant is False
    assert "political" in result.reason

    # Verify contents is a list with just the prompt string
    call_kwargs = client.aio.models.generate_content.call_args
    contents = call_kwargs.kwargs.get("contents") or call_kwargs.args[0]
    assert isinstance(contents, list)
    assert len(contents) == 1  # [prompt_str]
