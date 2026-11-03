
# 💻 Computer-Use Agents Evaluation

This repository contains all relevant code and material for our evaluation of autonomous computer-use agents on a Hospital Information System (HIS). The materials are intended to provide an unified entry point for agent implementations and task definitions, enable reproducibility and comparison of different computer-use agent architectures and support research on autonomous GUI interaction in clinical software systems.

🌐 **Project page:** [truhnlab.github.io/computer-use-agents](https://truhnlab.github.io/computer-use-agents/), with animated examples of the multi-agent harness and preliminary results.

---
## Computer-Use Agents
Three autonomous agents were evaluated in our experiments (Agents A–C). A fourth agent is an ongoing follow-up that was not part of the evaluation.

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

All agents receive natural-language task instructions (listed in [tasks.json](./tasks.json)) to iteratively perceive the GUI, plan actions, and execute mouse and keyboard interactions. For comparability, they share the same predefined action set (mouse movement, clicks, scrolling, text input, keyboard shortcuts).

## Follow-up Work

### Multi-Agent Harness ([multi_agent_harness](https://github.com/TruhnLab/computer-use-agents/tree/multi_agent_harness) branch)
- Developed after the evaluation and therefore **not part of the reported results**
- Splits the agent into a text-only supervisor that maintains a todo list and a vision actioner that executes one item at a time
- Local perception combines RapidOCR (text) with an OmniParser YOLO detector (icons, buttons, empty fields); completed steps must be confirmed by OCR evidence on screen
- Model behind each role is configurable, plus a React/Flask web UI for task submission and live monitoring

## Execution Environment
All experiments were conducted using [HospitalRun](https://hospitalrun.io/download), an open-source, offline demo Hospital Information System (HIS).

**System:**
- Windows 11
- Intel Core i9-13900K
- 128 GB RAM
- NVIDIA RTX 4090 (24 GB)

## Deployment
Instructions for running or using the agents can be found in the respective branches.
