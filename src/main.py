"""Main entry point and scheduling loop."""

import asyncio
import logging
from datetime import datetime, timedelta

from .ai import (
    classify_article,
    get_token_stats,
    init_gemini,
    reset_token_stats,
    translate_article,
)
from .bot import (
    create_bot,
    notify_admin_error,
    publish_article,
    send_approval_request,
    send_fetch_summary,
)
from .config import load_config
from .database import (
    article_exists,
    compute_content_hash,
    content_hash_exists,
    create_article,
    find_similar_title,
    get_article_by_id,
    get_last_publish_time,
    get_next_publishable,
    get_pending_count,
    init_database,
    mark_published,
    mark_url_seen,
    url_seen,
)
from .fetcher import FetchedArticle, create_http_client, fetch_source
from .media import cleanup_old_images, cleanup_old_videos, download_image, download_video


async def fetch_job(
    config,
    db_conn,
    http_client,
    gemini_model,
    bot,
) -> int:
    """
    Fetch and process articles from all sources.
    Returns number of unprocessed articles remaining.
    """
    logger = logging.getLogger(__name__)
    logger.info("Starting fetch job...")

    # Reset token stats for this cycle
    reset_token_stats()

    errors = []
    new_articles = 0
    skipped_duplicates = 0
    skipped_irrelevant = 0
    failed = 0
    remaining = 0

    # Collect articles from each source separately
    articles_by_source: list[list[tuple[str, FetchedArticle]]] = []
    for source in config.sources:
        source_name = source.get("name", "Unknown")
        try:
            articles = await fetch_source(source, http_client)
            logger.info(f"Fetched {len(articles)} from {source_name}")
            source_articles = [(source_name, article) for article in articles]
            if source_articles:
                articles_by_source.append(source_articles)
        except Exception as e:
            error_msg = f"{source_name}: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

    # Interleave articles from different sources (round-robin)
    # This ensures we don't process all articles from one source consecutively
    all_articles = []
    while articles_by_source:
        # Take one article from each source in turn
        empty_sources = []
        for i, source_list in enumerate(articles_by_source):
            if source_list:
                all_articles.append(source_list.pop(0))
            if not source_list:
                empty_sources.append(i)
        # Remove exhausted sources (in reverse order to preserve indices)
        for i in reversed(empty_sources):
            articles_by_source.pop(i)

    logger.info(f"Total fetched: {len(all_articles)} articles (interleaved)")

    # Process articles with limit
    processed = 0
    max_to_process = config.max_new_articles_per_fetch

    for source_name, article in all_articles:
        # Check if we've hit the limit
        if new_articles >= max_to_process:
            remaining = len(all_articles) - processed
            logger.info(
                f"Hit limit of {max_to_process}. {remaining} articles remaining."
            )
            break

        processed += 1

        try:
            # Check URL deduplication (normalized)
            if article_exists(db_conn, article.url) or url_seen(db_conn, article.url):
                skipped_duplicates += 1
                continue

            # Compute content hash for duplicate detection
            content_hash = compute_content_hash(article.title, article.content)

            # Check content hash for similar content from different sources
            if content_hash_exists(db_conn, content_hash):
                logger.debug(f"Duplicate content hash: {article.title[:50]}")
                mark_url_seen(
                    db_conn,
                    article.url,
                    content_hash,
                    "duplicate",
                    "content hash match",
                )
                skipped_duplicates += 1
                continue

            # Check for similar titles
            similar = find_similar_title(db_conn, article.title)
            if similar:
                logger.debug(
                    f"Similar title found: {article.title[:50]} ~ {similar.original_title[:50]}"
                )
                mark_url_seen(
                    db_conn,
                    article.url,
                    content_hash,
                    "duplicate",
                    f"similar to article {similar.id}",
                )
                skipped_duplicates += 1
                continue

            # Pre-filter: skip posts without media (save API calls)
            if not article.image_url:
                logger.debug(f"Skipped (no media): {article.title[:50]}")
                mark_url_seen(
                    db_conn, article.url, content_hash, "irrelevant", "no media"
                )
                skipped_irrelevant += 1
                continue

            # Pre-filter: skip low-karma Reddit posts
            if article.source_type == "reddit" and article.score < 1000:
                logger.debug(
                    f"Skipped (low karma {article.score}): {article.title[:50]}"
                )
                mark_url_seen(
                    db_conn, article.url, content_hash, "irrelevant", "low karma"
                )
                skipped_irrelevant += 1
                continue

            # Classify content
            classification = await classify_article(
                gemini_model,
                article.title,
                article.content,
                media_url=article.image_url,
                source_type=article.source_type,
            )

            if not classification.is_relevant:
                logger.debug(f"Skipped: {article.title[:50]} - {classification.reason}")
                mark_url_seen(
                    db_conn,
                    article.url,
                    content_hash,
                    "irrelevant",
                    classification.reason,
                )
                skipped_irrelevant += 1
                continue

            # Translate (with source name and media type for attribution)
            translation = await translate_article(
                gemini_model,
                article.title,
                article.content,
                article.url,
                source_name=source_name,
                media_type=article.media_type,
            )

            if not translation.success:
                logger.warning(
                    f"Translation failed for {article.url}: {translation.error}"
                )
                mark_url_seen(
                    db_conn, article.url, content_hash, "failed", translation.error
                )
                failed += 1
                continue

            # Download and cache media
            local_image_path = None
            local_video_path = None

            if article.media_type == "video" and article.image_url:
                # For Reddit videos, use the post URL (yt-dlp handles it better)
                # For other sources, use the direct video URL
                video_url = (
                    article.url
                    if article.source_type == "reddit"
                    else article.image_url
                )
                video_result = await download_video(
                    video_url,
                    data_dir=config.data_dir,
                )
                if video_result.success:
                    local_video_path = video_result.local_path
                    logger.debug(f"Cached video: {video_url} -> {local_video_path}")
                else:
                    logger.warning(f"Video download failed: {video_result.error}")
                    # Fall back to treating as image-less post
            elif article.image_url:
                # Download image
                image_result = await download_image(
                    http_client,
                    article.image_url,
                    data_dir=config.data_dir,
                )
                if image_result.success:
                    local_image_path = image_result.local_path
                    logger.debug(
                        f"Cached image: {article.image_url} -> {local_image_path}"
                    )
                else:
                    logger.warning(f"Image download failed: {image_result.error}")

            # Save to database
            article_id = create_article(
                db_conn,
                source_name=source_name,
                original_url=article.url,
                original_title=article.title,
                original_summary=article.content[:2000],
                content_hash=content_hash,
                image_url=article.image_url,
                local_image_path=local_image_path,
                local_video_path=local_video_path,
                media_type=article.media_type,
                uzbek_content=translation.content,
            )

            # Send for approval
            saved_article = get_article_by_id(db_conn, article_id)
            await send_approval_request(bot, config.telegram_admin_id, saved_article)

            new_articles += 1
            logger.info(f"New article: {article.title[:50]}")

            # Small delay between articles (backoff handles rate limits)
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Error processing article {article.url}: {e}")
            failed += 1

    # Calculate remaining (articles not yet processed)
    remaining = max(0, len(all_articles) - processed)

    # Log token usage
    stats = get_token_stats()
    logger.info(
        f"Fetch job complete. New: {new_articles}, "
        f"Duplicates: {skipped_duplicates}, Irrelevant: {skipped_irrelevant}, "
        f"Failed: {failed}, Remaining: {remaining}"
    )
    logger.info(
        f"Gemini API usage: {stats['classify_calls']} classifications, "
        f"{stats['translate_calls']} translations, "
        f"{stats['input_tokens']} input tokens, {stats['output_tokens']} output tokens"
    )

    # Send summary to admin
    await send_fetch_summary(
        bot,
        config.telegram_admin_id,
        new_articles,
        skipped_duplicates,
        skipped_irrelevant,
        failed,
        remaining,
    )

    if errors:
        await notify_admin_error(bot, config.telegram_admin_id, "\n".join(errors))

    return remaining


