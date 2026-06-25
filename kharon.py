import os
import tempfile
import asyncio
import logging

import whisper
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters, CommandHandler

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("kharon-bot")

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set. Check your .env file.")

VERSION = "1.11"
DATE = "June 21, 2026"

model = whisper.load_model("small")
# allowing 2 voice messages at the same time
transcribe_lock = asyncio.Semaphore(2)
# chat IDs where transcription is paused via /pause
paused_chats: set[int] = set()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I'm Kharon bot. Send me a voice message and I'll transcribe it."
    )

async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    was_paused = chat_id in paused_chats
    paused_chats.add(chat_id)
    await update.message.reply_text(
        "I'm already paused for this group" if was_paused
        else "Paused. Send /resume to wake me up."
    )

async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    was_paused = chat_id in paused_chats
    paused_chats.discard(chat_id)
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

    is_voice = update.message.voice is not None
    suffix = ".ogg" if is_voice else ".mp4"
    media_type = "voice" if is_voice else "video note"

    if update.effective_chat.id in paused_chats:
        log.info("Skipping %s in paused chat %s", media_type, update.effective_chat.id)
        return

    log.info("Got %s: file_id=%s duration=%s", media_type, media.file_id, media.duration)

    if media.file_size and media.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("This file is larger than 20MB. I cannot download it.")
        return

    status_msg = await update.message.reply_text("Working on the voice message…")

    tg_file = await context.bot.get_file(media.file_id)
    data = await tg_file.download_as_bytearray()

    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(data)
            path = f.name

        # Whisper is CPU-heavy, don't block the asyncio event loop
        async with transcribe_lock:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, model.transcribe, path)

        text = (result.get("text") or "").strip()
        if len(text) > 4096:
            text = text[:4093] + "..."
        await status_msg.edit_text(text if text else "(no speech detected)")

    except Exception as e:
        log.exception("Failed to process voice")
        await status_msg.edit_text("Sorry, an internal error occurred while processing your audio.")

    finally:
        if path and os.path.exists(path):
            os.remove(path)

def main():
    log.info("Kharon bot is running, version %s dated %s", VERSION, DATE)
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pause", pause))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(MessageHandler(filters.VOICE, transcribe)) # voice messages
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, transcribe)) # video messages
    app.run_polling()

if __name__ == "__main__":
    main()
