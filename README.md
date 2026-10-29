# Kharon — Telegram Voice Transcription Bot

A Telegram bot that transcribes voice messages and video notes to text using Whisper (via faster-whisper).

## Features

- Transcribes Telegram voice and video messages in real time
- Uses local Whisper via [faster-whisper](https://github.com/SYSTRAN/faster-whisper): free, runs on CPU, no audio leaves your server
- Transcriptions come in a collapsed quote that expands on tap, so long messages don't flood the chat
- Long transcriptions are split over several messages instead of being cut off
- 🔁 Retry button under every transcription that fits in one message — reruns it once with slower, more careful decoding
- Handles multiple chats efficiently
- Concurrency-limited transcription (max 2 at once, 1 on a single-core machine) prevents CPU overload

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

For reference, the old `openai-whisper` `small` used ~1.4 GB. Up to 2 messages are transcribed at once (1 on a single core), so leave some headroom.
On a small VM (1 CPU, 2 GB RAM) use `small`: `medium` doesn't fit next to the OS and starts swapping, which makes it far slower.

## Getting a Bot Token

1. Open Telegram and chat with [@BotFather](https://t.me/BotFather)
2. Send `/newbot`, follow the prompts
3. Copy the token and put it in your `.env` file

## Usage

Send a voice message or video note to your bot on Telegram — it replies with the transcription.

The transcription is shown as a quote collapsed to a few lines; tap it to read the rest.
If it's longer than one Telegram message (4096 characters, about 4–5 minutes of speech), the rest follows in more messages.
If the result looks wrong, tap **🔁 Retry**: it transcribes the message again more carefully, which takes about twice as long.
Each message can be retried once; the button disappears after that.
Transcriptions that take more than one message have no Retry button, since rerunning them takes too long.

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
