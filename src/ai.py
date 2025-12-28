"""Gemini AI for classification and translation."""

import json
import logging
from dataclasses import dataclass
from typing import Optional

import google.generativeai as genai

logger = logging.getLogger(__name__)


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


def init_gemini(api_key: str) -> genai.GenerativeModel:
    """Initialize Gemini client with flash model."""
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-1.5-flash")


CLASSIFIER_PROMPT = """You are a content classifier for a feel-good news channel.

Analyze the following article and determine if it's a feel-good human story.

INCLUDE stories about:
- Personal triumphs and achievements
- Acts of kindness and generosity
- Overcoming hardship or adversity
- Funny or heartwarming moments
- Community connection and support
- Inspiring individuals

EXCLUDE stories about:
- Politics or political figures
- Religion or religious conflicts
- Tragedy, disasters, or death
- Violence or crime
- Controversial or divisive topics
- Celebrity gossip
- Product promotions

ALSO EXCLUDE if:
- Content is too brief (less than a few sentences)
- Content lacks enough context to understand the story

Article Title: {title}

Article Content: {content}

Respond ONLY with valid JSON:
{{"is_relevant": true/false, "reason": "brief explanation"}}"""


TRANSLATOR_PROMPT = """You are an editor for a popular Uzbek Telegram channel "Olamda nima gap?".

Task: Retell the following story in Uzbek for a Telegram post. Do NOT translate word-for-word. Instead, creatively retell from third-person perspective.

Requirements:
1. Start with a bold hook headline (use <b>text</b> for bold)
2. Write 100-150 word summary focusing on emotion
3. End with an italic takeaway sentence (use <i>text</i> for italic)
4. Add source attribution
5. Include 2-3 relevant hashtags

Use Uzbek idioms where appropriate (sabr, oqibat, mehnat).
For surprising endings, use spoiler formatting: <tg-spoiler>text</tg-spoiler>

Source URL: {source_url}
Original Title: {title}
Original Content: {content}

Write the complete formatted Telegram post in Uzbek:"""


async def classify_article(
    model: genai.GenerativeModel,
    title: str,
    content: str,
) -> ClassificationResult:
    """
    Classify if article is a feel-good story.
    Returns is_relevant=False on any error.
    """
    try:
        # Truncate content to avoid token limits
        truncated_content = content[:3000] if len(content) > 3000 else content

        prompt = CLASSIFIER_PROMPT.format(title=title, content=truncated_content)
        response = await model.generate_content_async(prompt)

        # Parse JSON response
        text = response.text.strip()
        # Handle potential markdown code blocks
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        result = json.loads(text)
        return ClassificationResult(
            is_relevant=result.get("is_relevant", False),
            reason=result.get("reason", ""),
        )

    except Exception as e:
        logger.error(f"Classification failed: {e}")
        return ClassificationResult(is_relevant=False, reason=f"Error: {e}")


async def translate_article(
    model: genai.GenerativeModel,
    title: str,
    content: str,
    source_url: str,
) -> TranslationResult:
    """
    Translate/retell article in Uzbek.
    Returns success=False on any error (never raises).
    """
    try:
        # Truncate content to avoid token limits
        truncated_content = content[:4000] if len(content) > 4000 else content

        prompt = TRANSLATOR_PROMPT.format(
            title=title,
            content=truncated_content,
            source_url=source_url,
        )
        response = await model.generate_content_async(prompt)

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
