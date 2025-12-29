"""SQLite database connection and operations."""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


@dataclass
class Article:
    """Article record from database."""

    id: int
    source_name: str
    original_url: str
    original_title: str
    original_summary: str
    content_hash: Optional[str]
    image_url: Optional[str]
    local_image_path: Optional[str]
    local_video_path: Optional[str]
    media_type: str  # "image" or "video"
    uzbek_content: Optional[str]
    status: str
    created_at: str
    published_at: Optional[str]


# Tracking params to strip from URLs for normalization
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "source",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


def normalize_url(url: str) -> str:
    """
    Normalize URL by stripping tracking parameters and standardizing format.

    - Removes common tracking params (utm_*, fbclid, etc.)
    - Lowercases scheme and host
    - Removes trailing slashes
    - Handles reddit.com variants (old.reddit.com, www.reddit.com)
    """
    try:
        parsed = urlparse(url)

        # Normalize host
        host = parsed.netloc.lower()
        # Standardize reddit URLs
        if host in ("old.reddit.com", "www.reddit.com", "np.reddit.com"):
            host = "reddit.com"
        if host.startswith("www."):
            host = host[4:]

        # Filter out tracking params
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        filtered_params = {
            k: v for k, v in query_params.items() if k.lower() not in TRACKING_PARAMS
        }
        new_query = urlencode(filtered_params, doseq=True) if filtered_params else ""

        # Rebuild URL
        normalized = urlunparse(
            (
                parsed.scheme.lower(),
                host,
                parsed.path.rstrip("/") or "/",
                parsed.params,
                new_query,
                "",  # Remove fragment
            )
        )

        return normalized
    except Exception:
        return url  # Return original if parsing fails


def compute_content_hash(title: str, content: str) -> str:
    """
    Compute a hash of the content for duplicate detection.
    Uses first 500 chars of content + title to catch similar articles.
    """
    # Normalize: lowercase, remove extra whitespace
    normalized = f"{title.lower()} {content[:500].lower()}"
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


def title_similarity(title1: str, title2: str) -> float:
    """
    Calculate similarity ratio between two titles (0.0 to 1.0).
    Uses SequenceMatcher for fuzzy matching.
    """
    return SequenceMatcher(None, title1.lower().strip(), title2.lower().strip()).ratio()


