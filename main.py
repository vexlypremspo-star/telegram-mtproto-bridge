import os
from typing import Dict

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv()

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
BRIDGE_API_KEY = os.environ["BRIDGE_API_KEY"]
cipher = Fernet(os.environ["SESSION_ENCRYPTION_KEY"].encode())

app = FastAPI(title="Telegram MTProto Bridge")

# DEVELOPMENT ONLY: sessions are held in memory.
# Before production/multiple users, replace this with persistent encrypted storage.
sessions: Dict[str, str] = {}
clients: Dict[str, TelegramClient] = {}


class LoginStart(BaseModel):
    user_id: str
    phone: str


class LoginVerify(BaseModel):
    user_id: str
    code: str


class TwoFactorVerify(BaseModel):
    user_id: str
    password: str


class ForwardRequest(BaseModel):
    user_id: str
    source_chat_id: int
    message_id: int
    destination_chat_ids: list[int]


def check_api_key(api_key: str | None):
    if api_key != BRIDGE_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def get_client(user_id: str):
    encrypted_session = sessions.get(user_id)
    if not encrypted_session:
        raise HTTPException(status_code=404, detail="Telegram account is not connected")

    session_string = cipher.decrypt(encrypted_session.encode()).decode()
    client = clients.get(user_id)

    if client is None:
        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        await client.connect()
        clients[user_id] = client

    return client


@app.get("/")
async def root():
    return {"status": "online", "service": "Telegram MTProto Bridge"}


@app.post("/telegram/login/start")
async def login_start(request: LoginStart, x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    sent = await client.send_code_request(request.phone)
    clients[request.user_id] = client

    return {"status": "code_sent", "phone_code_hash": sent.phone_code_hash}


@app.post("/telegram/login/verify")
async def login_verify(request: LoginVerify, x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)

    client = clients.get(request.user_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Login session not found")

    try:
        await client.sign_in(code=request.code)
    except Exception as error:
        if error.__class__.__name__ == "SessionPasswordNeededError":
            return {"status": "2fa_required"}
        raise HTTPException(status_code=400, detail=error.__class__.__name__)

    session_string = client.session.save()
    sessions[request.user_id] = cipher.encrypt(session_string.encode()).decode()
    return {"status": "connected"}


@app.post("/telegram/login/2fa")
async def login_2fa(request: TwoFactorVerify, x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)

    client = clients.get(request.user_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Login session not found")

    await client.sign_in(password=request.password)

    session_string = client.session.save()
    sessions[request.user_id] = cipher.encrypt(session_string.encode()).decode()
    return {"status": "connected"}


@app.get("/telegram/chats")
async def get_chats(user_id: str, x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)
    client = await get_client(user_id)

    dialogs = []
    async for dialog in client.iter_dialogs():
        dialogs.append({
            "id": dialog.id,
            "name": dialog.name,
            "is_group": bool(dialog.is_group),
            "is_channel": bool(dialog.is_channel),
        })

    return {"chats": dialogs}


@app.get("/telegram/chats/{chat_id}/messages")
async def get_messages(
    chat_id: int,
    user_id: str,
    limit: int = 20,
    x_api_key: str | None = Header(default=None),
):
    check_api_key(x_api_key)
    client = await get_client(user_id)

    messages = []
    async for message in client.iter_messages(chat_id, limit=min(limit, 100)):
        messages.append({
            "id": message.id,
            "text": message.text or "",
            "date": message.date.isoformat() if message.date else None,
            "has_media": message.media is not None,
        })

    return {"messages": messages}


@app.post("/telegram/forward")
async def forward_message(
    request: ForwardRequest,
    x_api_key: str | None = Header(default=None),
):
    check_api_key(x_api_key)
    client = await get_client(request.user_id)

    results = []
    for destination in request.destination_chat_ids:
        try:
            await client.forward_messages(
                destination,
                request.message_id,
                from_peer=request.source_chat_id,
            )
            results.append({
                "destination_chat_id": destination,
                "status": "success",
            })
        except Exception as error:
            results.append({
                "destination_chat_id": destination,
                "status": "failed",
                "error": error.__class__.__name__,
            })

    return {"results": results}


@app.post("/telegram/logout")
async def logout(user_id: str, x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)

    client = clients.pop(user_id, None)
    if client:
        await client.log_out()

    sessions.pop(user_id, None)
    return {"status": "logged_out"}
