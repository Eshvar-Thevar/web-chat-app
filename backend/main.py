# backend/main.py

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Dict, List
from pathlib import Path
import os
import uuid

import db

"""
PingPong Web Chat backend.

Exposes:
- HTTP/JSON API for auth, friends, history, and file upload
- WebSocket endpoint for real-time 1-to-1 chat between friends

All persistent data lives in SQLite via db.py.
"""

app = FastAPI(title="Web Chat App (Backend)")

# Allow browser frontend to call the API (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directory for uploaded files
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Serve uploaded files at /files/...
app.mount("/files", StaticFiles(directory=UPLOAD_DIR), name="files")

# Track active WebSocket connections by username
active_connections: Dict[str, WebSocket] = {}


@app.on_event("startup")
def on_startup() -> None:
    # Ensure database and tables exist
    db.init_db()


# ---------- Pydantic models ----------

class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    token: str
    username: str


class MeResponse(BaseModel):
    id: int
    username: str


class FriendRequestBody(BaseModel):
    to_username: str


class FriendRespondBody(BaseModel):
    request_id: int
    accept: bool


class FriendItem(BaseModel):
    id: int
    username: str


class IncomingRequestItem(BaseModel):
    request_id: int
    from_username: str


class OutgoingRequestItem(BaseModel):
    request_id: int
    to_username: str
    status: str


class FriendSummaryResponse(BaseModel):
    friends: List[FriendItem]
    incoming_requests: List[IncomingRequestItem]
    outgoing_requests: List[OutgoingRequestItem]


class MessageItem(BaseModel):
    from_username: str
    to_username: str
    kind: str
    text: str
    url: str | None = None
    created_at: str


# ---------- Auth helpers ----------


def get_current_user(token: str) -> MeResponse:
    """
    Look up the user for a token.
    Used by HTTP routes and WebSocket.
    """
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return MeResponse(id=user["id"], username=user["username"])


# ---------- HTTP endpoints: auth ----------


@app.post("/register", response_model=MeResponse)
def register(data: RegisterRequest):
    if not data.username or not data.password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    try:
        user = db.create_user(data.username.strip(), data.password)
    except ValueError:
        raise HTTPException(status_code=400, detail="Username already taken")

    return MeResponse(id=user["id"], username=user["username"])


@app.post("/login", response_model=AuthResponse)
def login(data: LoginRequest):
    user = db.get_user_by_username(data.username.strip())
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if db.hash_password(data.password) != user["password_hash"]:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = db.create_session(user["id"])
    return AuthResponse(token=token, username=user["username"])


@app.get("/me", response_model=MeResponse)
def me(token: str):
    """
    Simple test endpoint:
    GET /me?token=abc123
    """
    return get_current_user(token)


# ---------- HTTP endpoints: friends ----------

