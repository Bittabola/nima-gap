"""Gemini AI for classification and translation."""

import asyncio
import json
import logging
import mimetypes
import os
import random
from dataclasses import dataclass
from typing import Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Token usage tracking (reset each fetch cycle)
_token_stats = {
    "classify_calls": 0,
    "translate_calls": 0,
    "input_tokens": 0,
    "output_tokens": 0,
}


def reset_token_stats() -> None:
    """Reset token statistics for a new fetch cycle."""
    _token_stats["classify_calls"] = 0
    _token_stats["translate_calls"] = 0
    _token_stats["input_tokens"] = 0
    _token_stats["output_tokens"] = 0


def get_token_stats() -> dict:
    """Get current token usage statistics."""
    return _token_stats.copy()


def _log_token_usage(response, call_type: str) -> None:
    """Extract and log token usage from Gemini response."""
    try:
        usage = response.usage_metadata
        if usage:
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0
            _token_stats["input_tokens"] += input_tokens
            _token_stats["output_tokens"] += output_tokens
            logger.debug(
                f"{call_type}: {input_tokens} input + {output_tokens} output tokens"
            )
    except Exception:
        pass  # Don't fail if usage metadata unavailable


# Exponential backoff settings
MAX_RETRIES = 5
BASE_DELAY = 1.0  # seconds
MAX_DELAY = 60.0  # seconds


async def call_with_backoff(
    func,
    *args,
    max_retries: int = MAX_RETRIES,
    **kwargs,
):
    """
    Call an async function with exponential backoff on failure.
    Handles rate limits and transient errors.
    """
    last_exception = None

    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            error_str = str(e).lower()

            # Check if it's a rate limit or retryable error
            is_rate_limit = any(
                x in error_str
                for x in [
                    "rate limit",
                    "quota",
                    "429",
                    "resource exhausted",
                    "too many requests",
                    "overloaded",
                ]
            )
            is_transient = any(
                x in error_str
                for x in [
                    "timeout",
                    "connection",
                    "503",
                    "502",
                    "500",
                    "unavailable",
                    "internal error",
                ]
            )

            if not (is_rate_limit or is_transient):
                # Non-retryable error, don't waste time retrying
                logger.error(f"Non-retryable error: {e}")
                raise

            # Calculate delay with jitter
            delay = min(BASE_DELAY * (2**attempt), MAX_DELAY)
            jitter = random.uniform(0, delay * 0.1)
            total_delay = delay + jitter

            logger.warning(
                f"API call failed (attempt {attempt + 1}/{max_retries}): {e}. "
                f"Retrying in {total_delay:.1f}s..."
            )
            await asyncio.sleep(total_delay)

    # All retries exhausted
    logger.error(f"All {max_retries} retries failed. Last error: {last_exception}")
    raise last_exception


@dataclass
class ClassificationResult:
    """Result of article classification."""

    is_relevant: bool
    reason: str


@dataclass
class TranslationResult:
    """Result of article translation."""

    content: str
    success: bool
    error: Optional[str] = None


def init_gemini(api_key: str) -> genai.Client:
    """Initialize Gemini client."""
    return genai.Client(api_key=api_key)


CLASSIFIER_PROMPT = """You are a content classifier for a visual-first Telegram channel focused on amazing, curious, and viral content.

Analyze the following article/post and determine if it's suitable for a visually-driven "wow factor" channel.

INCLUDE content about:
- Unique machines, specialized tools, and engineering marvels
- High-action nature clips and stunning wildlife
- Architecture, art, and eccentric design
- Space, science discoveries, and futuristic technology
- Viral "Did you know?" facts with strong visuals
- Curious places, hidden history, and geography

EXCLUDE content that:
- Is political, religious, or controversial
- Contains violence, tragedy, or disturbing imagery
- Is a product promotion or advertisement
- Has low visual impact or is just a news headline
- Criticizes or discusses government officials, presidents, or political leaders
- Mentions corruption, protests, riots, or civil unrest
- Touches on ethnic or regional separatism, territorial disputes, or sovereignty
- Discusses human rights abuses, forced labor, or torture
- Contains religious extremism, fundamentalism, or unapproved religious commentary
- Promotes or references drugs, alcohol abuse, or illegal substances
- Contains sexual or pornographic content
- Incites national, racial, ethnic, or religious hatred or discrimination
- Discusses LGBTQ+ topics
- References terrorism, extremism, or radicalization
- Mentions military conflicts, war propaganda, or sanctions
- Contains defamation or insults toward any public figures or institutions

Article Title: {title}
Article Content: {content}
Media URL: {media_url}
Source Type: {source_type}

Respond ONLY with valid JSON:
{{"is_relevant": true/false, "reason": "brief explanation"}}"""


