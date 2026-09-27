# Base44 Telegram Bridge

Standalone Telegram MTProto bridge for the TeleRelay Base44 app.

## Architecture

Base44 TeleRelay -> HTTPS API -> this bridge -> Telegram

This service is intentionally separate from the Base44 app and from the existing bridge.

## Required Railway environment variables

- TELEGRAM_API_ID
- TELEGRAM_API_HASH
- BRIDGE_API_KEY
- SESSION_ENCRYPTION_KEY
- GITHUB_TOKEN
- GITHUB_REPO=vexlypremspo-star/telegram-mtproto-bridge
- GITHUB_SESSION_PATH=base44_telegram_sessions.enc

Generate a Fernet key for SESSION_ENCRYPTION_KEY. Never commit secrets to GitHub.

## Deploy

Deploy the `base44-bridge` directory as its own Railway service. Railway must expose the service over HTTPS.

## Base44 connection

After deployment, configure Base44 secrets:

- TELEGRAM_BRIDGE_URL = Railway public HTTPS URL
- TELEGRAM_BRIDGE_SECRET = same value as BRIDGE_API_KEY

The bridge never needs the user's Telegram password or API credentials in Base44.

## Telegram sessions

Each TeleRelay user receives an independent Telegram authorization session. Starting a bridge login does not log out the user's existing Telegram mobile/desktop sessions.

## Endpoints

- GET /
- POST /telegram/login/start
- POST /telegram/login/verify
- POST /telegram/login/2fa
- GET /telegram/status
- GET /telegram/folders
- GET /telegram/chats
- GET /telegram/chats/{chat_id}/messages
- POST /telegram/forward
- POST /telegram/logout

All endpoints require the bridge API key through the `X-API-Key` header except the root endpoint.
