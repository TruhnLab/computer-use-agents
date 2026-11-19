# 💻 Computer-Use Agents for Hospital Information Systems

This repository holds our work on autonomous computer-use agents that operate a Hospital Information System (HIS) through its graphical interface, the way a person does. Each agent has its own branch: the harness we are developing now, and the three agents of our submitted evaluation study.

🌐 **Project page:** [truhnlab.github.io/computer-use-agents](https://truhnlab.github.io/computer-use-agents/), with animated examples and preliminary results.

## Current Work: Decision-Model Harness

**Branch: [decision_model_harness](https://github.com/TruhnLab/computer-use-agents/tree/decision_model_harness)**

Our most advanced agent. It works from screenshots only and with self-hosted models, so it can run inside a hospital network.

- **Planner:** a chat model writes the steps for a screen. It is called a few times per request, not for every step
- **Decision model:** answers the short questions in between (which click leads on, did the step work, is the request complete) with one of the given options and a probability
- **Code, without a model:** finds fields from their labels, types, and checks every entry on screen, with RapidOCR for text and a YOLO detector for empty fields and icons
- Runs with a chat model alone if no decision model is available
- React/Flask web console to give requests and watch the agent work

> **Status:** in development. This agent has not been evaluated yet and is **not part of the results of the study below**.

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

---

## Submitted Study: Agents A–C

Three autonomous agents were evaluated on a HIS (Agents A–C). This part of the repository is described in a manuscript that is currently under review. It provides the agent implementations and task definitions to enable reproducibility and comparison of different computer-use agent architectures.

### Agent A ([omniparser_mcp](https://github.com/TruhnLab/computer-use-agents/tree/omniparser_mcp) branch)
- GUI perception handled locally via OmniParser MCP server
- Screenshots converted into structured textual representations
- Reduced model token usage, increased local computation

### Agent B ([openai_computer_use_preview](https://github.com/TruhnLab/computer-use-agents/tree/openai_computer_use_preview) branch)
- Integrated vision, planning, and reasoning
- Screenshots provided directly as image inputs alongside OCR results
- Lower local compute requirements, higher token usage

### Agent C ([openai_gpt54_computer](https://github.com/TruhnLab/computer-use-agents/tree/openai_gpt54_computer) branch)
- Successor of the computer-use-preview model
- Uses OpenAIs GPT-5.4 model with an updated computer tool

The three agents receive natural-language task instructions (listed in [tasks.json](./tasks.json)) to iteratively perceive the GUI, plan actions, and execute mouse and keyboard interactions. For comparability, they share the same predefined action set (mouse movement, clicks, scrolling, text input, keyboard shortcuts).

### Execution Environment
All experiments were conducted using [HospitalRun](https://hospitalrun.io/download), an open-source, offline demo Hospital Information System (HIS).

**System:**
- Windows 11
- Intel Core i9-13900K
- 128 GB RAM
- NVIDIA RTX 4090 (24 GB)

---
## Deployment
Instructions for running the agents can be found in the respective branches.
