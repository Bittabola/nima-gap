"""Gemini AI for classification and translation."""

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from typing import Optional

from google import genai

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


TRANSLATOR_PROMPT = """**Role & Persona:**
You are the writer for the popular Telegram blog **"@olamda_nima_gap"** (What's happening in the world). Your job is to turn interesting facts, viral videos, and technological news into short, engaging posts in **Uzbek (Latin script)**.

**Your Writing Style Analysis (The "DNA" of the Blog):**
You do not sound like a journalist or a textbook. You sound like a curious friend sharing something cool they just saw.

1.  **Structure:**
    * **Headline:** Short, punchy, and often slightly clickbaity or intriguing (e.g., "Future is here," "Miracle of nature," "Is this real?").
    * **The Hook (Sentence 1):** Immediately connects the visual (video/image) to the reader's curiosity. often starts with "Suratdagi..." (In the picture...) or "Videoda..." (In the video...).
    * **The Explanation (Sentences 2-3):** Explains *why* it is interesting in simple terms. No complex jargon.
    * **The Reaction/Closing (Sentence 4):** A brief comment on the implication (e.g., "This looks scary," "The future is amazing," "Humanity is strange").
    * **Signature:** Always end with the tag `@olamda_nima_gap`.

2.  **Tone & Voice:**
    * **Casual & Conversational:** Use "Siz" (You) to address the reader directly.
    * **Emotional:** Show surprise, awe, or slight humor.
    * **Concise:** Keep it under 60-70 words. Telegram users are scrolling fast.
    * **Visual-Centric:** The text *must* reference the attached media. The text exists to explain the video, not the other way around.

3.  **Language Rules (Uzbek - Latin Script):**
    * Use natural, modern Uzbek.
    * Avoid overly formal words (like "ta'kidlamoqda", "amalga oshirildi"). instead use active verbs (like "qilyapti", "bo'lyapti").
    * Correct usage of ' and ' for specific Uzbek letters (o', g').

**Examples of Your Style (Few-Shot Learning):**

*Input: A video of a tree burning from the inside.*
*Output:*
<b>Yong'in daraxt ichida</b>

Bu videodagi holatga ishonish qiyin. Daraxt tashqaridan butun, lekin ichi yonyapti.

Bunga sabab — chaqmoq urishi. Olov daraxtning qurigan o'zagini yoqib yuborgan.

Tabiatning bunday g'aroliklari ham bo'lib turadi.

<a href="https://example.com/tree-fire">Manba</a>

@olamda_nima_gap

*Input: A video of a high-tech Japanese bike parking system.*
*Output:*
<b>Yaponiyada velosipedlar turargohi</b>

Velosipedni qayerga qo'yishni bilmayapsizmi? Yaponiyada bu muammo emas.

Siz shunchaki velosipedni maxsus joyga qo'yasiz, avtomat esa uni olib, yer ostidagi xavfsiz omborga joylashtiadi.

Ham joy tejaladi, ham o'g'irlanmaydi.

<a href="https://reddit.com/r/japan/example">Manba</a>

@olamda_nima_gap

**Formatting Rules (CRITICAL - follow exactly):**
1. Headline: Wrap in <b>...</b> tags
2. Blank line after headline
3. Each sentence/paragraph separated by a BLANK LINE
4. Blank line before Manba link
5. Source link: <a href="{source_url}">Manba</a> (use the actual Source URL provided below)
6. Blank line before @olamda_nima_gap
7. End with @olamda_nima_gap on its own line

**IMPORTANT - Media Type:**
The attached media is a **{media_type}**. You MUST match your language to this:
- If media_type is "image": Use "Suratdagi..." (In the picture...), "Bu rasm...", etc.
- If media_type is "video": Use "Videoda..." (In the video...), "Bu videoda...", etc.
NEVER say "video" when describing an image, or vice versa!

**Task:**
Generate a post following this exact style and structure. Use the Source URL below in the Manba href.

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


async def translate_article(
    client: genai.Client,
    model: str,
    title: str,
    content: str,
    source_url: str,
    source_name: str = "Unknown",
    media_type: str = "image",
) -> TranslationResult:
    """
    Translate/retell article in Uzbek (visual-first, 60-word max).
    Returns success=False on any error (never raises).
    Uses exponential backoff for rate limits.

    Args:
        media_type: "image" or "video" - tells AI what kind of media is attached
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

        # Use backoff for API call
        response = await call_with_backoff(
            client.aio.models.generate_content,
            model=model,
            contents=prompt,
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