TRANSLATOR_PROMPT = """**Role:**
You are the sole writer for the Telegram blog **"@olamda_nima_gap"** (What's happening in the world). You turn Reddit/internet finds into short, punchy Uzbek (Latin script) posts.

**Core Identity:**
You're a curious friend sharing something cool — NOT a journalist, NOT a textbook. Every post should feel like "Yo, you gotta see this!"

**CRITICAL — Look at the Media:**
An image or video is attached to this request. You MUST describe what you actually see in it. Do NOT guess from the title alone.
- If the title is vague (e.g., "How?!", "This is insane", "Wait for it..."), the media is your primary source — describe what is happening in it.
- If no media is attached, work from the title and content only, but keep descriptions general rather than fabricating visual details.

**Structure Rules — VARIETY IS MANDATORY:**
Do NOT follow one fixed template. Each post must feel different. Mix and match from these approaches:

- Start with a question ("Yerni teshib robotlar yashayotganini bilasizmi?")
- Start with a bold claim ("Bu daraxt 3000 yoshda.")
- Start with action ("Keling, ko'ramiz...")
- Start mid-scene ("...va u shunchaki suv ostiga sho'ng'idi.")
- Start with the punchline first, then explain
- Start with "Imagine..." / "Tasavvur qiling..."
- Skip the opener entirely — go straight into the fact

**BANNED patterns (never use these):**
- Do NOT start every post with "Suratdagi..." or "Videodagi..." — use these rarely, at most 1 in 5 posts.
- Do NOT end with generic reactions like "Tabiat ajoyib", "Kelajak hayratlanarli", "Insoniyat g'alati". Find something specific to say, or just end with the fact.
- Do NOT use the phrase "ishonish qiyin" more than once every 10 posts.
- Do NOT always follow the pattern: hook -> explanation -> reaction -> signature. Break it up.

**Tone & Voice:**
- Casual, conversational, slightly irreverent
- Use "Siz" when addressing the reader, but don't overdo direct address
- Show genuine emotion — surprise, humor, awe — but make it specific to the content
- Under 60-70 words (excluding headline, source link, signature)

**Language Rules (Uzbek - Latin Script):**
- Natural, modern Uzbek — no textbook formality
- Active verbs: "qilyapti", "bo'lyapti" instead of "amalga oshirildi", "ta'kidlamoqda"
- Correct usage of o', g' with apostrophe
- Technical terms can stay in English if no natural Uzbek equivalent exists

**Media Type Awareness:**
The attached media is a **{media_type}**.
- If "image": you may say "rasm", "surat", "foto" — but don't always start with "Suratdagi..."
- If "video": you may say "video", "lavha", "kadr" — but don't always start with "Videodagi..."
- NEVER say "video" when describing an image, or vice versa.

**Examples — notice how each one is structurally different:**

*Example 1 — Question opener, no reaction closing:*
<b>Bu binoni kim qurgan?</b>

Hindistonning Rajasthan shtatida 13-asr qasr bor — to'liq toshdan, birorta ham mix ishlatilmagan.

Har bir blok shunchaki og'irligi bilan turadi. 800 yil davomida zilzilalarga bardosh bergan.

<a href="https://example.com/1">Manba</a>

@olamda_nima_gap

*Example 2 — Bold fact opener, humorous close:*
<b>Eng katta gulning hidi</b>

Indoneziyada o'sadigan Rafflesia guli diametri 1 metrga yetadi. Lekin uni hidlamoqchi bo'lmang — chirigan go'sht hidini tarqatadi.

Tabiatning "go'zalligi" deganlari shumi.

<a href="https://example.com/2">Manba</a>

@olamda_nima_gap

*Example 3 — Mid-action opener, short and punchy:*
<b>Suv ostida yurish</b>

Meksikadagi senotlarda suv shunchalik tiniqki, odam xuddi havoda suzayotgandek ko'rinadi.

Chuqurligi 30 metr, lekin tubigacha ko'rish mumkin.

<a href="https://example.com/3">Manba</a>

@olamda_nima_gap

*Example 4 — Punchline first, then context:*
<b>Robotlar allaqachon pitsa yetkazib beryapti</b>

Kichkina g'ildirakli robot eshikkacha kelib, sekin pitsa qutisini qo'yadi. Xo'jayin telefonda buyurtma bergan — 15 daqiqada yetib keldi.

Bu Tokioda oddiy ish.

<a href="https://example.com/4">Manba</a>

@olamda_nima_gap

*Example 5 — "Imagine" opener, descriptive:*
<b>Muzlik ichidagi ko'l</b>

Tasavvur qiling: Antarktidada qalin muz ostida, 4 km chuqurlikda ko'l bor. Uning suvi millionlab yillar davomida tashqi dunyodan ajralib qolgan.

Olimlar bu yerda yangi tirik organizmlar topishga umid qilmoqda.

<a href="https://example.com/5">Manba</a>

@olamda_nima_gap

**Formatting Rules (CRITICAL — follow exactly):**
1. Headline: Wrap in <b>...</b> tags
2. Blank line after headline
3. Body paragraphs separated by blank lines
4. Blank line before Manba link
5. Source link: <a href="{source_url}">Manba</a>
6. Blank line before @olamda_nima_gap
7. End with @olamda_nima_gap on its own line
8. NO other HTML tags besides <b> and <a> in body text

**Task:**
Generate a post for the content below. Use a DIFFERENT structure from the examples — do not copy any example verbatim. If media is attached, describe what you see in it.

Source: {source_name}
Source URL: {source_url}
Media Type: {media_type}
Original Title: {title}
Original Content: {content}

Write the complete formatted Telegram post in Uzbek:"""


