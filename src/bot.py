"""Telegram bot handlers and publishing."""

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from .database import (
    Article,
    get_approved_count,
    get_article_by_id,
    get_pending_articles,
    get_pending_count,
    update_article_status,
)

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000
MAX_CAPTION_LENGTH = 1024
MAX_RESEND = 10


def truncate(text: str, max_length: int) -> str:
    """
    Truncate text to max length, preserving HTML tag integrity.
    Closes any unclosed tags after truncation.
    Guarantees result does not exceed max_length.
    """
    if len(text) <= max_length:
        return text

    import re  # noqa: PLC0415 - local import for rarely-used function

    # Reserve space for closing tags and ellipsis
    # Worst case: 3-4 nested tags like </code></pre></b></a> = ~30 chars + "..." = ~33
    reserve = 40
    truncated = text[: max_length - reserve]

    # Find all opening tags (including self-closing detection)
    open_tags = []
    for match in re.finditer(r"<([a-zA-Z]+)[^>]*>", truncated):
        tag = match.group(1).lower()
        # Skip self-closing or void elements
        if tag not in ("br", "hr", "img", "input", "meta", "link"):
            open_tags.append(tag)

    # Remove tags that were closed
    for match in re.finditer(r"</([a-zA-Z]+)>", truncated):
        tag = match.group(1).lower()
        if tag in open_tags:
            open_tags.remove(tag)

    # Check if we cut in the middle of a tag (unclosed <)
    last_open = truncated.rfind("<")
    last_close = truncated.rfind(">")
    if last_open > last_close:
        # We're inside a tag, remove the partial tag
        truncated = truncated[:last_open]

    # Build suffix with closing tags
    suffix = "..."
    for tag in reversed(open_tags):
        suffix += f"</{tag}>"

    # Final safety check - trim more if still too long
    while len(truncated) + len(suffix) > max_length and len(truncated) > 0:
        truncated = truncated[:-1]

    return truncated + suffix


async def _send_with_media(
    bot: Bot,
    chat_id,
    article: Article,
    content: str,
    parse_mode: str = "HTML",
    reply_markup=None,
) -> tuple[bool, bool, Optional[str]]:
    """Try sending with video -> image -> text.

    Returns (success, media_failed, error_message).
    """
    media_failed = False
    media_error = None

    # Try video if available
    if article.media_type == "video" and article.local_video_path:
        try:
            with open(article.local_video_path, "rb") as video_file:
                sent_message = await bot.send_video(
                    chat_id=chat_id,
                    video=video_file,
                    caption=truncate(content, MAX_CAPTION_LENGTH),
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    supports_streaming=True,
                    width=article.video_width,
                    height=article.video_height,
                )
            # Verify Telegram actually processed the video
            if sent_message and sent_message.video:
                return True, False, None
            media_failed = True
            media_error = "Telegram accepted the request but no video object in response"
            logger.warning(
                f"Video upload not confirmed for article {article.id}: response has no video"
            )
        except Exception as e:
            media_failed = True
            media_error = str(e)
            logger.warning(f"Video send failed for article {article.id}: {e}")

    # Try image if available
    image_source = article.local_image_path or article.image_url
    if image_source and (not media_failed or article.media_type == "video"):
        try:
            if article.local_image_path:
                with open(article.local_image_path, "rb") as photo_file:
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_file,
                        caption=truncate(content, MAX_CAPTION_LENGTH),
                        parse_mode=parse_mode,
                        reply_markup=reply_markup,
                    )
            else:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=article.image_url,
                    caption=truncate(content, MAX_CAPTION_LENGTH),
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            return True, False, None
        except Exception as e:
            media_failed = True
            media_error = str(e)
            logger.warning(f"Image send failed for article {article.id}: {e}")

    # Text-only fallback
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=truncate(content, MAX_MESSAGE_LENGTH),
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        return True, media_failed, media_error
    except Exception as e:
        logger.error(f"Text send failed for article {article.id}: {e}")
        return False, media_failed, media_error


