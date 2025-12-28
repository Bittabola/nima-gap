"""Configuration loader for environment variables and sources."""

from dataclasses import dataclass
from pathlib import Path
import os

import yaml
from dotenv import load_dotenv


@dataclass
class Config:
    """Application configuration."""

    # Telegram
    telegram_bot_token: str
    telegram_channel_id: str
    telegram_admin_id: int

    # Reddit
    reddit_client_id: str
    reddit_client_secret: str

    # Gemini
    gemini_api_key: str

    # Paths and settings
    database_path: str
    fetch_interval_hours: int
    publish_gap_minutes: int
    log_level: str

    # Sources from YAML
    sources: list[dict]


def load_config() -> Config:
    """
    Load configuration from .env and sources.yaml.

    Raises:
        KeyError: If required environment variables are missing.
        FileNotFoundError: If sources.yaml doesn't exist.
    """
    load_dotenv()

    # Load sources.yaml
    sources_path = Path(__file__).parent.parent / "config" / "sources.yaml"
    with open(sources_path) as f:
        sources_data = yaml.safe_load(f)

    return Config(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_channel_id=os.environ["TELEGRAM_CHANNEL_ID"],
        telegram_admin_id=int(os.environ["TELEGRAM_ADMIN_ID"]),
        reddit_client_id=os.environ["REDDIT_CLIENT_ID"],
        reddit_client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        gemini_api_key=os.environ["GEMINI_API_KEY"],
        database_path=os.getenv("DATABASE_PATH", "data/olamda.db"),
        fetch_interval_hours=int(os.getenv("FETCH_INTERVAL_HOURS", "3")),
        publish_gap_minutes=int(os.getenv("PUBLISH_GAP_MINUTES", "60")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        sources=sources_data.get("sources", []),
    )
