# Kharon — Telegram Voice Transcription Bot

A Telegram bot that transcribes voice messages and video notes to text using Whisper (via faster-whisper).

## Features

- Transcribes Telegram voice and video messages in real time
- Uses local Whisper via [faster-whisper](https://github.com/SYSTRAN/faster-whisper): free, runs on CPU, no audio leaves your server
- 🔁 Retry button under every transcription — pick the right language if Whisper guessed wrong
- Handles multiple chats efficiently
- Concurrency-limited transcription (max 2 at once) prevents CPU overload

## Installation

### Docker (recommended)

```bash
git clone https://github.com/kirooshii/kharon-bot.git
cd kharon-bot

# Create .env with your token
echo 'TELEGRAM_TOKEN=your_bot_token_here' > .env

docker compose up --build
```

The `/pause` state and downloaded models are kept in Docker volumes (`data` and `models`), so they survive rebuilds. `docker compose down -v` deletes them.

### Without Docker

```bash
git clone https://github.com/kirooshii/kharon-bot.git
cd kharon-bot

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

echo 'TELEGRAM_TOKEN=your_bot_token_here' > .env
.venv/bin/python kharon.py
```
## Choosing a Model

Set `WHISPER_MODEL` in `.env` (default `medium`). The model downloads on first start.
Measured peak RAM / time per short voice message on CPU (int8):

| Model | Peak RAM | Speed | Notes |
|---|---|---|---|
| `small` | ~0.8 GB | ~3s | Fastest, makes the most mistakes |
| `medium` | ~1.2 GB | ~8s | Default, good balance |
| `large-v3-turbo` | ~1.6 GB | ~11s | Close to `large-v3` quality |
| `large-v3` | ~1.8 GB | ~20s | Best quality, slowest |

For reference, the old `openai-whisper` `small` used ~1.4 GB. Up to 2 messages are transcribed at once, so leave some headroom.

## Getting a Bot Token

1. Open Telegram and chat with [@BotFather](https://t.me/BotFather)
2. Send `/newbot`, follow the prompts
3. Copy the token and put it in your `.env` file

## Usage

Send a voice message or video note to your bot on Telegram — it replies with the transcription.

If the result is wrong (e.g. in the wrong language), tap **🔁 Retry** and pick the language the message is actually in.
The picker shows the languages Whisper found likely; set `RETRY_LANGUAGES=ru,en` in `.env` to always include the languages your chats use.

## Commands

- `/start` — Welcome message
- `/pause` — Stop transcribing voice/video messages in this chat
- `/resume` — Resume transcription in this chat

## Tech Stack

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) — Telegram Bot API
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — Speech-to-text (Whisper on CTranslate2)
- [python-dotenv](https://github.com/theskumar/python-dotenv) — Environment management

## License

MIT
