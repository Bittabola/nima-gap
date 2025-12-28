"""Main entry point and scheduling loop."""

import asyncio
import logging
from datetime import datetime, timedelta

from .ai import classify_article, init_gemini, translate_article
from .bot import (
    create_bot,
    notify_admin_error,
    publish_article,
    send_approval_request,
)
from .config import load_config
from .database import (
    article_exists,
    create_article,
    get_article_by_id,
    get_last_publish_time,
    get_next_publishable,
    init_database,
    mark_published,
)
from .fetcher import create_http_client, create_reddit_client, fetch_source


async def fetch_job(
    config,
    db_conn,
    http_client,
    reddit,
    gemini_model,
    bot,
) -> None:
    """Fetch and process articles from all sources."""
    logger = logging.getLogger(__name__)
    logger.info("Starting fetch job...")

    errors = []
    new_articles = 0

    for source in config.sources:
        source_name = source.get("name", "Unknown")

        try:
            # Fetch articles
            articles = await fetch_source(source, http_client, reddit)
            logger.info(f"Fetched {len(articles)} from {source_name}")

            for article in articles:
                # Skip if already seen
                if article_exists(db_conn, article.url):
                    continue

                # Classify
                classification = await classify_article(
                    gemini_model, article.title, article.content
                )

                if not classification.is_relevant:
                    logger.debug(
                        f"Skipped: {article.title[:50]} - {classification.reason}"
                    )
                    continue

                # Translate
                translation = await translate_article(
                    gemini_model, article.title, article.content, article.url
                )

                if not translation.success:
                    logger.warning(
                        f"Translation failed for {article.url}: {translation.error}"
                    )
                    continue

                # Save to database
                article_id = create_article(
                    db_conn,
                    source_name=source_name,
                    original_url=article.url,
                    original_title=article.title,
                    original_summary=article.content[:2000],
                    image_url=article.image_url,
                    uzbek_content=translation.content,
                )

                # Send for approval
                saved_article = get_article_by_id(db_conn, article_id)
                await send_approval_request(bot, config.telegram_admin_id, saved_article)

                new_articles += 1
                logger.info(f"New article: {article.title[:50]}")

                # Rate limit Gemini calls
                await asyncio.sleep(1)

        except Exception as e:
            error_msg = f"{source_name}: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

    logger.info(f"Fetch job complete. New articles: {new_articles}")

    if errors:
        await notify_admin_error(bot, config.telegram_admin_id, "\n".join(errors))


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

    # Publish
    success = await publish_article(bot, config.telegram_channel_id, article)

    if success:
        mark_published(db_conn, article.id)
        logger.info(f"Published: {article.original_title[:50]}")
    else:
        logger.error(f"Failed to publish article {article.id}")


async def scheduler_loop(config, db_conn, http_client, reddit, gemini_model, app):
    """Main scheduling loop."""
    logger = logging.getLogger(__name__)
    bot = app.bot

    fetch_interval = config.fetch_interval_hours * 3600  # Convert to seconds
    last_fetch = 0
    last_heartbeat = 0

    # Run initial fetch
    await fetch_job(config, db_conn, http_client, reddit, gemini_model, bot)
    last_fetch = asyncio.get_event_loop().time()

    while True:
        try:
            current_time = asyncio.get_event_loop().time()

            # Check for manual fetch trigger
            if app.bot_data.get("fetch_now"):
                app.bot_data["fetch_now"] = False
                await fetch_job(config, db_conn, http_client, reddit, gemini_model, bot)
                last_fetch = current_time

            # Scheduled fetch
            elif current_time - last_fetch >= fetch_interval:
                await fetch_job(config, db_conn, http_client, reddit, gemini_model, bot)
                last_fetch = current_time

            # Check publishing every minute
            await publish_job(config, db_conn, bot)

            # Heartbeat log every hour
            if current_time - last_heartbeat >= 3600:
                logger.info("💓 Heartbeat: Bot is running")
                last_heartbeat = current_time

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

    # Initialize Reddit client
    reddit = await create_reddit_client(
        config.reddit_client_id,
        config.reddit_client_secret,
    )
    logger.info("Reddit client initialized")

    # Initialize Gemini
    gemini_model = init_gemini(config.gemini_api_key)
    logger.info("Gemini initialized")

    # Create Telegram bot
    app = create_bot(config.telegram_bot_token, config.telegram_admin_id, db_conn)

    # Start bot and scheduler
    async with app:
        await app.start()
        logger.info("Telegram bot started")

        try:
            # Run scheduler in parallel with bot polling
            scheduler_task = asyncio.create_task(
                scheduler_loop(config, db_conn, http_client, reddit, gemini_model, app)
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
            await reddit.close()
            db_conn.close()
            logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
