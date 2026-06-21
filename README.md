# Kharon — Telegram Voice Transcription Bot

A Telegram bot that transcribes voice messages and video notes to text using OpenAI Whisper.

## Features

- Transcribes Telegram voice and video messages in real time
- Uses local OpenAI Whisper for quality transcription
- Handles multiple chats efficiently
- Single-transcription lock prevents CPU overload

## Installation

### Docker (recommended)

```bash
git clone https://github.com/kirooshii/kharon-bot.git
cd kharon-bot

# Create .env with your token
echo 'TELEGRAM_TOKEN=your_bot_token_here' > .env

docker compose up --build
```

### Without Docker

```bash
git clone https://github.com/kirooshii/kharon-bot.git
cd kharon-bot

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

echo 'TELEGRAM_TOKEN=your_bot_token_here' > .env
.venv/bin/python kharon.py
```
## Getting a Bot Token

1. Open Telegram and chat with [@BotFather](https://t.me/BotFather)
2. Send `/newbot`, follow the prompts
3. Copy the token and put it in your `.env` file

## Usage

Send a voice message or video note to your bot on Telegram — it replies with the transcription.

## Commands

- `/start` — Welcome message

## Tech Stack

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) — Telegram Bot API
- [OpenAI Whisper](https://github.com/openai/whisper) — Speech-to-text
- [python-dotenv](https://github.com/theskumar/python-dotenv) — Environment management

## License

MIT
