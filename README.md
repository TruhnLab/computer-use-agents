
# 💻 Computer-Use Agents for Hospital Information Systems

This repository holds our work on autonomous computer-use agents that operate a Hospital Information System (HIS) through its graphical interface, the way a person does. Each agent has its own branch: the harness we are developing now, and the three agents of our submitted evaluation study.

🌐 **Project page:** [truhnlab.github.io/computer-use-agents](https://truhnlab.github.io/computer-use-agents/), with animated examples and preliminary results.

---
## Current Work: Decision-Model Harness

**Branch: [decision_model_harness](https://github.com/TruhnLab/computer-use-agents/tree/decision_model_harness)**

Our most advanced agent. It works from screenshots only and with self-hosted models, so it can run inside a hospital network.

- **Planner:** a chat model writes the steps for a screen. It is called a few times per request, not for every step
- **Decision model:** answers the short questions in between (which click leads on, did the step work, is the request complete) with one of the given options and a probability
- **Code, without a model:** finds fields from their labels, types, and checks every entry on screen, with RapidOCR for text and a YOLO detector for empty fields and icons
- Runs with a chat model alone if no decision model is available
- React/Flask web console to give requests and watch the agent work

> **Status:** in development. This agent has not been evaluated yet and is **not part of the results of the study below**.

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
