# Multi-Agent Harness

A computer-use agent that controls the foreground window of a Windows desktop through a **hierarchical supervisor / actioner split**. A React + Flask web interface lets you submit a task in natural language, pick which model plays which role, and watch the plan and the agent's actions unfold in real time.

Unlike a single-model control loop, this harness separates *planning* from *acting* and verifies progress against what is actually on screen — so it can drive native Windows applications and Citrix-hosted remote apps where no structural UI tree is available.

---

## How it works

### Perception

Every step captures the active foreground window and runs two signals in parallel:

- **RapidOCR** (PPOCRv5, Latin) — text elements with confidence ≥ 0.9. Drawn as orange boxes.
- **OmniParser YOLOv8** (via ONNX Runtime) — interactive regions *without* text: buttons, icons, empty fields. Drawn as blue boxes. A YOLO box overlapping an OCR box by ≥ 5 % of that OCR box's area is suppressed as redundant.

Both lists are merged into a stable element registry with persistent IDs, and the annotated screenshot — boxes, IDs, and a crosshair at the last click — is what the model sees and what the web UI displays. A short burst of frames is used to detect and remove the blinking text caret, so a caret does not register as a screen change.

### The three roles

| Role | Sees | Needs vision | Job |
|---|---|---|---|
| **Supervisor** | The user's full request plus a compact state summary. **Never** a screenshot. | No | Owns a dynamically growing todo list. Called at *sub-task boundaries* and on *alarms* — not after every action. |
| **Actioner** | The annotated screenshot and the **single current todo item**. Never the user's full request. | Yes | Executes mouse/keyboard actions until it reports `subtask_done` or `subtask_blocked`. |
| **Narrator** | The annotated screenshot. Reuses the actioner's model. | Yes | One-line plain-language commentary per step for the UI. |

The information asymmetry is deliberate: the actioner cannot drift toward the overall goal because it never sees it, and the supervisor cannot hallucinate screen contents because it never sees a screenshot.

### Machine-verified progress

Ticking off a todo item is not the model's word alone:

- An item may declare an `expected_value` and an `expected_label`. Evidence must be found by OCR **and** sit geometrically adjacent to the right field label.
- A deterministic **wrong-field guard** runs after every action and raises a supervisor alarm the moment a value lands next to the wrong label.
- Only the supervisor may declare `task_complete`, and only with machine-checked final evidence and no open items. A premature completion is rejected and fed back.

Additional safety nets: a thrashing detector over the recent action trail, a wait-aware stuck counter, and a tight context diet (two recent full observations; older tool call/result pairs collapsed to one-line trail entries).

### Action set

`click` · `double_click` · `click_and_type` · `type_text` · `delete_chars` · `keypress` · `scroll` · `wait` · `list_windows` · `focus_window` · `open_application` · `calculate` · `subtask_done` · `subtask_blocked`

Every action carries a `thought` parameter — the model states what changed, what it is doing, and what it expects *before* acting. Those thoughts become the progress trail.

---

## Requirements

- Windows (foreground-window capture and window management use the Win32 API)
- Python 3.13+
- Node.js 18+
- One or more **OpenAI-compatible** chat-completions endpoints. At least one must serve a **vision** model.

## Setup

### 1. Python dependencies

```bash
pip install -r requirements.txt
pip install -r backend/requirements.txt
```

### 2. Download models (one-time)

```bash
python download_models.py
```

This downloads and verifies:

- **RapidOCR** models to `~/.rapidocr/models/`
- **OmniParser YOLO** model to `models/icon_detect.onnx` (~100 MB from HuggingFace, exported to ONNX)

Both are excluded from version control and recreated by this script.

### 3. Frontend dependencies

```bash
cd frontend
npm install
```

### 4. Configuration

Copy the template and fill in your own endpoints:

```bash
cp .env.example .env
```

The agent uses a **profile system**. A profile is any set of variables sharing a prefix:

```env
<PROFILE>_ENDPOINT   # OpenAI-compatible /v1/chat/completions URL
<PROFILE>_API_KEY    # bearer token
<PROFILE>_MODEL      # model name as served by the endpoint
<PROFILE>_VISION     # 1 = vision-capable, 0 = text-only (default 1)
```

