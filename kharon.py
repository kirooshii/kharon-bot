import html
import io
import os
import sqlite3
import time
import asyncio
import logging

from faster_whisper import WhisperModel
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
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
# httpx logs every request URL, and Telegram's contain the bot token
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("kharon-bot")

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set. Check your .env file.")

DB_PATH = os.getenv("KHARON_DB_PATH", "kharon.db")
MODEL_NAME = os.getenv("WHISPER_MODEL", "medium")

VERSION = "1.14"
DATE = "October 29, 2026"

CPU_COUNT = os.cpu_count() or 1

log.info("Loading Whisper model %s (downloads on first run)", MODEL_NAME)
# faster-whisper defaults to 4 threads, which on a small VM just fight over the cores
model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8", cpu_threads=CPU_COUNT)
# allowing 2 voice messages at the same time, 1 on a single core:
# two jobs there take as long as running them one by one, and need twice the RAM
transcribe_lock = asyncio.Semaphore(min(2, CPU_COUNT))

ERROR_TEXT = "Sorry, an internal error occurred while processing your audio."

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute(
    "CREATE TABLE IF NOT EXISTS paused_chats (chat_id INTEGER PRIMARY KEY)"
)
# replies that were already retried, one retry per voice message
db.execute(
    "CREATE TABLE IF NOT EXISTS retried_messages ("
    "chat_id INTEGER, message_id INTEGER, PRIMARY KEY (chat_id, message_id))"
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


def mark_retried(chat_id: int, message_id: int) -> bool:
    # False if it was retried before. Check and mark in one step,
    # so two quick taps can't both get through
    cur = db.execute(
        "INSERT OR IGNORE INTO retried_messages (chat_id, message_id) VALUES (?, ?)",
        (chat_id, message_id),
    )
    db.commit()
    return cur.rowcount > 0


# The Retry button keeps no state, the audio is found again through the
# voice message our reply points to
RETRY_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🔁 Retry", callback_data="retry")]]
)


# Telegram's limit, counted on the text after formatting, so the quote tags don't use it up
MAX_MESSAGE = 4096


async def telegram_call(make_call, what: str):
    # a failed edit is what leaves a message stuck on "Working…", so retry network errors.
    # Returns None if it gave up
    for attempt in range(3):
        try:
            return await make_call()
        except BadRequest as e:
            # deleted message, or a retried edit that already went through
            log.warning("Could not %s: %s", what, e)
            return None
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramError:
            if attempt == 2:
                log.exception("Giving up, could not %s", what)
                return None
            await asyncio.sleep(2 ** attempt)


async def edit(msg: Message, text: str, reply_markup=None, parse_mode=None):
    await telegram_call(
        lambda: msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode),
        f"edit message {msg.message_id}",
    )


def quote(text: str) -> str:
    # collapsed to a few lines, expands on tap
    return f"<blockquote expandable>{html.escape(text)}</blockquote>"


def split_text(text: str) -> list[str]:
    # cut at the last sentence end that leaves the part at least half full,
    # else between words, and mid-word only if there's no space at all
    parts = []
    while len(text) > MAX_MESSAGE:
        window = text[:MAX_MESSAGE]
        cut = max(window.rfind(end) for end in (". ", "! ", "? ", "… ")) + 1
        if cut < MAX_MESSAGE // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = MAX_MESSAGE
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    parts.append(text)
    return parts


def run_whisper(data: bytes, careful: bool) -> str:
    segments, _ = model.transcribe(
        io.BytesIO(data),
        # greedy decoding is about twice as fast as a beam of 5 for a small loss,
        # but it always gives the same answer, so Retry uses the slower beam search
        beam_size=5 if careful else 1,
        # skipping silence cuts down on made-up text
        vad_filter=True,
        # keeps one bad guess from derailing the rest of the message
        condition_on_previous_text=False,
    )
    # segments is lazy, the actual work happens while iterating, so do it in this thread
    return " ".join(s.text.strip() for s in segments).strip()


async def process(status_msg: Message, media, context: ContextTypes.DEFAULT_TYPE,
                  careful: bool = False):
    # every path through here must end with status_msg edited
    # a retry is final, so its result gets no button
    keyboard = None if careful else RETRY_KEYBOARD
    try:
        tg_file = await context.bot.get_file(media.file_id, read_timeout=30)
        data = await tg_file.download_as_bytearray(read_timeout=120)

        # Whisper is CPU-heavy, don't block the asyncio event loop
        async with transcribe_lock:
            loop = asyncio.get_running_loop()
            started = time.monotonic()
            text = await loop.run_in_executor(None, run_whisper, bytes(data), careful)
            log.info("Transcribed %s in %.1fs", media.file_id, time.monotonic() - started)
    except Exception:
        log.exception("Failed to process %s", media.file_id)
        await edit(status_msg, ERROR_TEXT, keyboard)
        return

    if not text:
        await edit(status_msg, "(no speech detected)", keyboard)
        return
    parts = split_text(text)
    # only a one-message result gets Retry: rerunning a long one would tie up the CPU
    # for minutes, and its extra parts would need replacing too
    if len(parts) > 1:
        keyboard = None
    await edit(status_msg, quote(parts[0]), keyboard, ParseMode.HTML)

    # the rest go out as a chain of replies, so they stay together in a busy group
    previous = status_msg
    for number, part in enumerate(parts[1:], start=2):
        sent = await telegram_call(
            lambda: previous.reply_text(quote(part), parse_mode=ParseMode.HTML, do_quote=True),
            f"send part {number} of {len(parts)} for {media.file_id}",
        )
        previous = sent or previous


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
    status_msg = query.message
    if not isinstance(status_msg, Message):
        await query.answer("This message is too old to retry.", show_alert=True)
        return

    original = status_msg.reply_to_message
    media = original and (original.voice or original.video_note)
    if not media:
        await query.answer("The original voice message is gone.", show_alert=True)
        return

    if not mark_retried(status_msg.chat_id, status_msg.message_id):
        await query.answer("This message was already retried once.", show_alert=True)
        return

    await query.answer()
    log.info("Retrying %s", media.file_id)
    await edit(status_msg, "Retrying…")
    await process(status_msg, media, context, careful=True)

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
    # lang: and back: come from the language picker of 1.13, they now just retry
    app.add_handler(CallbackQueryHandler(retry, pattern=r"^(retry|lang|back)"))
    app.add_error_handler(on_error)
    app.run_polling()

if __name__ == "__main__":
    main()
