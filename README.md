# Multi-Agent Harness

Computer-use agent that controls the foreground window of a Windows desktop through a hierarchical **supervisor / actioner** split. The supervisor (text-only) owns a growing todo list and never sees the screen; the actioner (vision) works one todo item at a time and never sees the full task. Perception combines RapidOCR for text with an OmniParser YOLO detector for icons, buttons and empty fields. Completing an item requires OCR evidence on screen, not the model's own claim.

A React + Flask web interface submits tasks, switches the model behind each role, and streams the plan and actions live. Confirmed on Windows.

## Setup

1. Install Python dependencies:
	```bash
	uv venv visualagent
	uv pip install -r requirements.txt -r backend/requirements.txt
	```

2. Download the OCR and icon-detection models (one-time, ~100 MB):
	```bash
	python download_models.py
	```

3. Install frontend dependencies:
	```bash
	cd frontend
	npm install
	```

4. Create a `.env` file — copy [`.env.example`](.env.example) and fill in your own endpoints:
	```env
	ACTIVE_PROFILE=example_vision   # the ACTIONER  — must be vision-capable
	NAVIGATOR_PROFILE=SAME          # the SUPERVISOR — SAME reuses the actioner model

	EXAMPLE_VISION_ENDPOINT=https://your-llm-host/v1/chat/completions
	EXAMPLE_VISION_API_KEY=your_api_key
	EXAMPLE_VISION_MODEL=your-model-name
	EXAMPLE_VISION_VISION=1         # 1 = vision-capable, 0 = text-only
	```

	Any number of profiles may coexist — each is discovered automatically from its `*_ENDPOINT` and `*_MODEL`. Both roles are also switchable at runtime from the web UI. See `.env.example` for the optional tuning variables.

## Usage

Start everything with the provided script:

```
start_all.bat
```

It launches the Flask backend (port 5000) and the Vite dev server (port 3000), then opens the web UI at **http://localhost:3000**.

Or run the parts yourself:

```bash
cd backend && python app.py     # terminal 1 — backend
cd frontend && npm run dev      # terminal 2 — frontend
python agent.py                 # or drive the agent from the console
```

---

Part of the [computer-use-agents](https://github.com/TruhnLab/computer-use-agents) evaluation — see the [main](https://github.com/TruhnLab/computer-use-agents/tree/main) branch for study context and task definitions.
