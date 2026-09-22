# Telegram MTProto Bridge

Development bridge for connecting a web application to users' Telegram accounts through Telethon/MTProto.

## Important

This package is a DEVELOPMENT STARTER, not production-ready. Telegram sessions are currently kept in memory. Before serving multiple real users, implement persistent encrypted session storage, proper authentication between the website and bridge, login-state persistence, rate limiting, audit logging, and a background job queue.

Never commit `.env`, API hashes, Telegram session strings, login codes, or passwords to GitHub.

## Requirements

- Python 3.10+
- Telegram `api_id` and `api_hash` from https://my.telegram.org/apps

## Run locally

1. Create a virtual environment:

   `python -m venv .venv`

2. Activate it.

3. Install dependencies:

   `pip install -r requirements.txt`

4. Copy `.env.example` to `.env`.

5. Generate a Fernet encryption key:

   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

6. Put the generated key into `SESSION_ENCRYPTION_KEY`.

7. Fill in `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and a strong `BRIDGE_API_KEY`.

8. Start:

   `uvicorn main:app --host 0.0.0.0 --port 8000`

9. Open:

   `http://localhost:8000`

## API

- `POST /telegram/login/start`
- `POST /telegram/login/verify`
- `POST /telegram/login/2fa`
- `GET /telegram/chats`
- `GET /telegram/chats/{chat_id}/messages`
- `POST /telegram/forward`
- `POST /telegram/logout`

Send the bridge API key in the `X-API-Key` header.

## Security

The 2FA endpoint accepts the password only to complete Telegram's authentication flow. The code does not intentionally persist it. Do not log requests containing credentials.

Before public deployment, add persistent encrypted session storage and make sure the frontend never exposes the bridge API key.
