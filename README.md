# HospitalRun Navigation Agent

AI-powered agent for navigating and interacting with HospitalRun using Azure OpenAI Computer Use and OCR. This project uses the [computer-use-preview model from OpenAI](https://platform.openai.com/docs/guides/tools-computer-use) for advanced computer interaction capabilities.

## Setup


1. Install Python dependencies:
	```bash
	uv init
	uv venv hack_env
	source hack_env/bin/activate
	uv pip install -r requirements.txt
	```

2. Install frontend dependencies:
	```bash
	cd frontend
	npm install
	```

3. Configure Azure OpenAI in `.env`:

2. Configure Azure OpenAI in `.env`:
```env
AZURE_OPENAI_ENDPOINT=your_endpoint
AZURE_OPENAI_API_KEY=your_key
AZURE_OPENAI_API_VERSION=2025-03-01-preview
AZURE_OPENAI_DEPLOYMENT=your_deployment
```

3. Install Tesseract OCR:
```bash
# macOS
brew install tesseract

# Ubuntu/Debian
sudo apt-get install tesseract-ocr
```

## Usage

You can run the agent in two ways:

### 1. Directly from the command line

```bash
python hospitalrun_agent.py
```

### 2. Using the provided scripts

Run each script in order:
1. `1_run_backend.bat` &mdash; starts the backend server
2. `2_run_frontend.bat` &mdash; launches the web frontend
3. `3_run_agent.bat` &mdash; starts the navigation agent

The agent will use GUI automation and OCR to navigate HospitalRun based on your instructions.
