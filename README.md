# Computer Use Agent

An agent that works in the window in front on your desktop, from screenshots only. A web console lets you give it requests and watch it work.

## How it works

```
                request
                   │
                   ▼
         ┌───────────────────┐
         │  Read the screen  │   OCR reads all text in the window
         └─────────┬─────────┘
                   ▼
         ┌───────────────────┐   Decider: the clicks that lead to the right view
   ┌────►│  Choose the step  │   Planner: the steps for a form, and a new plan when
   │     └─────────┬─────────┘            a step fails or the screen surprises
   │               ▼
   │     ┌───────────────────┐   Code clicks and types
   │     │   Carry it out    │   YOLO finds empty fields and icons, when a step needs them
   │     └─────────┬─────────┘
   │               ▼
   │     ┌───────────────────┐   OCR reads what changed
   │     │     Check it      │   Code: did the value arrive next to its label?
   │     └─────────┬─────────┘   Decider: did the click work, is the request complete?
   │  not complete │ complete
   └───────────────┤
                   ▼
                answer
```

- **Planner** — a chat model. It writes the steps as JSON and is called a few times per request, not for every step.
- **Decider** — a decision model: it answers a short question with one of the given options and a probability. Without one, the planner's model also decides.
- **OCR** (RapidOCR) and **YOLO** (a YOLO11m trained on screenshots) run locally. OCR runs at every look at the screen, on the changed parts only. YOLO takes several seconds on a CPU and runs only when a step needs it.
- **Code** does the rest without a model: it finds fields from their labels, clicks, types and checks every entry on screen.

Every request leaves a log in `logs/`. The logs hold what the screen showed, so on a real system they hold patient data and stay on the machine.

## Requirements

- Python 3.13 (the version it is developed and tested on), Windows
- Node.js 18+ (for the web console)
- A chat model accessible via a chat completions API (the planner)
- Optional: a decision model served on `/v1/systemone` (the decider)

## Setup

```bash
conda create -n visualagent python=3.13
conda activate visualagent
pip install -r requirements.txt
python download_models.py        # the OCR models and the YOLO weights, one-time
cd frontend && npm install
```

Then copy [.env.example](.env.example) to `.env` and fill in the two model profiles (endpoint, key, model name). That file holds every setting, each with a comment.

## Models

The models are not part of this repository. `download_models.py` fetches them, and their own licences apply:

- **Controls:** a YOLO11m trained on screenshots to find clickable regions. The weights are the file `icon_detect` from [microsoft/OmniParser-v2.0](https://huggingface.co/microsoft/OmniParser-v2.0), AGPL-3.0. They are converted once with [ultralytics](https://github.com/ultralytics/ultralytics), also AGPL-3.0.
- **Text:** PP-OCRv5 through [RapidOCR](https://github.com/RapidAI/RapidOCR), Apache-2.0.

## Running

On Windows, `start_all.bat` starts the backend and the console and opens the console in the browser. By hand:

```bash
cd backend && python app.py      # terminal 1: Flask on http://localhost:5000
cd frontend && npm run dev       # terminal 2: console on http://localhost:3000
```

## Web console

- Type a request and press **Enter**. The plan appears as a list of steps; each step shows how it was checked, or why it failed.
- **Pause** stops the agent before its next action so you can use the mouse and keyboard yourself; **Resume** lets it look at the screen again and adjust its plan.
- **Stop** (or **Esc** while the console has the focus) asks the agent to stop and say where it was. A second press kills it at once.
- If the agent asks a question, answer with one of the offered buttons or by typing.
- The settings (top right) hold the view, the two models and the cursor speed. **Debug** view adds each step's actions, the decider's answers, the time split and the raw log.

## Architecture

```
┌──────────────────┐
│  React console   │  port 3000
└────────┬─────────┘
         │ HTTP + SSE
         ▼
┌──────────────────┐
│  Flask backend   │  port 5000
└────────┬─────────┘
         │ subprocess
         ▼
┌──────────────────┐        ┌──────────────────┐
│  agent.py        │  HTTP  │  Model server    │
│  OCR, YOLO, code │ ─────► │  planner, decider│
└──────────────────┘        └──────────────────┘
```

## Code layout

```
agent.py             entry point; what the backend imports
harness/             the agent
  loop.py            ComputerAgent and the run loop of one request
  reading.py         reading the screen during a run
  models.py          calls to the planner and the decider
  acting.py          one action, the look at the screen after it, checks on typed values
  steps.py           carrying out the steps: fill, select, click, drag, type, ...
  plan.py            the plan and its steps
  prompts.py         what the models are told
  perception.py      OCR and the YOLO detector
  screen.py          the window in front: its picture, comparisons, the annotation
  elements.py        the elements of a screen and their ids
  matching.py        checks on the text of a screen that need no model
  candidates.py      the elements a step can mean
  actions.py         mouse and keyboard
  hotkeys.py         the global stop and pause keys
  storage.py         the session file and the run log
  config.py          the settings, read from .env
  paths.py           where files lie
backend/app.py       Flask backend of the console
frontend/            React console
download_models.py   one-time model setup
```

## API reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/task` | Start a new agent task (`{"task": "..."}`) |
| `POST` | `/api/stop` | Ask the agent to stop (it ends the run itself, within 15 s); `{"force": true}` kills the process at once |
| `POST` | `/api/pause` | Pause the running agent before its next step or input action |
| `POST` | `/api/resume` | Resume a paused agent |
| `POST` | `/api/reset` | Stop any run and start a fresh conversation |
| `GET`  | `/api/run` | The current run (request, start time, line count, active, paused) and the agent's global keys |
| `GET`  | `/api/logs` | Stream the current run's log via Server-Sent Events, from its first line or after `Last-Event-ID` |
| `GET`  | `/api/screenshot` | Latest annotated debug screenshot (PNG); `?view=raw` for the unannotated frame |
| `GET`  | `/api/health` | Health check |
| `GET`  | `/api/info` | Agent and model info |
