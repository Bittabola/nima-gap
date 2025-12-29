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


def truncate(text: str, max_length: int) -> str:
    """Truncate text to max length."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


async def send_approval_request(bot: Bot, admin_id: int, article: Article) -> None:
    """Send article to admin for approval with media preview."""
    # Format message
    original_summary = truncate(article.original_summary, 500)
    uzbek_preview = truncate(article.uzbek_content or "", 1500)

    # Add media type indicator
    media_indicator = "🎬" if article.media_type == "video" else "🖼"

    message = f"""🆕 <b>Yangi hikoya topildi!</b> {media_indicator}

📰 <b>Original:</b> {article.original_title}

{original_summary}

━━━━━━━━━━━━━━━

🇺🇿 <b>Telegram uchun:</b>

{uzbek_preview}"""

    # Create approval buttons
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

    try:
        # Try to send with video if available
        if article.media_type == "video" and article.local_video_path:
            try:
                with open(article.local_video_path, "rb") as video_file:
                    await bot.send_video(
                        chat_id=admin_id,
                        video=video_file,
                        caption=truncate(message, MAX_CAPTION_LENGTH),
                        parse_mode="HTML",
                        reply_markup=keyboard,
                        supports_streaming=True,
                    )
                return
            except Exception as e:
                logger.warning(
                    f"Failed to send video preview for article {article.id}: {e}"
                )
                # Fall through to image or text-only

        # Try to send with image if available
        image_path = article.local_image_path or article.image_url
        if image_path:
            try:
                # Use local file if available, otherwise use URL
                if article.local_image_path:
                    with open(article.local_image_path, "rb") as photo_file:
                        await bot.send_photo(
                            chat_id=admin_id,
                            photo=photo_file,
                            caption=truncate(message, MAX_CAPTION_LENGTH),
                            parse_mode="HTML",
                            reply_markup=keyboard,
                        )
                else:
                    await bot.send_photo(
                        chat_id=admin_id,
                        photo=article.image_url,
                        caption=truncate(message, MAX_CAPTION_LENGTH),
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                return
            except Exception as e:
                logger.warning(
                    f"Failed to send image preview for article {article.id}: {e}"
                )
                # Fall through to text-only

        # Text-only fallback (no media or media failed)
        await bot.send_message(
            chat_id=admin_id,
            text=truncate(message, MAX_MESSAGE_LENGTH),
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Failed to send approval request for article {article.id}: {e}")


async def publish_article(
    bot: Bot, channel_id: str, article: Article, admin_id: Optional[int] = None
) -> bool:
    """
    Publish article to channel.
    Tries with cached video/image first, falls back to text-only.
    Notifies admin if media sending fails.
    Returns True on success.
    """
    content = truncate(article.uzbek_content or "", MAX_MESSAGE_LENGTH)
    media_failed = False
    media_error = None

    # Try with video if available
    if article.media_type == "video" and article.local_video_path:
        try:
            with open(article.local_video_path, "rb") as video_file:
                await bot.send_video(
                    chat_id=channel_id,
                    video=video_file,
                    caption=truncate(content, MAX_CAPTION_LENGTH),
                    parse_mode="HTML",
                    supports_streaming=True,
                )
            return True
        except Exception as e:
            media_failed = True
            media_error = str(e)
            logger.warning(f"Video send failed for article {article.id}: {e}")

    # Try with image if available (and not a video post that failed)
    if not media_failed:
        image_path = article.local_image_path or article.image_url
        if image_path:
            try:
                # Prefer local cached image
                if article.local_image_path:
                    with open(article.local_image_path, "rb") as photo_file:
                        await bot.send_photo(
                            chat_id=channel_id,
                            photo=photo_file,
                            caption=truncate(content, MAX_CAPTION_LENGTH),
                            parse_mode="HTML",
                        )
                    return True
                else:
                    # Fall back to URL
                    await bot.send_photo(
                        chat_id=channel_id,
                        photo=article.image_url,
                        caption=truncate(content, MAX_CAPTION_LENGTH),
                        parse_mode="HTML",
                    )
                    return True
            except Exception as e:
                media_failed = True
                media_error = str(e)
                logger.warning(f"Image send failed for article {article.id}: {e}")

    # Text-only (fallback or no media)
    try:
        await bot.send_message(
            chat_id=channel_id,
            text=content,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

        # Notify admin if media failed
        if media_failed and admin_id:
            media_type_label = "Video" if article.media_type == "video" else "Rasm"
            await bot.send_message(
                chat_id=admin_id,
                text=f"⚠️ <b>{media_type_label} yuborilmadi</b>\n\n"
                f"📰 {truncate(article.original_title, 100)}\n"
                f"❌ {truncate(media_error or 'Unknown error', 200)}\n\n"
                f"Hikoya mediasiz nashr qilindi.",
                parse_mode="HTML",
            )

        return True
    except Exception as e:
        logger.error(f"Failed to publish article {article.id}: {e}")
        return False


async def notify_admin_error(bot: Bot, admin_id: int, error: str) -> None:
    """Send error notification to admin."""
    try:
        await bot.send_message(
            chat_id=admin_id,
            text=f"⚠️ <b>Xatolik:</b>\n\n{truncate(error, 1000)}",
            parse_mode="HTML",
        )
    except Exception:
        pass  # Don't fail if notification fails


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
    except Exception:
        pass


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
    """Handle /resend command - resend all pending articles for approval."""
    admin_id = context.bot_data.get("admin_id")
    if update.effective_user.id != admin_id:
        return

    conn = context.bot_data.get("db_conn")
    pending = get_pending_articles(conn)

    if not pending:
        await update.message.reply_text("📭 Kutilayotgan hikoyalar yo'q")
        return

    await update.message.reply_text(
        f"📤 {len(pending)} ta hikoya qayta yuborilmoqda..."
    )

    sent = 0
    for article in pending:
        try:
            await send_approval_request(context.bot, admin_id, article)
            sent += 1
            # Small delay to avoid rate limits
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Failed to resend article {article.id}: {e}")

    await update.message.reply_text(f"✅ {sent}/{len(pending)} ta hikoya yuborildi")


async def approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle approval/rejection button callbacks."""
    query = update.callback_query
    admin_id = context.bot_data.get("admin_id")

    if query.from_user.id != admin_id:
        await query.answer("Ruxsat yo'q")
        return

    await query.answer()

    # Parse callback data
    action, article_id_str = query.data.split(":")
    article_id = int(article_id_str)

    conn = context.bot_data.get("db_conn")
    article = get_article_by_id(conn, article_id)

    if not article:
        await query.answer("❌ Hikoya topilmadi", show_alert=True)
        return

    if action == "approve":
        update_article_status(conn, article_id, "approved")
        response_text = (
            f"✅ <b>Tasdiqlandi</b>\n\n"
            f"📰 {article.original_title}\n\n"
            f"Nashr qilish navbatiga qo'shildi."
        )
    else:  # reject
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
    token: str, admin_id: int, channel_id: str, db_conn: "sqlite3.Connection"
) -> Application:
    """Create and configure Telegram bot application."""
    app = Application.builder().token(token).build()

    # Store shared data
    app.bot_data["admin_id"] = admin_id
    app.bot_data["channel_id"] = channel_id
    app.bot_data["db_conn"] = db_conn
    app.bot_data["fetch_now"] = False

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("fetch", fetch_command))
    app.add_handler(CommandHandler("resend", resend_command))
    app.add_handler(CallbackQueryHandler(approval_callback))

    return app
