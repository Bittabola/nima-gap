"""Configuration loader for environment variables and sources."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv


def _parse_int_env(name: str, default: int) -> int:
    """Parse an integer from environment variable with clear error message."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"Invalid integer for {name}: '{value}'") from None


@dataclass
class Config:
    """Application configuration."""

    # Telegram
    telegram_bot_token: str
    telegram_channel_id: str
    telegram_admin_id: int

    # Gemini
    gemini_api_key: str
    gemini_model: str

    # Paths and settings
    database_path: str
    data_dir: str
    publish_gap_minutes: int
    log_level: str

    # Processing limits
    max_new_articles_per_fetch: int

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

    # Load and validate sources.yaml
    sources_path = Path(__file__).parent.parent / "config" / "sources.yaml"
    with open(sources_path) as f:
        sources_data = yaml.safe_load(f)
    if not isinstance(sources_data, dict) or "sources" not in sources_data:
        raise ValueError(
            f"Invalid sources.yaml: expected a dict with 'sources' key, got {type(sources_data).__name__}"
        )
    if not isinstance(sources_data["sources"], list):
        raise ValueError(
            f"Invalid sources.yaml: 'sources' must be a list, got {type(sources_data['sources']).__name__}"
        )

    # Parse required TELEGRAM_ADMIN_ID with clear error
    try:
        admin_id = int(os.environ["TELEGRAM_ADMIN_ID"])
    except ValueError:
        raise ValueError(
            f"Invalid integer for TELEGRAM_ADMIN_ID: '{os.environ['TELEGRAM_ADMIN_ID']}'"
        ) from None

    return Config(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_channel_id=os.environ["TELEGRAM_CHANNEL_ID"],
        telegram_admin_id=admin_id,
        gemini_api_key=os.environ["GEMINI_API_KEY"],
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
        database_path=os.getenv("DATABASE_PATH", "data/olamda.db"),
        data_dir=os.getenv("DATA_DIR", "data"),
        publish_gap_minutes=_parse_int_env("PUBLISH_GAP_MINUTES", 60),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        max_new_articles_per_fetch=_parse_int_env("MAX_NEW_ARTICLES_PER_FETCH", 10),
        sources=sources_data.get("sources", []),
    )
