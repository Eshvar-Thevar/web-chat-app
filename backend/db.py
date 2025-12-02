# backend/db.py

import sqlite3
import hashlib
import os
from typing import Optional, Dict, Any, List

"""
SQLite helper functions for PingPong Web Chat.

This module owns the schema for:
- users, sessions       (identity + login)
- friend_requests       (who is allowed to talk to whom)
- messages              (chat history, including text and file links)

The rest of the code only calls these functions instead of writing raw SQL.
"""

DB_PATH = "chat.db"


def get_connection() -> sqlite3.Connection:
    """
    Open a new SQLite connection.
    Using a new connection per request keeps things simple and thread-safe
    for a small project.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """
    Create tables if they do not exist yet.
    """
    conn = get_connection()
    cur = conn.cursor()

    # Basic users table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        );
        """
    )

    # Sessions map random token -> user
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )

    # Friend requests / friendships
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS friend_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER NOT NULL,
            to_user_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected')),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            responded_at TEXT,
            UNIQUE (from_user_id, to_user_id),
            FOREIGN KEY (from_user_id) REFERENCES users(id),
            FOREIGN KEY (to_user_id) REFERENCES users(id)
        );
        """
    )

    # Messages between users
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER NOT NULL,
            to_user_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('text', 'file')),
            text TEXT NOT NULL,
            url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (from_user_id) REFERENCES users(id),
            FOREIGN KEY (to_user_id) REFERENCES users(id)
        );
        """
    )

    conn.commit()
    conn.close()


# ---------- Password helpers ----------