async def send_approval_request(bot: Bot, admin_id: int, article: Article) -> None:
    """Send article to admin for approval with media preview."""
    uzbek_preview = truncate(article.uzbek_content or "", 1500)
    media_indicator = "🎬" if article.media_type == "video" else "🖼"

    summary_text = ""
    if (
        article.original_summary
        and article.original_summary.strip() != article.original_title.strip()
    ):
        if not article.original_title.strip().startswith(
            article.original_summary.strip()[:50]
        ):
            summary_text = f"\n\n{truncate(article.original_summary, 500)}"

    message = f"""🆕 <b>Yangi hikoya topildi!</b> {media_indicator}

📰 <b>Original:</b> {article.original_title}{summary_text}

━━━━━━━━━━━━━━━

🇺🇿 <b>Telegram uchun:</b>

{uzbek_preview}"""

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Tasdiqlash", callback_data=f"approve:{article.id}"
                ),
                InlineKeyboardButton(
                    "❌ Rad etish", callback_data=f"reject:{article.id}"
                ),
            ]
        ]
    )

    success, _, _ = await _send_with_media(
        bot, admin_id, article, message, reply_markup=keyboard
    )
    if not success:
        logger.error(f"Failed to send approval request for article {article.id}")


async def publish_article(
    bot: Bot, channel_id: str, article: Article, admin_id: Optional[int] = None
) -> bool:
    """
    Publish article to channel.
    Tries with cached video/image first, falls back to text-only.
    Notifies admin if media sending fails.
    Returns True on success.
    """
    content = article.uzbek_content or ""

    success, media_failed, media_error = await _send_with_media(
        bot, channel_id, article, content
    )

    if success and media_failed and admin_id:
        media_type_label = "Video" if article.media_type == "video" else "Rasm"
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=f"⚠️ <b>{media_type_label} yuborilmadi</b>\n\n"
                f"📰 {truncate(article.original_title, 100)}\n"
                f"❌ {truncate(media_error or 'Unknown error', 200)}\n\n"
                f"Hikoya mediasiz nashr qilindi.",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Failed to notify admin about media failure: {e}")

    return success


async def notify_admin_error(bot: Bot, admin_id: int, error: str) -> None:
    """Send error notification to admin."""
    try:
        await bot.send_message(
            chat_id=admin_id,
            text=f"⚠️ <b>Xatolik:</b>\n\n{truncate(error, 1000)}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Failed to send admin error notification: {e}")