def init_database(db_path: str) -> sqlite3.Connection:
    """Initialize database and create tables."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            original_url TEXT NOT NULL UNIQUE,
            original_title TEXT NOT NULL,
            original_summary TEXT NOT NULL,
            content_hash TEXT,
            image_url TEXT,
            local_image_path TEXT,
            local_video_path TEXT,
            media_type TEXT NOT NULL DEFAULT 'image',
            uzbek_content TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            published_at TEXT
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_articles_status_created
        ON articles(status, created_at)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_articles_content_hash
        ON articles(content_hash)
    """)

    # Table to track all seen URLs (including skipped/failed)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_urls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_url TEXT NOT NULL UNIQUE,
            original_url TEXT NOT NULL,
            content_hash TEXT,
            status TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_seen_urls_content_hash
        ON seen_urls(content_hash)
    """)

    # Migration: add content_hash column if missing (for existing databases)
    try:
        conn.execute("SELECT content_hash FROM articles LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE articles ADD COLUMN content_hash TEXT")

    # Migration: add local_image_path column if missing
    try:
        conn.execute("SELECT local_image_path FROM articles LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE articles ADD COLUMN local_image_path TEXT")

    # Migration: add local_video_path column if missing
    try:
        conn.execute("SELECT local_video_path FROM articles LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE articles ADD COLUMN local_video_path TEXT")

    # Migration: add media_type column if missing
    try:
        conn.execute("SELECT media_type FROM articles LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE articles ADD COLUMN media_type TEXT NOT NULL DEFAULT 'image'"
        )

    conn.commit()
    return conn


def article_exists(conn: sqlite3.Connection, url: str) -> bool:
    """Check if article URL already exists (uses normalized URL)."""
    normalized = normalize_url(url)
    cursor = conn.execute(
        "SELECT 1 FROM articles WHERE original_url = ? OR original_url = ?",
        (url, normalized),
    )
    return cursor.fetchone() is not None


def url_seen(conn: sqlite3.Connection, url: str) -> bool:
    """Check if URL has been seen before (in seen_urls table)."""
    normalized = normalize_url(url)
    cursor = conn.execute(
        "SELECT 1 FROM seen_urls WHERE normalized_url = ?", (normalized,)
    )
    return cursor.fetchone() is not None


def content_hash_exists(conn: sqlite3.Connection, content_hash: str) -> bool:
    """Check if content hash already exists (duplicate content detection)."""
    # Check in articles
    cursor = conn.execute(
        "SELECT 1 FROM articles WHERE content_hash = ?", (content_hash,)
    )
    if cursor.fetchone():
        return True

    # Check in seen_urls
    cursor = conn.execute(
        "SELECT 1 FROM seen_urls WHERE content_hash = ?", (content_hash,)
    )
    return cursor.fetchone() is not None


def find_similar_title(
    conn: sqlite3.Connection, title: str, threshold: float = 0.85
) -> Optional[Article]:
    """
    Find an article with a similar title (above threshold).
    Returns the first matching article or None.
    """
    cursor = conn.execute(
        "SELECT * FROM articles WHERE status IN ('pending', 'approved', 'published')"
    )
    for row in cursor:
        existing_title = row["original_title"]
        if title_similarity(title, existing_title) >= threshold:
            return Article(**dict(row))
    return None


def mark_url_seen(
    conn: sqlite3.Connection,
    url: str,
    content_hash: Optional[str],
    status: str,
    reason: Optional[str] = None,
) -> None:
    """Mark a URL as seen with its status and reason."""
    normalized = normalize_url(url)
    try:
        conn.execute(
            """
            INSERT INTO seen_urls (normalized_url, original_url, content_hash, status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                normalized,
                url,
                content_hash,
                status,
                reason,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Already exists, update it
        conn.execute(
            """
            UPDATE seen_urls SET status = ?, reason = ?, content_hash = ?
            WHERE normalized_url = ?
            """,
            (status, reason, content_hash, normalized),
        )
        conn.commit()


def create_article(
    conn: sqlite3.Connection,
    source_name: str,
    original_url: str,
    original_title: str,
    original_summary: str,
    content_hash: Optional[str],
    image_url: Optional[str],
    local_image_path: Optional[str],
    local_video_path: Optional[str],
    media_type: str,
    uzbek_content: str,
) -> int:
    """Create article with status 'pending'. Returns article ID."""
    cursor = conn.execute(
        """
        INSERT INTO articles
        (source_name, original_url, original_title, original_summary,
         content_hash, image_url, local_image_path, local_video_path,
         media_type, uzbek_content, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            source_name,
            original_url,
            original_title,
            original_summary,
            content_hash,
            image_url,
            local_image_path,
            local_video_path,
            media_type,
            uzbek_content,
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def get_article_by_id(conn: sqlite3.Connection, article_id: int) -> Optional[Article]:
    """Get article by ID."""
    cursor = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
    row = cursor.fetchone()
    return Article(**dict(row)) if row else None


def update_article_status(
    conn: sqlite3.Connection, article_id: int, status: str
) -> None:
    """Update article status."""
    conn.execute(
        "UPDATE articles SET status = ? WHERE id = ?",
        (status, article_id),
    )
    conn.commit()


def get_next_publishable(conn: sqlite3.Connection) -> Optional[Article]:
    """Get oldest approved article."""
    cursor = conn.execute(
        """
        SELECT * FROM articles
        WHERE status = 'approved'
        ORDER BY created_at ASC
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    return Article(**dict(row)) if row else None


def mark_published(conn: sqlite3.Connection, article_id: int) -> None:
    """Mark article as published with timestamp."""
    conn.execute(
        """
        UPDATE articles
        SET status = 'published', published_at = ?
        WHERE id = ?
        """,
        (datetime.utcnow().isoformat(), article_id),
    )
    conn.commit()


def get_last_publish_time(conn: sqlite3.Connection) -> Optional[datetime]:
    """Get timestamp of most recently published article."""
    cursor = conn.execute(
        "SELECT MAX(published_at) FROM articles WHERE status = 'published'"
    )
    row = cursor.fetchone()
    if row and row[0]:
        return datetime.fromisoformat(row[0])
    return None


def get_pending_count(conn: sqlite3.Connection) -> int:
    """Count pending articles."""
    cursor = conn.execute("SELECT COUNT(*) FROM articles WHERE status = 'pending'")
    return cursor.fetchone()[0]


def get_pending_articles(conn: sqlite3.Connection) -> list[Article]:
    """Get all pending articles ordered by creation date."""
    cursor = conn.execute(
        "SELECT * FROM articles WHERE status = 'pending' ORDER BY created_at ASC"
    )
    return [Article(**dict(row)) for row in cursor.fetchall()]


def get_approved_count(conn: sqlite3.Connection) -> int:
    """Count approved articles waiting to publish."""
    cursor = conn.execute("SELECT COUNT(*) FROM articles WHERE status = 'approved'")
    return cursor.fetchone()[0]