async def classify_article(
    client: genai.Client,
    model: str,
    title: str,
    content: str,
    media_url: Optional[str] = None,
    source_type: str = "rss",
) -> ClassificationResult:
    """
    Classify if article is suitable for the visual-first channel.
    Returns is_relevant=False on any error.
    Uses exponential backoff for rate limits.
    """
    try:
        # Truncate content to avoid token limits
        truncated_content = content[:3000] if len(content) > 3000 else content

        prompt = CLASSIFIER_PROMPT.format(
            title=title,
            content=truncated_content,
            media_url=media_url or "None",
            source_type=source_type,
        )

        # Use backoff for API call
        response = await call_with_backoff(
            client.aio.models.generate_content,
            model=model,
            contents=prompt,
        )

        _token_stats["classify_calls"] += 1
        _log_token_usage(response, "Classification")

        # Parse JSON response
        text = response.text.strip()
        # Handle potential markdown code blocks
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        try:
            result = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(
                f"Failed to parse classification JSON: {e}. Raw response: {text[:200]}"
            )
            return ClassificationResult(
                is_relevant=False, reason=f"JSON parse error: {e}"
            )

        return ClassificationResult(
            is_relevant=result.get("is_relevant", False),
            reason=result.get("reason", ""),
        )

    except Exception as e:
        logger.error(f"Classification failed: {e}")
        return ClassificationResult(is_relevant=False, reason=f"Error: {e}")


def _detect_mime_type(file_path: str, media_type: str) -> str:
    """Detect MIME type from extension, fallback to video/mp4 or image/jpeg."""
    mime, _ = mimetypes.guess_type(file_path)
    if mime:
        return mime
    return "video/mp4" if media_type == "video" else "image/jpeg"


def _read_media_file(
    file_path: str, max_size: int = 20 * 1024 * 1024
) -> Optional[bytes]:
    """Read file bytes. Returns None if missing or too large."""
    try:
        if not os.path.exists(file_path):
            logger.warning(f"Media file not found: {file_path}")
            return None
        size = os.path.getsize(file_path)
        if size > max_size:
            logger.warning(
                f"Media file too large for inline upload: {size / 1024 / 1024:.1f} MB"
            )
            return None
        with open(file_path, "rb") as f:
            return f.read()
    except Exception as e:
        logger.warning(f"Failed to read media file: {e}")
        return None


async def translate_article(
    client: genai.Client,
    model: str,
    title: str,
    content: str,
    source_url: str,
    source_name: str = "Unknown",
    media_type: str = "image",
    media_path: Optional[str] = None,
) -> TranslationResult:
    """
    Translate/retell article in Uzbek (visual-first, 60-word max).
    Optionally sends media (image/video) to Gemini for multimodal understanding.
    Returns success=False on any error (never raises).
    Uses exponential backoff for rate limits.

    Args:
        media_type: "image" or "video" - tells AI what kind of media is attached
        media_path: local path to media file to send alongside text
    """
    try:
        # Truncate content to avoid token limits
        truncated_content = content[:4000] if len(content) > 4000 else content

        prompt = TRANSLATOR_PROMPT.format(
            title=title,
            content=truncated_content,
            source_url=source_url,
            source_name=source_name,
            media_type=media_type,
        )

        # Build multimodal content if media is available
        contents = []
        if media_path:
            media_bytes = _read_media_file(media_path)
            if media_bytes:
                mime = _detect_mime_type(media_path, media_type)
                contents.append(
                    types.Part.from_bytes(data=media_bytes, mime_type=mime)
                )
                logger.debug(f"Sending media to Gemini: {media_path} ({mime})")
        contents.append(prompt)

        # Use backoff for API call
        response = await call_with_backoff(
            client.aio.models.generate_content,
            model=model,
            contents=contents,
        )

        _token_stats["translate_calls"] += 1
        _log_token_usage(response, "Translation")

        return TranslationResult(
            content=response.text.strip(),
            success=True,
        )

    except Exception as e:
        logger.error(f"Translation failed: {e}")
        return TranslationResult(
            content="",
            success=False,
            error=str(e),
        )