async def send_fetch_summary(
    bot: Bot,
    admin_id: int,
    new_articles: int,
    skipped_duplicates: int,
    skipped_irrelevant: int,
    failed: int,
    remaining: int,
) -> None:
    """Send a summary of the fetch job to admin."""
    if new_articles == 0 and failed == 0:
        return  # Nothing interesting to report

    parts = []
    parts.append("📊 <b>Qidiruv yakunlandi</b>\n")

    if new_articles > 0:
        parts.append(f"✅ Yangi hikoyalar: {new_articles}")
    if skipped_duplicates > 0:
        parts.append(f"🔄 Takroriy: {skipped_duplicates}")
    if skipped_irrelevant > 0:
        parts.append(f"⏭ O'tkazib yuborildi: {skipped_irrelevant}")
    if failed > 0:
        parts.append(f"❌ Xatolik: {failed}")
    if remaining > 0:
        parts.append(f"⏳ Keyingi safar: {remaining}")

    try:
        await bot.send_message(
            chat_id=admin_id,
            text="\n".join(parts),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Failed to send fetch summary: {e}")


# Command handlers


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    admin_id = context.bot_data.get("admin_id")
    if update.effective_user.id != admin_id:
        return

    await update.message.reply_text(
        "👋 Salom! Men Olamda nima gap? botiman.\n\n"
        "Buyruqlar:\n"
        "/status - Statistika\n"
        "/fetch - Yangi hikoyalarni qidirish\n"
        "/resend - Kutilayotgan hikoyalarni qayta yuborish"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    admin_id = context.bot_data.get("admin_id")
    if update.effective_user.id != admin_id:
        return

    conn = context.bot_data.get("db_conn")
    pending = get_pending_count(conn)
    approved = get_approved_count(conn)

    await update.message.reply_text(
        f"📊 <b>Status</b>\n\n⏳ Kutilmoqda: {pending}\n✅ Tasdiqlangan: {approved}",
        parse_mode="HTML",
    )


async def fetch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /fetch command - trigger manual fetch."""
    admin_id = context.bot_data.get("admin_id")
    if update.effective_user.id != admin_id:
        return

    await update.message.reply_text("🔄 Yangi hikoyalar qidirilmoqda...")

    # Set flag for fetch job to run immediately
    context.bot_data["fetch_now"] = True


async def resend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /resend command - resend pending articles for approval (max 10 at a time)."""
    admin_id = context.bot_data.get("admin_id")
    if update.effective_user.id != admin_id:
        return

    conn = context.bot_data.get("db_conn")
    pending = get_pending_articles(conn)

    if not pending:
        await update.message.reply_text("📭 Kutilayotgan hikoyalar yo'q")
        return

    to_send = pending[:MAX_RESEND]
    remaining = len(pending) - len(to_send)

    await update.message.reply_text(
        f"📤 {len(to_send)} ta hikoya yuborilmoqda..."
        + (f" ({remaining} ta keyinroq)" if remaining > 0 else "")
    )

    sent = 0
    for article in to_send:
        try:
            await send_approval_request(context.bot, admin_id, article)
            sent += 1
            # Longer delay to avoid flooding
            await asyncio.sleep(1.0)
        except Exception as e:
            logger.error(f"Failed to resend article {article.id}: {e}")

    await update.message.reply_text(f"✅ {sent}/{len(to_send)} ta hikoya yuborildi")


async def approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle approval/rejection button callbacks."""
    query = update.callback_query
    admin_id = context.bot_data.get("admin_id")

    if query.from_user.id != admin_id:
        await query.answer("Ruxsat yo'q")
        return

    await query.answer()

    # Parse callback data with validation
    try:
        action, article_id_str = query.data.split(":", 1)
        article_id = int(article_id_str)
    except (ValueError, AttributeError) as e:
        logger.warning(f"Invalid callback data '{query.data}': {e}")
        await query.answer("❌ Noto'g'ri ma'lumot", show_alert=True)
        return

    conn = context.bot_data.get("db_conn")
    db_lock = context.bot_data.get("db_lock")
    article = get_article_by_id(conn, article_id)

    if not article:
        await query.answer("❌ Hikoya topilmadi", show_alert=True)
        return

    if action == "approve":
        if db_lock:
            async with db_lock:
                update_article_status(conn, article_id, "approved")
        else:
            update_article_status(conn, article_id, "approved")
        response_text = (
            f"✅ <b>Tasdiqlandi</b>\n\n"
            f"📰 {article.original_title}\n\n"
            f"Nashr qilish navbatiga qo'shildi."
        )
    else:  # reject
        if db_lock:
            async with db_lock:
                update_article_status(conn, article_id, "rejected")
        else:
            update_article_status(conn, article_id, "rejected")
        response_text = f"❌ <b>Rad etildi</b>\n\n📰 {article.original_title}"

    # Use appropriate edit method based on message type
    # Media messages (photo/video) have captions, text messages have text
    if query.message.photo or query.message.video:
        await query.edit_message_caption(
            caption=response_text,
            parse_mode="HTML",
            reply_markup=None,
        )
    else:
        await query.edit_message_text(
            text=response_text,
            parse_mode="HTML",
            reply_markup=None,
        )


def create_bot(
    token: str,
    admin_id: int,
    channel_id: str,
    db_conn: "sqlite3.Connection",
    db_lock: "asyncio.Lock | None" = None,
) -> Application:
    """Create and configure Telegram bot application."""
    app = Application.builder().token(token).build()

    # Store shared data
    app.bot_data["admin_id"] = admin_id
    app.bot_data["channel_id"] = channel_id
    app.bot_data["db_conn"] = db_conn
    app.bot_data["db_lock"] = db_lock
    app.bot_data["fetch_now"] = False

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("fetch", fetch_command))
    app.add_handler(CommandHandler("resend", resend_command))
    app.add_handler(CallbackQueryHandler(approval_callback))

    return app
