import io
import os
import sqlite3
import asyncio
import logging

from faster_whisper import WhisperModel
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("kharon-bot")

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set. Check your .env file.")

DB_PATH = os.getenv("KHARON_DB_PATH", "kharon.db")
MODEL_NAME = os.getenv("WHISPER_MODEL", "medium")
# languages always offered when retrying, e.g. "ru,en"
RETRY_LANGUAGES = [
    code.strip().lower() for code in os.getenv("RETRY_LANGUAGES", "").split(",") if code.strip()
]

VERSION = "1.13"
DATE = "October 23, 2026"

log.info("Loading Whisper model %s (downloads on first run)", MODEL_NAME)
model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
# allowing 2 voice messages at the same time
transcribe_lock = asyncio.Semaphore(2)

ERROR_TEXT = "Sorry, an internal error occurred while processing your audio."

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute(
    "CREATE TABLE IF NOT EXISTS paused_chats (chat_id INTEGER PRIMARY KEY)"
)
db.commit()


def is_paused(chat_id: int) -> bool:
    row = db.execute(
        "SELECT 1 FROM paused_chats WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    return row is not None


def pause_chat(chat_id: int) -> bool:
    cur = db.execute(
        "INSERT OR IGNORE INTO paused_chats (chat_id) VALUES (?)", (chat_id,)
    )
    db.commit()
    return cur.rowcount > 0


def resume_chat(chat_id: int) -> bool:
    cur = db.execute(
        "DELETE FROM paused_chats WHERE chat_id = ?", (chat_id,)
    )
    db.commit()
    return cur.rowcount > 0


# Callback data is stateless (max 64 bytes), the audio is found again through
# the voice message our reply points to:
#   retry:<langs>         show the language picker (or rerun right away if no langs)
#   lang:<code>:<langs>   rerun forcing <code>
#   back:<langs>          close the picker
# <langs> is a comma-separated list of the most likely languages, e.g. "ru,uk,en".

def retry_keyboard(langs: list[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔁 Retry", callback_data=f"retry:{','.join(langs)}")]]
    )


def language_keyboard(langs: list[str]) -> InlineKeyboardMarkup:
    joined = ",".join(langs)
    row = [
        InlineKeyboardButton(code.upper(), callback_data=f"lang:{code}:{joined}")
        for code in langs
    ]
    row.append(InlineKeyboardButton("✖", callback_data=f"back:{joined}"))
    return InlineKeyboardMarkup([row])


async def edit(msg: Message, text: str | None, reply_markup=None):
    # a failed edit is what leaves a message stuck on "Working…", so retry network errors.
    # text=None only swaps the keyboard
    for attempt in range(3):
        try:
            if text is None:
                await msg.edit_reply_markup(reply_markup=reply_markup)
            else:
                await msg.edit_text(text, reply_markup=reply_markup)
            return
        except BadRequest as e:
            # deleted message, or a retried edit that already went through
            log.warning("Could not edit message %s: %s", msg.message_id, e)
            return
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramError:
            if attempt == 2:
                log.exception("Giving up editing message %s", msg.message_id)
                return
            await asyncio.sleep(2 ** attempt)


def run_whisper(data: bytes, language: str | None):
    segments, info = model.transcribe(
        io.BytesIO(data),
        language=language,
        # skipping silence cuts down on made-up text
        vad_filter=True,
        # keeps one bad guess from derailing the rest of the message
        condition_on_previous_text=False,
    )
    # segments is lazy, the actual work happens while iterating, so do it in this thread
    text = " ".join(s.text.strip() for s in segments).strip()
    # only filled when the language was detected, not forced
    return text, retry_candidates(info.all_language_probs or [])


def retry_candidates(probs: list[tuple[str, float]]) -> list[str]:
    ranked = sorted(probs, key=lambda lp: lp[1], reverse=True)
    if not ranked:
        return []
    # detected language first, then anything plausible, then the configured ones
    langs = [ranked[0][0]] + [lang for lang, p in ranked if p >= 0.05] + RETRY_LANGUAGES
    langs = list(dict.fromkeys(langs))  # dedupe, keep order
    # pad with the next best guesses, even unlikely ones, so there's always a choice
    for lang, _ in ranked:
        if len(langs) >= 3:
            break
        if lang not in langs:
            langs.append(lang)
    return langs[:5]


async def process(status_msg: Message, media, context: ContextTypes.DEFAULT_TYPE,
                  language: str | None = None, langs: list[str] | None = None):
    # every path through here must end with status_msg edited
    try:
        tg_file = await context.bot.get_file(media.file_id, read_timeout=30)
        data = await tg_file.download_as_bytearray(read_timeout=120)

        # Whisper is CPU-heavy, don't block the asyncio event loop
        async with transcribe_lock:
            loop = asyncio.get_running_loop()
            text, detected = await loop.run_in_executor(
                None, run_whisper, bytes(data), language
            )
    except Exception:
        log.exception("Failed to process %s", media.file_id)
        await edit(status_msg, ERROR_TEXT, retry_keyboard(langs or []))
        return

    langs = detected or langs or []
    if len(text) > 4096:
        text = text[:4093] + "..."
    await edit(status_msg, text or "(no speech detected)", retry_keyboard(langs))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I'm Kharon bot. Send me a voice message and I'll transcribe it."
    )

async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    was_paused = is_paused(chat_id)
    pause_chat(chat_id)
    await update.message.reply_text(
        "I'm already paused for this group" if was_paused
        else "Paused. Send /resume to wake me up."
    )

async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    was_paused = is_paused(chat_id)
    resume_chat(chat_id)
    await update.message.reply_text(
        "Resuming..." if was_paused
        else "I'm already on!"
    )

async def transcribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    media = update.message.voice or update.message.video_note
    if not media:
        return

    media_type = "voice message" if update.message.voice else "video note"

    if is_paused(update.effective_chat.id):
        log.info("Skipping %s in paused chat %s", media_type, update.effective_chat.id)
        return

    log.info("Got %s: file_id=%s duration=%s", media_type, media.file_id, media.duration)

    if media.file_size and media.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("This file is larger than 20MB. I cannot download it.")
        return

    # do_quote: private chats don't quote by default, and Retry needs the reply link
    status_msg = await update.message.reply_text(
        f"Working on the {media_type}…", do_quote=True
    )
    await process(status_msg, media, context)

async def retry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action, _, rest = query.data.partition(":")
    status_msg = query.message
    if not isinstance(status_msg, Message):
        await query.answer("This message is too old to retry.", show_alert=True)
        return

    if action == "lang":
        language, _, rest = rest.partition(":")
    else:
        language = None
    langs = [code for code in rest.split(",") if code]

    if action == "retry" and langs:
        await query.answer()
        await edit(status_msg, None, language_keyboard(langs))
        return
    if action == "back":
        await query.answer()
        await edit(status_msg, None, retry_keyboard(langs))
        return

    original = status_msg.reply_to_message
    media = original and (original.voice or original.video_note)
    if not media:
        await query.answer("The original voice message is gone.", show_alert=True)
        return

    await query.answer()
    log.info("Retrying %s with language=%s", media.file_id, language)
    await edit(status_msg, f"Retrying in {language.upper()}…" if language else "Retrying…")
    await process(status_msg, media, context, language, langs)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)

def main():
    log.info("Kharon bot is running, version %s dated %s", VERSION, DATE)
    # concurrent_updates: otherwise one long transcription blocks every other update
    app = Application.builder().token(TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pause", pause))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(MessageHandler(filters.VOICE, transcribe)) # voice messages
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, transcribe)) # video messages
    app.add_handler(CallbackQueryHandler(retry, pattern=r"^(retry|lang|back):"))
    app.add_error_handler(on_error)
    app.run_polling()

if __name__ == "__main__":
    main()