Any number of profiles may coexist; a profile is discovered automatically once it has both a `*_MODEL` and an `*_ENDPOINT`. Assign the roles with:

```env
ACTIVE_PROFILE=example_vision   # the ACTIONER — must be vision-capable
NAVIGATOR_PROFILE=SAME          # the SUPERVISOR — SAME reuses the actioner model
```

Vision profiles are offered as both actioner and supervisor in the UI; text-only profiles (`_VISION=0`) are offered as supervisor only. Both roles can also be switched at runtime from the web interface. See [.env.example](.env.example) for the full set of optional tuning variables (perception thresholds, per-subtask iteration budget, cursor glide, caret suppression, logging).

Two settings deserve attention when using **reasoning** models: they spend their output budget on hidden thinking before emitting a tool call, so `NAVIGATOR_MAX_TOKENS` and `ACTIONER_MAX_TOKENS` need several thousand tokens rather than the direct-answer defaults; and a non-streaming vLLM server returns nothing until generation completes, so `REQUEST_TIMEOUT` may need raising above 120 s.

## Running

On Windows, one script starts everything:

```
start_all.bat
```

It opens the Flask backend and the Vite dev server in their own windows, then opens the UI in your browser. Close either window to stop that service.

Or start them manually:

```bash
# Terminal 1 — backend
cd backend
python app.py

# Terminal 2 — frontend
cd frontend
npm run dev
```

Then open **http://localhost:3000**.

The agent can also be driven from the console without the web stack:

```bash
python agent.py
```

## Web interface

- **Actioner** and **Supervisor** dropdowns pick the model for each role for the next task; the lists are populated from your profiles and refresh automatically.
- **Cursor Speed** controls how fast the pointer travels to a click target — useful for applications that drop events on instant jumps, and for watching what the agent does.
- The **plan panel** mirrors the supervisor's todo list live with per-item status, including corrective items inserted after a mistake.
- The **event stream** shows narration, supervisor verdicts, actions and raw log output as they happen.
- The **screenshot panel** shows the annotated frame the model is currently looking at.
- **⏹ Stop** terminates the agent immediately and re-enables the input.

## Architecture

```
┌─────────────────┐
│  React Frontend │  (Port 3000)
└────────┬────────┘
         │ HTTP + SSE
         ▼
┌─────────────────┐
│  Flask Backend  │  (Port 5000)
│   Agent Runner  │
└────────┬────────┘
         │ subprocess
         ▼
┌────────────────────────────────────────────────┐
│                    agent.py                    │
│                                                │
│   SUPERVISOR  ── owns the todo list, called    │
│    (text)        at boundaries and alarms      │
│        │  one item                             │
│        ▼                                       │
│   ACTIONER    ── bounded subroutine, acts      │
│    (vision)      until done/blocked            │
│        │                                       │
│        ▼                                       │
│   ┌───────────┐ ┌──────────┐                   │
│   │ RapidOCR  │ │   YOLO   │  (parallel)       │
│   │  (text)   │ │ (icons)  │                   │
│   └─────┬─────┘ └────┬─────┘                   │
│         └──────┬─────┘                         │
│          merge + dedup + verify                │
│                │                               │
│           PyAutoGUI                            │
└────────────────────────────────────────────────┘
```

## API reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/task` | Start a new agent task (`{"task": "..."}`) |
| `POST` | `/api/stop` | Terminate the running agent immediately |
| `POST` | `/api/reset` | Clear the conversation and start fresh |
| `GET`  | `/api/logs` | Stream agent logs via Server-Sent Events |
| `GET`  | `/api/screenshot` | Latest annotated debug screenshot (PNG) |
| `GET`  | `/api/models` | Available model profiles and their vision capability |
| `POST` | `/api/select_model` | Assign a profile to the actioner or supervisor role |
| `GET`  | `/api/cursor_speed` | Current cursor glide speed |
| `POST` | `/api/cursor_speed` | Set cursor glide speed |
| `GET`  | `/api/health` | Health check |
| `GET`  | `/api/info` | Agent and model info |

---

Part of the [computer-use-agents](https://github.com/TruhnLab/computer-use-agents) evaluation — see the [main](https://github.com/TruhnLab/computer-use-agents/tree/main) branch for the study context and task definitions.
