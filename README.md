# PingPong Web Chat Application

A lightweight real-time chat application built for **CS 4390 – Computer Networks**.

The app supports:
- User registration and login  
- Friend requests and acceptance  
- Real-time messaging via WebSockets  
- File upload and sharing  
- Persistent message history stored in SQLite  

Frontend: HTML/CSS/JavaScript  
Backend: FastAPI (Python)

---

## Clone the repo to your device

## How to Run the Backend

### 1. Install dependencies

    cd backend
    pip install fastapi "uvicorn[standard]" python-multipart

### 2. Start the backend server

    uvicorn main:app --reload --port 8000

Backend will run at:

    http://127.0.0.1:8000

API docs:

    http://127.0.0.1:8000/docs

---

## How to Run the Frontend

No build system needed — static HTML.

### 3. Open the login page

Open the file in cloned repo folder:

    frontend/index.html

After logging in, you will be redirected to:

    app.html

---

## How to Test the Chat

To simulate two users:

1. Open `index.html` normally  
2. Open `index.html` again in incognito/private mode  
3. Register two different accounts  
4. Send a friend request and accept it  
5. Select a friend to open the chat  
6. Send messages — they appear instantly on both sides  
7. Upload files with the 📎 button  
8. Refresh — previous messages are restored from history  

---

## Features Implemented

- Login / Register  
- Friend system  
- Real-time chat (WebSocket)  
- File upload  
- SQLite message history  
- Clean, simple UI  

---

## Project Structure

    web-chat-app/
    │
    ├── backend/
    │   ├── main.py
    │   ├── db.py
    │   ├── chat.db
    │   ├── uploads/
    │   └── _pycache_/
    │
    └── frontend/
        ├── index.html
        ├── app.html
        ├── app.js
        └── style.css

---

## Notes

- Designed for single-device local testing  
- Possible future upgrades: LAN support, group chats, cloud deployment  