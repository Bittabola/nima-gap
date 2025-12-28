"""Image downloading, validation and caching."""

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Supported image types
VALID_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
}

# File extensions by content type
EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

# Limits
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
MIN_IMAGE_SIZE = 1024  # 1 KB (skip tiny images/icons)
REQUEST_TIMEOUT = 30.0


@dataclass
class ImageResult:
    """Result of image download attempt."""

    success: bool
    local_path: Optional[str] = None
    error: Optional[str] = None
    original_url: Optional[str] = None


def get_images_dir(data_dir: str = "data") -> Path:
    """Get or create the images directory."""
    images_dir = Path(data_dir) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    return images_dir


def generate_filename(url: str, content_type: str) -> str:
    """Generate a unique filename based on URL hash."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    extension = EXTENSIONS.get(content_type, ".jpg")
    return f"{url_hash}{extension}"


async def validate_image_url(
    http_client: httpx.AsyncClient,
    url: str,
) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Validate image URL with HEAD request.
    Returns (is_valid, content_type, error_message).
    """
    if not url:
        return False, None, "No URL provided"

    # Basic URL validation
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, None, f"Invalid scheme: {parsed.scheme}"
    except Exception as e:
        return False, None, f"Invalid URL: {e}"

    try:
        response = await http_client.head(
            url,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )

        if response.status_code != 200:
            return False, None, f"HTTP {response.status_code}"

        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        if content_type not in VALID_CONTENT_TYPES:
            return False, None, f"Invalid content type: {content_type}"

        # Check content length if available
        content_length = response.headers.get("content-length")
        if content_length:
            size = int(content_length)
            if size > MAX_IMAGE_SIZE:
                return False, None, f"Image too large: {size} bytes"
            if size < MIN_IMAGE_SIZE:
                return False, None, f"Image too small: {size} bytes"

        return True, content_type, None

    except httpx.TimeoutException:
        return False, None, "Request timeout"
    except httpx.RequestError as e:
        return False, None, f"Request failed: {e}"
    except Exception as e:
        return False, None, f"Validation error: {e}"


async def download_image(
    http_client: httpx.AsyncClient,
    url: str,
    data_dir: str = "data",
) -> ImageResult:
    """
    Download and cache an image locally.
    Returns ImageResult with local path on success.
    """
    if not url:
        return ImageResult(success=False, error="No URL provided", original_url=url)

    # Validate first
    is_valid, content_type, error = await validate_image_url(http_client, url)
    if not is_valid:
        logger.warning(f"Image validation failed for {url}: {error}")
        return ImageResult(success=False, error=error, original_url=url)

    # Generate local path
    images_dir = get_images_dir(data_dir)
    filename = generate_filename(url, content_type)
    local_path = images_dir / filename

    # Check if already cached
    if local_path.exists():
        logger.debug(f"Image already cached: {local_path}")
        return ImageResult(
            success=True,
            local_path=str(local_path),
            original_url=url,
        )

    # Download
    try:
        response = await http_client.get(
            url,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()

        # Verify size
        content = response.content
        if len(content) > MAX_IMAGE_SIZE:
            return ImageResult(
                success=False,
                error=f"Image too large: {len(content)} bytes",
                original_url=url,
            )
        if len(content) < MIN_IMAGE_SIZE:
            return ImageResult(
                success=False,
                error=f"Image too small: {len(content)} bytes",
                original_url=url,
            )

        # Save to disk
        with open(local_path, "wb") as f:
            f.write(content)

        logger.info(f"Downloaded image: {url} -> {local_path}")
        return ImageResult(
            success=True,
            local_path=str(local_path),
            original_url=url,
        )

    except httpx.TimeoutException:
        return ImageResult(success=False, error="Download timeout", original_url=url)
    except httpx.RequestError as e:
        return ImageResult(
            success=False, error=f"Download failed: {e}", original_url=url
        )
    except OSError as e:
        return ImageResult(success=False, error=f"Save failed: {e}", original_url=url)
    except Exception as e:
        return ImageResult(
            success=False, error=f"Unexpected error: {e}", original_url=url
        )


def get_cached_image_path(url: str, data_dir: str = "data") -> Optional[str]:
    """
    Check if image is already cached and return path.
    Returns None if not cached.
    """
    if not url:
        return None

    images_dir = get_images_dir(data_dir)

    # Try each possible extension
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    for ext in EXTENSIONS.values():
        path = images_dir / f"{url_hash}{ext}"
        if path.exists():
            return str(path)

    return None


def cleanup_old_images(data_dir: str = "data", max_age_days: int = 30) -> int:
    """
    Remove cached images older than max_age_days.
    Returns number of files removed.
    """
    import time

    images_dir = get_images_dir(data_dir)
    max_age_seconds = max_age_days * 24 * 60 * 60
    current_time = time.time()
    removed = 0

    for path in images_dir.iterdir():
        if path.is_file():
            age = current_time - path.stat().st_mtime
            if age > max_age_seconds:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass

    if removed > 0:
        logger.info(f"Cleaned up {removed} old cached images")

    return removed
