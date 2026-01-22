# Computer Use Agent - Web Interface

Clean, dark-mode web interface for the Computer Use Agent with real-time debug logs.

## Features

- 🎨 Modern dark mode UI
- 💬 Chat-style interface for tasks
- 🔍 Expandable debug panel with real-time logs
- ⚡ Fast React + Flask stack
- 🔄 Live streaming of agent output

## Setup

### Backend (Flask)

```bash
# Navigate to backend directory
cd backend

# Install Python dependencies
pip install -r requirements.txt

# Run Flask server
python app.py
```

Backend runs on: **http://localhost:5000**

### Frontend (React)

```bash
# Navigate to frontend directory
cd frontend

# Install Node dependencies
npm install

# Run development server
npm run dev
```

Frontend runs on: **http://localhost:3000**

## Usage

1. Start both backend and frontend servers
2. Open http://localhost:3000 in your browser
3. Enter a task in the chat input (e.g., "Create a new patient")
4. Watch the agent work in real-time
5. Expand the debug panel (bottom) to see detailed logs

## Architecture

```
┌─────────────────┐
│  React Frontend │  (Port 3000)
│   Dark Mode UI  │
└────────┬────────┘
         │ HTTP + SSE
         ▼
┌─────────────────┐
│  Flask Backend  │  (Port 5000)
│   Agent Runner  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Computer Agent  │
│  (PyAutoGUI)    │
└─────────────────┘
```

## API Endpoints

- `POST /api/task` - Start a new agent task
- `GET /api/logs` - Stream logs via Server-Sent Events
- `GET /api/health` - Health check

## Tips

- Use the debug panel to troubleshoot issues
- Logs stream in real-time as the agent works
- Tasks are queued - wait for completion before starting new ones