# Friend requests are a tiny "social" layer on top of basic accounts.
# Users can only chat or share files with people that accepted their request.
@app.post("/friends/request")
def send_friend_request(token: str, body: FriendRequestBody):
    me = get_current_user(token)
    try:
        req = db.create_friend_request(me.id, body.to_username.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return req


@app.post("/friends/respond")
def respond_friend_request(token: str, body: FriendRespondBody):
    me = get_current_user(token)
    try:
        result = db.respond_to_friend_request(body.request_id, me.id, body.accept)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.get("/friends", response_model=FriendSummaryResponse)
def list_friends(token: str):
    me = get_current_user(token)
    summary = db.get_friend_summary(me.id)

    return FriendSummaryResponse(
        friends=[FriendItem(**f) for f in summary["friends"]],
        incoming_requests=[IncomingRequestItem(**r) for r in summary["incoming_requests"]],
        outgoing_requests=[OutgoingRequestItem(**r) for r in summary["outgoing_requests"]],
    )


# ---------- HTTP endpoints: message history ----------

# Returns the stored message history between the logged-in user and a friend.
# This is used when a chat is opened so the UI can show messages from SQLite
# instead of starting from a blank screen after every refresh.
@app.get("/history", response_model=List[MessageItem])
def get_history(token: str, friend_username: str, limit: int = 100):
    me = get_current_user(token)
    friend = db.get_user_by_username(friend_username.strip())
    if not friend:
        raise HTTPException(status_code=400, detail="Friend not found")

    if not db.are_friends(me.id, friend["id"]):
        raise HTTPException(status_code=400, detail="You are not friends with this user")

    rows = db.get_conversation(me.id, friend["id"], limit=limit)
    return [
        MessageItem(
            from_username=row["from_username"],
            to_username=row["to_username"],
            kind=row["kind"],
            text=row["text"],
            url=row["url"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


# ---------- HTTP endpoint: file upload ----------


@app.post("/upload")
async def upload_file(token: str, to_username: str, file: UploadFile = File(...)):
    """
    Upload a file to a friend.

    Request:
        POST /upload?token=...&to_username=SomeUser
        body: multipart/form-data with field "file"

    Response JSON:
        {
          "type": "file",
          "from": "alice",
          "filename": "notes.pdf",
          "url": "/files/<random>_notes.pdf"
        }

    Also pushes the same JSON over WebSocket to:
      - the recipient (if online)
      - the sender (if online)
      - and stores it in DB as a 'file' message
    """
    me = get_current_user(token)

    target = db.get_user_by_username(to_username.strip())
    if not target:
        raise HTTPException(status_code=400, detail="Target user does not exist")

    if not db.are_friends(me.id, target["id"]):
        raise HTTPException(status_code=400, detail="You are not friends with this user")

    original_name = os.path.basename(file.filename or "file.bin")
    if len(original_name) > 100:
        original_name = original_name[-100:]

    unique_name = f"{uuid.uuid4().hex}_{original_name}"
    dest_path = UPLOAD_DIR / unique_name

    contents = await file.read()
    try:
        with open(dest_path, "wb") as f:
            f.write(contents)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    url_path = f"/files/{unique_name}"

    # Save in DB
    db.save_message(me.id, target["id"], kind="file", text=original_name, url=url_path)

    ws_payload = {
        "type": "file",
        "from": me.username,
        "filename": original_name,
        "url": url_path,
    }

    # Notify recipient if online
    target_ws = active_connections.get(target["username"])
    if target_ws:
        await target_ws.send_json(ws_payload)

    # Also echo to sender if they have WS open
    sender_ws = active_connections.get(me.username)
    if sender_ws:
        await sender_ws.send_json(ws_payload)

    return ws_payload


# ---------- WebSocket chat: 1-to-1 between friends ----------


async def _send_system(ws: WebSocket, message: str) -> None:
    """
    Small helper to send a system/info message over WebSocket.
    """
    await ws.send_json({"type": "system", "message": message})


@app.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket):
    """
    WebSocket endpoint for real-time chat.

    Client connects as:
        ws://host:8000/ws/chat?token=...token...

    Protocol (JSON) from client:
        { "type": "chat", "to": "bob", "text": "hello" }

    Protocol from server:
        - chat message delivered:
            { "type": "chat", "from": "alice", "text": "hello" }

        - system/info/error:
            { "type": "system", "message": "..." }
    """
    await websocket.accept()

    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)
        return

    try:
        me = get_current_user(token)
    except HTTPException:
        await websocket.close(code=4401)
        return

    username = me.username
    active_connections[username] = websocket
    print(f"[WS] {username} connected")

    await _send_system(websocket, f"Connected as {username}")

    try:
        while True:
            data = await websocket.receive_json()

            msg_type = data.get("type")
            if msg_type != "chat":
                await _send_system(websocket, "Unsupported message type.")
                continue

            to_username = (data.get("to") or "").strip()
            text = (data.get("text") or "").strip()

            if not to_username or not text:
                await _send_system(websocket, "Both 'to' and 'text' fields are required.")
                continue

            target = db.get_user_by_username(to_username)
            if not target:
                await _send_system(websocket, f"User '{to_username}' does not exist.")
                continue

            if not db.are_friends(me.id, target["id"]):
                await _send_system(websocket, f"You are not friends with '{to_username}'.")
                continue

            target_ws = active_connections.get(to_username)
            if not target_ws:
                await _send_system(websocket, f"User '{to_username}' is currently offline.")
                # Still store the message even if offline
                db.save_message(me.id, target["id"], kind="text", text=text, url=None)
                continue

            # Store message in DB
            db.save_message(me.id, target["id"], kind="text", text=text, url=None)

            msg_payload = {
                "type": "chat",
                "from": username,
                "text": text,
            }
            await target_ws.send_json(msg_payload)
            await websocket.send_json(msg_payload)

    except WebSocketDisconnect:
        print(f"[WS] {username} disconnected")
    finally:
        if active_connections.get(username) is websocket:
            active_connections.pop(username, None)
