"""Telegram bot handlers and publishing."""

import logging
from typing import TYPE_CHECKING

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
    """Send article to admin for approval."""
    # Format message
    original_summary = truncate(article.original_summary, 500)
    uzbek_preview = truncate(article.uzbek_content or "", 1500)

    message = f"""🆕 <b>Yangi hikoya topildi!</b>

📰 <b>Original:</b> {article.original_title}

{original_summary}

━━━━━━━━━━━━━━━

🇺🇿 <b>Telegram uchun:</b>

{uzbek_preview}

🔗 {article.original_url}"""

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
        await bot.send_message(
            chat_id=admin_id,
            text=truncate(message, MAX_MESSAGE_LENGTH),
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Failed to send approval request for article {article.id}: {e}")


async def publish_article(bot: Bot, channel_id: str, article: Article) -> bool:
    """
    Publish article to channel.
    Tries with image first, falls back to text-only.
    Returns True on success.
    """
    content = truncate(article.uzbek_content or "", MAX_MESSAGE_LENGTH)

    # Try with image if available
    if article.image_url:
        try:
            await bot.send_photo(
                chat_id=channel_id,
                photo=article.image_url,
                caption=truncate(content, MAX_CAPTION_LENGTH),
                parse_mode="HTML",
            )
            return True
        except Exception as e:
            logger.warning(f"Image send failed, falling back to text: {e}")

    # Text-only (fallback or no image)
    try:
        await bot.send_message(
            chat_id=channel_id,
            text=content,
            parse_mode="HTML",
            disable_web_page_preview=True,
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
        "/fetch - Yangi hikoyalarni qidirish"
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
        f"📊 <b>Status</b>\n\n"
        f"⏳ Kutilmoqda: {pending}\n"
        f"✅ Tasdiqlangan: {approved}",
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


async def approval_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        await query.edit_message_text("❌ Hikoya topilmadi")
        return

    if action == "approve":
        update_article_status(conn, article_id, "approved")
        await query.edit_message_text(
            f"✅ <b>Tasdiqlandi</b>\n\n"
            f"📰 {article.original_title}\n"
            f"🔗 {article.original_url}\n\n"
            f"Nashr qilish navbatiga qo'shildi.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:  # reject
        update_article_status(conn, article_id, "rejected")
        await query.edit_message_text(
            f"❌ <b>Rad etildi</b>\n\n"
            f"📰 {article.original_title}\n"
            f"🔗 {article.original_url}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def create_bot(token: str, admin_id: int, db_conn: "sqlite3.Connection") -> Application:
    """Create and configure Telegram bot application."""
    app = Application.builder().token(token).build()

    # Store shared data
    app.bot_data["admin_id"] = admin_id
    app.bot_data["db_conn"] = db_conn
    app.bot_data["fetch_now"] = False

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("fetch", fetch_command))
    app.add_handler(CallbackQueryHandler(approval_callback))

    return app