async def publish_job(config, db_conn, bot) -> None:
    """Publish approved articles with rate limiting."""
    logger = logging.getLogger(__name__)

    # Check time since last publish
    last_publish = get_last_publish_time(db_conn)
    if last_publish:
        elapsed = datetime.utcnow() - last_publish
        if elapsed < timedelta(minutes=config.publish_gap_minutes):
            return  # Too soon

    # Get next article to publish
    article = get_next_publishable(db_conn)
    if not article:
        return  # Nothing to publish

    # Publish (pass admin_id for failure notifications)
    success = await publish_article(
        bot, config.telegram_channel_id, article, admin_id=config.telegram_admin_id
    )

    if success:
        mark_published(db_conn, article.id)
        logger.info(f"Published: {article.original_title[:50]}")
    else:
        logger.error(f"Failed to publish article {article.id}")


async def scheduler_loop(config, db_conn, http_client, gemini_model, app):
    """Main scheduling loop."""
    logger = logging.getLogger(__name__)
    bot = app.bot

    remaining_interval = 300  # 5 minutes if there are remaining articles
    cleanup_interval = 86400  # 24 hours
    last_remaining_check = 0
    last_heartbeat = 0
    last_cleanup = 0
    has_remaining = False
    was_pending = False  # Track if queue was previously non-empty

    # Run initial fetch only if no pending articles
    pending_count = get_pending_count(db_conn)
    if pending_count > 0:
        logger.info(
            f"Skipping initial fetch: {pending_count} articles pending approval"
        )
        was_pending = True
    else:
        remaining = await fetch_job(config, db_conn, http_client, gemini_model, bot)
        has_remaining = remaining > 0
        last_remaining_check = asyncio.get_event_loop().time()

    while True:
        try:
            current_time = asyncio.get_event_loop().time()

            # Check pending articles
            pending_count = get_pending_count(db_conn)
            queue_empty = pending_count == 0

            # Check for manual fetch trigger
            if app.bot_data.get("fetch_now"):
                app.bot_data["fetch_now"] = False
                if not queue_empty:
                    logger.info(
                        f"Manual fetch skipped: {pending_count} articles pending approval"
                    )
                else:
                    remaining = await fetch_job(
                        config, db_conn, http_client, gemini_model, bot
                    )
                    has_remaining = remaining > 0
                    last_remaining_check = current_time
                    was_pending = False

            # Fetch immediately when queue becomes empty
            if queue_empty and was_pending:
                logger.info("Queue empty, fetching new articles...")
                remaining = await fetch_job(
                    config, db_conn, http_client, gemini_model, bot
                )
                has_remaining = remaining > 0
                last_remaining_check = current_time
                was_pending = False

            # If we have remaining articles from hitting limit, check every 5 min
            elif queue_empty and has_remaining:
                if current_time - last_remaining_check >= remaining_interval:
                    logger.info("Processing remaining articles...")
                    remaining = await fetch_job(
                        config, db_conn, http_client, gemini_model, bot
                    )
                    has_remaining = remaining > 0
                    last_remaining_check = current_time

            # Update pending state for next iteration
            was_pending = not queue_empty

            # Check publishing every minute
            await publish_job(config, db_conn, bot)

            # Heartbeat log every hour
            if current_time - last_heartbeat >= 3600:
                logger.info("💓 Heartbeat: Bot is running")
                last_heartbeat = current_time

            # Cleanup old cached media every 24 hours
            if current_time - last_cleanup >= cleanup_interval:
                images_removed = cleanup_old_images(config.data_dir)
                videos_removed = cleanup_old_videos(config.data_dir)
                if images_removed or videos_removed:
                    logger.info(
                        f"Media cleanup: {images_removed} images, {videos_removed} videos removed"
                    )
                last_cleanup = current_time

            await asyncio.sleep(60)  # Check every minute

        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            await asyncio.sleep(60)


async def main() -> None:
    """Application entry point."""
    # Load config
    config = load_config()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("Starting Olamda nima gap? bot...")

    # Initialize database
    db_conn = init_database(config.database_path)
    logger.info("Database initialized")

    # Initialize HTTP client
    http_client = create_http_client()

    # Initialize Gemini
    gemini_model = init_gemini(config.gemini_api_key, config.gemini_model)
    logger.info(f"Gemini initialized with model: {config.gemini_model}")

    # Create Telegram bot
    app = create_bot(
        config.telegram_bot_token,
        config.telegram_admin_id,
        config.telegram_channel_id,
        db_conn,
    )

    # Start bot and scheduler
    async with app:
        await app.start()
        logger.info("Telegram bot started")

        try:
            # Run scheduler in parallel with bot polling
            scheduler_task = asyncio.create_task(
                scheduler_loop(config, db_conn, http_client, gemini_model, app)
            )

            # Start polling (this blocks)
            await app.updater.start_polling(drop_pending_updates=True)

            # Wait for scheduler (won't reach here normally)
            await scheduler_task

        except asyncio.CancelledError:
            logger.info("Shutting down...")
        finally:
            await app.updater.stop()
            await app.stop()
            await http_client.aclose()
            db_conn.close()
            logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
