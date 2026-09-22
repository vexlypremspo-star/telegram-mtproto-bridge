import os
import base64
import json
import urllib.error
import urllib.request
from typing import Dict

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from telethon import TelegramClient, functions, types, utils
from telethon.sessions import StringSession

load_dotenv()

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
BRIDGE_API_KEY = os.environ["BRIDGE_API_KEY"]
cipher = Fernet(os.environ["SESSION_ENCRYPTION_KEY"].encode())

app = FastAPI(title="Telegram MTProto Bridge")

# Free session persistence: store one encrypted session bundle in a private GitHub
# repository instead of using Render's paid Persistent Disk.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "vexlypremspo-star/telegram-mtproto-bridge")
GITHUB_SESSION_PATH = os.environ.get("GITHUB_SESSION_PATH", "telegram_sessions.enc")
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_SESSION_PATH}"

sessions: Dict[str, str] = {}
clients: Dict[str, TelegramClient] = {}
github_file_sha: str | None = None


def _github_request(method: str, url: str, body: bytes | None = None):
    if not GITHUB_TOKEN:
        return None

    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "telegram-mtproto-bridge",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def _load_sessions() -> None:
    global sessions, github_file_sha

    if not GITHUB_TOKEN:
        sessions = {}
        return

    try:
        result = _github_request("GET", GITHUB_API_URL)
        if not result:
            sessions = {}
            github_file_sha = None
            return

        github_file_sha = result.get("sha")
        encoded = result.get("content", "").replace("\n", "")
        if not encoded:
            sessions = {}
            return

        encrypted_bundle = base64.b64decode(encoded).decode("utf-8")
        decrypted_bundle = cipher.decrypt(encrypted_bundle.encode()).decode("utf-8")
        data = json.loads(decrypted_bundle)
        sessions = {
            str(user_id): str(value)
            for user_id, value in data.items()
            if isinstance(value, str) and value
        } if isinstance(data, dict) else {}
    except Exception:
        sessions = {}


def _save_sessions() -> None:
    global github_file_sha

    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not configured")

    bundle = json.dumps(sessions, separators=(",", ":")).encode("utf-8")
    encrypted_bundle = cipher.encrypt(bundle).decode("utf-8")
    encoded = base64.b64encode(encrypted_bundle.encode("utf-8")).decode("ascii")

    payload = {
        "message": "Update encrypted Telegram sessions",
        "content": encoded,
    }
    if github_file_sha:
        payload["sha"] = github_file_sha

    result = _github_request(
        "PUT",
        GITHUB_API_URL,
        json.dumps(payload).encode("utf-8"),
    )
    github_file_sha = result.get("content", {}).get("sha") if result else github_file_sha


_load_sessions()


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

    try:
        session_string = cipher.decrypt(encrypted_session.encode()).decode()
    except Exception:
        raise HTTPException(status_code=500, detail="Stored Telegram session could not be decrypted")
    client = clients.get(user_id)

    if client is None:
        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        await client.connect()
        clients[user_id] = client

    return client


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "Telegram MTProto Bridge",
        "persistent_sessions": True,
    }


@app.post("/telegram/login/start")
async def login_start(request: LoginStart, x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)

    if request.user_id in sessions:
        try:
            client = await get_client(request.user_id)
            if await client.is_user_authorized():
                return {"status": "already_connected"}
        except Exception:
            sessions.pop(request.user_id, None)
            _save_sessions()

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
    _save_sessions()
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
    _save_sessions()
    return {"status": "connected"}


def _peer_id(peer) -> str | None:
    try:
        return str(utils.get_peer_id(peer))
    except Exception:
        return None


