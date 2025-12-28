"""SQLite database connection and operations."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Article:
    """Article record from database."""

    id: int
    source_name: str
    original_url: str
    original_title: str
    original_summary: str
    image_url: Optional[str]
    uzbek_content: Optional[str]
    status: str
    created_at: str
    published_at: Optional[str]


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
            image_url TEXT,
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

    conn.commit()
    return conn


def article_exists(conn: sqlite3.Connection, url: str) -> bool:
    """Check if article URL already exists."""
    cursor = conn.execute(
        "SELECT 1 FROM articles WHERE original_url = ?", (url,)
    )
    return cursor.fetchone() is not None


def create_article(
    conn: sqlite3.Connection,
    source_name: str,
    original_url: str,
    original_title: str,
    original_summary: str,
    image_url: Optional[str],
    uzbek_content: str,
) -> int:
    """Create article with status 'pending'. Returns article ID."""
    cursor = conn.execute(
        """
        INSERT INTO articles
        (source_name, original_url, original_title, original_summary,
         image_url, uzbek_content, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            source_name,
            original_url,
            original_title,
            original_summary,
            image_url,
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


def get_approved_count(conn: sqlite3.Connection) -> int:
    """Count approved articles waiting to publish."""
    cursor = conn.execute("SELECT COUNT(*) FROM articles WHERE status = 'approved'")
    return cursor.fetchone()[0]
