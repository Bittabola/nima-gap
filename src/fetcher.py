"""RSS and Reddit content fetching."""

import html
import logging
import re
from dataclasses import dataclass
from typing import Optional

import feedparser
import httpx

logger = logging.getLogger(__name__)

# Minimum image dimensions to consider (skip tiny icons/badges)
MIN_IMAGE_WIDTH = 200
MIN_IMAGE_HEIGHT = 200


def strip_html(text: str) -> str:
    """
    Remove HTML tags and decode entities from text.
    Returns clean plain text suitable for Telegram messages.
    """
    if not text:
        return ""
    # Remove HTML tags
    clean = re.sub(r"<[^>]+>", " ", text)
    # Decode HTML entities (&amp; -> &, &lt; -> <, etc.)
    clean = html.unescape(clean)
    # Normalize whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def extract_image_from_html(html_content: str) -> Optional[str]:
    """
    Extract the first significant image URL from HTML content.
    Looks for <img> tags and filters out small/icon images.
    """
    if not html_content:
        return None

    # Find all img tags with src attribute
    img_pattern = r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>'
    matches = re.findall(img_pattern, html_content, re.IGNORECASE)

    for url in matches:
        # Skip common icon/badge patterns
        if any(
            skip in url.lower()
            for skip in [
                "icon",
                "logo",
                "badge",
                "avatar",
                "emoji",
                "button",
                "pixel",
                "tracking",
                "ads",
                "banner",
                "sprite",
                "1x1",
                "spacer",
            ]
        ):
            continue

        # Skip data URLs
        if url.startswith("data:"):
            continue

        # Skip very short URLs (likely relative paths to icons)
        if len(url) < 20:
            continue

        return url

    return None


def extract_image_from_media_thumbnail(entry) -> Optional[str]:
    """Extract image from media:thumbnail RSS element."""
    # Try media_thumbnail (list of thumbnails)
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        # Get the largest thumbnail
        thumbnails = entry.media_thumbnail
        if isinstance(thumbnails, list) and len(thumbnails) > 0:
            # Sort by width if available, take largest
            best = max(
                thumbnails,
                key=lambda t: int(t.get("width", 0)) if t.get("width") else 0,
            )
            url = best.get("url")
            if url:
                return url

    return None


def extract_image_from_entry(entry, content: str) -> Optional[str]:
    """
    Extract image URL from RSS entry using multiple methods.
    Priority:
    1. media:content
    2. media:thumbnail
    3. enclosures
    4. <img> tags in content
    """
    image_url = None

    # 1. media:content (most reliable for media RSS)
    if hasattr(entry, "media_content") and entry.media_content:
        for media in entry.media_content:
            media_type = media.get("type", "")
            if media_type.startswith("image/") or not media_type:
                url = media.get("url")
                if url:
                    image_url = url
                    break

    # 2. media:thumbnail
    if not image_url:
        image_url = extract_image_from_media_thumbnail(entry)

    # 3. enclosures
    if not image_url and hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            enc_type = enc.get("type", "")
            if enc_type.startswith("image/"):
                url = enc.get("href") or enc.get("url")
                if url:
                    image_url = url
                    break

    # 4. Extract from content HTML (last resort)
    if not image_url:
        # Try content:encoded first (usually has full HTML)
        html_content = ""
        if hasattr(entry, "content") and entry.content:
            html_content = entry.content[0].value
        elif hasattr(entry, "summary"):
            html_content = entry.summary
        elif hasattr(entry, "description"):
            html_content = entry.description

        if html_content:
            image_url = extract_image_from_html(html_content)

    return image_url


@dataclass
class FetchedArticle:
    """Article fetched from a source before processing."""

    url: str
    title: str
    content: str
    image_url: Optional[str]


def create_http_client() -> httpx.AsyncClient:
    """Create HTTP client with retries and timeout."""
    transport = httpx.AsyncHTTPTransport(retries=3)
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
        headers={"User-Agent": "Olamda-Nima-Gap/1.0"},
    )


async def fetch_rss(http_client: httpx.AsyncClient, url: str) -> list[FetchedArticle]:
    """Fetch articles from RSS feed."""
    articles = []

    try:
        response = await http_client.get(url)
        response.raise_for_status()

        feed = feedparser.parse(response.text)

        for entry in feed.entries[:20]:  # Limit to recent 20
            # Extract content
            content = ""
            if hasattr(entry, "content") and entry.content:
                content = entry.content[0].value
            elif hasattr(entry, "summary"):
                content = entry.summary
            elif hasattr(entry, "description"):
                content = entry.description

            # Extract image using enhanced extraction
            image_url = extract_image_from_entry(entry, content)

            articles.append(
                FetchedArticle(
                    url=entry.link,
                    title=strip_html(entry.get("title", "")),
                    content=strip_html(content),
                    image_url=image_url,
                )
            )

    except Exception as e:
        logger.error(f"Failed to fetch RSS {url}: {e}")

    return articles


def extract_reddit_image(post_data: dict) -> Optional[str]:
    """
    Extract the best image from a Reddit post.
    Tries preview images first, then thumbnail.
    """
    # 1. Try preview images (higher quality)
    preview = post_data.get("preview", {})
    images = preview.get("images", [])
    if images:
        # Get the first image's source (original resolution)
        source = images[0].get("source", {})
        url = source.get("url")
        if url:
            # Reddit escapes URLs in JSON, unescape them
            return html.unescape(url)

        # Try resolutions if source not available
        resolutions = images[0].get("resolutions", [])
        if resolutions:
            # Get highest resolution
            best = max(resolutions, key=lambda r: r.get("width", 0))
            url = best.get("url")
            if url:
                return html.unescape(url)

    # 2. Try thumbnail (lower quality fallback)
    thumbnail = post_data.get("thumbnail")
    if thumbnail and thumbnail not in ("self", "default", "nsfw", "spoiler", ""):
        # Validate it's a URL
        if thumbnail.startswith("http"):
            return thumbnail

    return None


async def fetch_reddit(
    http_client: httpx.AsyncClient,
    subreddit_name: str,
    limit: int = 25,
) -> list[FetchedArticle]:
    """Fetch text posts from a subreddit using Reddit's public .json endpoint."""
    articles = []

    try:
        url = f"https://www.reddit.com/r/{subreddit_name}/hot.json?limit={limit}"
        response = await http_client.get(url)
        response.raise_for_status()

        data = response.json()

        for post in data.get("data", {}).get("children", []):
            post_data = post.get("data", {})

            # Skip non-text posts (images, videos, links)
            selftext = post_data.get("selftext", "")
            if not selftext:
                continue
            if selftext in ("[removed]", "[deleted]"):
                continue

            # Extract image from preview/thumbnail
            image_url = extract_reddit_image(post_data)

            permalink = post_data.get("permalink", "")
            articles.append(
                FetchedArticle(
                    url=f"https://reddit.com{permalink}",
                    title=post_data.get("title", ""),
                    content=selftext,
                    image_url=image_url,
                )
            )

    except Exception as e:
        logger.error(f"Failed to fetch r/{subreddit_name}: {e}")

    return articles


async def fetch_source(
    source: dict,
    http_client: httpx.AsyncClient,
) -> list[FetchedArticle]:
    """Fetch articles from a source based on its type."""
    source_type = source.get("type", "rss")

    if source_type == "reddit":
        subreddit = source.get("subreddit")
        if subreddit:
            return await fetch_reddit(http_client, subreddit)
    else:  # rss
        url = source.get("url")
        if url:
            return await fetch_rss(http_client, url)

    return []