def _dialog_matches_folder(dialog, dialog_filter) -> bool:
    peer_id = str(dialog.id)
    include_peers = {
        value for value in (_peer_id(peer) for peer in getattr(dialog_filter, "include_peers", []))
        if value is not None
    }
    exclude_peers = {
        value for value in (_peer_id(peer) for peer in getattr(dialog_filter, "exclude_peers", []))
        if value is not None
    }

    if peer_id in exclude_peers:
        return False

    entity = dialog.entity
    is_group = bool(dialog.is_group)
    is_broadcast = bool(dialog.is_channel and getattr(entity, "broadcast", False))
    is_bot = bool(getattr(entity, "bot", False))
    is_contact = bool(getattr(entity, "contact", False))
    is_non_contact = bool(getattr(entity, "contact", False) is False and dialog.is_user)

    included = peer_id in include_peers

    if getattr(dialog_filter, "contacts", False) and is_contact:
        included = True
    if getattr(dialog_filter, "non_contacts", False) and is_non_contact:
        included = True
    if getattr(dialog_filter, "groups", False) and is_group:
        included = True
    if getattr(dialog_filter, "broadcasts", False) and is_broadcast:
        included = True
    if getattr(dialog_filter, "bots", False) and is_bot:
        included = True

    has_include_rules = bool(
        include_peers
        or getattr(dialog_filter, "contacts", False)
        or getattr(dialog_filter, "non_contacts", False)
        or getattr(dialog_filter, "groups", False)
        or getattr(dialog_filter, "broadcasts", False)
        or getattr(dialog_filter, "bots", False)
    )

    if not has_include_rules:
        included = True

    if not included:
        return False

    if getattr(dialog_filter, "exclude_archived", False) and getattr(dialog, "folder_id", None) == 1:
        return False

    if getattr(dialog_filter, "exclude_read", False) and getattr(dialog, "unread_count", 0) == 0:
        return False

    if getattr(dialog_filter, "exclude_muted", False):
        notify_settings = getattr(dialog.dialog, "notify_settings", None)
        mute_until = getattr(notify_settings, "mute_until", None)
        if mute_until is not None:
            return False

    return True


def _chat_payload(dialog) -> dict:
    entity = dialog.entity
    is_channel = bool(dialog.is_channel)
    is_broadcast = bool(is_channel and getattr(entity, "broadcast", False))

    if is_broadcast:
        chat_type = "channel"
    elif bool(dialog.is_group) or is_channel:
        chat_type = "group"
    else:
        chat_type = "private"

    return {
        "id": dialog.id,
        "name": dialog.name,
        "title": getattr(entity, "title", None) or dialog.name,
        "username": getattr(entity, "username", None),
        "first_name": getattr(entity, "first_name", None),
        "last_name": getattr(entity, "last_name", None),
        "is_group": bool(dialog.is_group),
        "is_channel": is_channel,
        "type": chat_type,
        "memberCount": int(getattr(entity, "participants_count", 0) or 0),
    }


@app.get("/telegram/folders")
async def get_folders(user_id: str, x_api_key: str | None = Header(default=None)):
    check_api_key(x_api_key)
    client = await get_client(user_id)

    result = await client(functions.messages.GetDialogFiltersRequest())
    folders = []

    for dialog_filter in getattr(result, "filters", []):
        if isinstance(dialog_filter, types.DialogFilterDefault):
            continue

        title = getattr(dialog_filter, "title", None)
        if hasattr(title, "text"):
            title = title.text

        folders.append({
            "id": int(dialog_filter.id),
            "title": str(title or f"Folder {dialog_filter.id}"),
        })

    return {"folders": folders}


@app.get("/telegram/chats")
async def get_chats(
    user_id: str,
    folder_id: int | None = None,
    x_api_key: str | None = Header(default=None),
):
    check_api_key(x_api_key)
    client = await get_client(user_id)

    if folder_id is not None:
        if folder_id == 1:
            dialogs = [dialog async for dialog in client.iter_dialogs(folder=1, limit=None)]
        elif folder_id == 0:
            dialogs = [dialog async for dialog in client.iter_dialogs(folder=0, limit=None)]
        else:
            result = await client(functions.messages.GetDialogFiltersRequest())
            dialog_filter = next(
                (
                    item
                    for item in getattr(result, "filters", [])
                    if getattr(item, "id", None) == folder_id
                ),
                None,
            )

            if dialog_filter is None:
                raise HTTPException(status_code=404, detail="Telegram folder not found")

            dialogs = [
                dialog
                async for dialog in client.iter_dialogs()
                if _dialog_matches_folder(dialog, dialog_filter)
            ]
    else:
        dialogs = [dialog async for dialog in client.iter_dialogs(limit=None)]

    return {"chats": [_chat_payload(dialog) for dialog in dialogs]}


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
    _save_sessions()
    return {"status": "logged_out"}