def hash_password(password: str) -> str:
    """
    Very simple password hashing using SHA-256.
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


# ---------- Users ----------


def create_user(username: str, password: str) -> Dict[str, Any]:
    """
    Create a new user with the given username and password.

    Raises ValueError if the username already exists.
    """
    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, hash_password(password)),
        )
        conn.commit()
        user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError("Username already taken")

    cur.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row)


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# ---------- Sessions ----------


def create_session(user_id: int) -> str:
    """
    Create a random token for this user and store it in the sessions table.
    Returns the token string.
    """
    token = os.urandom(24).hex()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions (token, user_id) VALUES (?, ?)",
        (token, user_id),
    )
    conn.commit()
    conn.close()

    return token


def get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Look up the user associated with this session token.
    Returns a dict with user info or None if invalid.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT u.id, u.username
        FROM sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.token = ?
        """,
        (token,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# ---------- Friends / Friend Requests ----------


def create_friend_request(from_user_id: int, to_username: str) -> Dict[str, Any]:
    """
    Create a friend request from one user to another (by username).

    Raises ValueError on:
    - user not found
    - sending to self
    - existing friendship / pending request
    """
    # Look up target user
    target = get_user_by_username(to_username)
    if not target:
        raise ValueError("Target user does not exist")

    to_user_id = target["id"]
    if to_user_id == from_user_id:
        raise ValueError("Cannot add yourself as a friend")

    conn = get_connection()
    cur = conn.cursor()

    # Check for any existing relationship in either direction
    cur.execute(
        """
        SELECT id, status
        FROM friend_requests
        WHERE (from_user_id = ? AND to_user_id = ?)
           OR (from_user_id = ? AND to_user_id = ?)
        """,
        (from_user_id, to_user_id, to_user_id, from_user_id),
    )
    row = cur.fetchone()
    if row:
        status = row["status"]
        conn.close()
        if status == "accepted":
            raise ValueError("You are already friends")
        elif status == "pending":
            raise ValueError("A pending friend request already exists")
        else:
            raise ValueError("A friend request already exists")

    # Create new pending request
    cur.execute(
        """
        INSERT INTO friend_requests (from_user_id, to_user_id, status)
        VALUES (?, ?, 'pending')
        """,
        (from_user_id, to_user_id),
    )
    conn.commit()

    request_id = cur.lastrowid
    cur.execute(
        """
        SELECT fr.id, fr.status, u_from.username AS from_username,
               u_to.username AS to_username
        FROM friend_requests fr
        JOIN users u_from ON fr.from_user_id = u_from.id
        JOIN users u_to   ON fr.to_user_id   = u_to.id
        WHERE fr.id = ?
        """,
        (request_id,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row)


def respond_to_friend_request(request_id: int, to_user_id: int, accept: bool) -> Dict[str, Any]:
    """
    Accept or reject a friend request.

    Only the 'to_user' is allowed to respond.
    Raises ValueError if request not found, not pending, or user mismatch.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, from_user_id, to_user_id, status
        FROM friend_requests
        WHERE id = ?
        """,
        (request_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise ValueError("Friend request not found")

    if row["to_user_id"] != to_user_id:
        conn.close()
        raise ValueError("You are not allowed to respond to this request")

    if row["status"] != "pending":
        conn.close()
        raise ValueError("Friend request is not pending")

    new_status = "accepted" if accept else "rejected"
    cur.execute(
        """
        UPDATE friend_requests
        SET status = ?, responded_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (new_status, request_id),
    )
    conn.commit()

    # Return basic info
    cur.execute(
        """
        SELECT fr.id, fr.status,
               u_from.username AS from_username,
               u_to.username   AS to_username
        FROM friend_requests fr
        JOIN users u_from ON fr.from_user_id = u_from.id
        JOIN users u_to   ON fr.to_user_id   = u_to.id
        WHERE fr.id = ?
        """,
        (request_id,),
    )
    out = cur.fetchone()
    conn.close()
    return dict(out)


def get_friend_summary(user_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """
    Return a summary of friendships and friend requests for this user.
    """
    conn = get_connection()
    cur = conn.cursor()

    # Friends are accepted requests in either direction
    cur.execute(
        """
        SELECT u.id, u.username
        FROM friend_requests fr
        JOIN users u ON
            (CASE
               WHEN fr.from_user_id = ? THEN fr.to_user_id
               ELSE fr.from_user_id
             END) = u.id
        WHERE (fr.from_user_id = ? OR fr.to_user_id = ?)
          AND fr.status = 'accepted'
        """,
        (user_id, user_id, user_id),
    )
    friends = [dict(row) for row in cur.fetchall()]

    # Incoming pending requests (others → me)
    cur.execute(
        """
        SELECT fr.id AS request_id, u_from.username AS from_username
        FROM friend_requests fr
        JOIN users u_from ON fr.from_user_id = u_from.id
        WHERE fr.to_user_id = ? AND fr.status = 'pending'
        """,
        (user_id,),
    )
    incoming = [dict(row) for row in cur.fetchall()]

    # Outgoing pending requests (me → others)
    cur.execute(
        """
        SELECT fr.id AS request_id, u_to.username AS to_username, fr.status
        FROM friend_requests fr
        JOIN users u_to ON fr.to_user_id = u_to.id
        WHERE fr.from_user_id = ? AND fr.status = 'pending'
        """,
        (user_id,),
    )
    outgoing = [dict(row) for row in cur.fetchall()]

    conn.close()
    return {
        "friends": friends,
        "incoming_requests": incoming,
        "outgoing_requests": outgoing,
    }


def are_friends(user_id1: int, user_id2: int) -> bool:
    """
    Return True if two users are friends (accepted request in either direction).
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM friend_requests
        WHERE status = 'accepted'
          AND (
                (from_user_id = ? AND to_user_id = ?)
             OR (from_user_id = ? AND to_user_id = ?)
          )
        LIMIT 1
        """,
        (user_id1, user_id2, user_id2, user_id1),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


# ---------- Messages ----------


def save_message(from_user_id: int, to_user_id: int, kind: str, text: str, url: Optional[str]) -> Dict[str, Any]:
    """
    Insert a message into the DB and return its basic info joined with usernames.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO messages (from_user_id, to_user_id, kind, text, url)
        VALUES (?, ?, ?, ?, ?)
        """,
        (from_user_id, to_user_id, kind, text, url),
    )
    conn.commit()
    msg_id = cur.lastrowid

    cur.execute(
        """
        SELECT m.id,
               u_from.username AS from_username,
               u_to.username   AS to_username,
               m.kind,
               m.text,
               m.url,
               m.created_at
        FROM messages m
        JOIN users u_from ON m.from_user_id = u_from.id
        JOIN users u_to   ON m.to_user_id   = u_to.id
        WHERE m.id = ?
        """,
        (msg_id,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row)


def get_conversation(user1_id: int, user2_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Return the most recent messages in the conversation between two users.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT u_from.username AS from_username,
               u_to.username   AS to_username,
               m.kind,
               m.text,
               m.url,
               m.created_at
        FROM messages m
        JOIN users u_from ON m.from_user_id = u_from.id
        JOIN users u_to   ON m.to_user_id   = u_to.id
        WHERE (m.from_user_id = ? AND m.to_user_id = ?)
           OR (m.from_user_id = ? AND m.to_user_id = ?)
        ORDER BY m.created_at ASC, m.id ASC
        LIMIT ?
        """,
        (user1_id, user2_id, user2_id, user1_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]
