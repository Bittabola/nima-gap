"""RSS and Reddit content fetching."""

import logging
from dataclasses import dataclass
from typing import Optional

import feedparser
import httpx

logger = logging.getLogger(__name__)


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



async def fetch_rss(
    http_client: httpx.AsyncClient, url: str
) -> list[FetchedArticle]:
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

            # Extract image
            image_url = None
            if hasattr(entry, "media_content") and entry.media_content:
                image_url = entry.media_content[0].get("url")
            elif hasattr(entry, "enclosures") and entry.enclosures:
                for enc in entry.enclosures:
                    if enc.get("type", "").startswith("image/"):
                        image_url = enc.get("href")
                        break

            articles.append(
                FetchedArticle(
                    url=entry.link,
                    title=entry.get("title", ""),
                    content=content,
                    image_url=image_url,
                )
            )

    except Exception as e:
        logger.error(f"Failed to fetch RSS {url}: {e}")

    return articles


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

            permalink = post_data.get("permalink", "")
            articles.append(
                FetchedArticle(
                    url=f"https://reddit.com{permalink}",
                    title=post_data.get("title", ""),
                    content=selftext,
                    image_url=None,  # Text posts don't have images
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
