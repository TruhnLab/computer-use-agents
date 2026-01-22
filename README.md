
# 💻 Computer-Use Agents Evaluation

This repository contains all relevant code and material for our evaluation of autonomous computer-use agents on a clinical software environment. It links agent implementations, task definitions, and the experimental setup used for our benchmarks of GUI interaction capabilities in a Hospital Information System (HIS).

This repository is intended to:
- Provide a unified entry point for agent implementations and task definitions
- Enable reproducibility and comparison of different computer-use agent architectures
- Support research on autonomous GUI interaction in clinical software systems

---
## 🤖 Computer-Use Agents
Two autonomous agents were evaluated:

### Agent A ([omniparser_mcp](https://github.com/TruhnLab/computer-use-agents/tree/omniparser_mcp) branch)
- GUI perception handled locally via OmniParser
- Screenshots converted into structured textual representations
- Reduced model token usage, increased local computation

### Agent B ([openai_computer_use_preview](https://github.com/TruhnLab/computer-use-agents/tree/openai_computer_use_preview) branch)
- Integrated vision, planning, and reasoning
- Screenshots provided directly as image inputs
- Lower local compute requirements, higher token usage

**Both agents:**
- Receive natural-language task instructions (listed in [tasks.json](./tasks.json))
- Iteratively perceive the GUI, plan actions, and execute mouse and keyboard interactions
- Share the same predefined action set (mouse movement, clicks, scrolling, text input, keyboard shortcuts)


## 🧪 Evaluation Environment
All experiments were conducted using [HospitalRun](https://hospitalrun.io/download), an open-source, offline demo Hospital Information System (HIS).

**Execution environment:**
- Windows 11
- Intel Core i9-13900K
- 128 GB RAM
- NVIDIA RTX 4090 (24 GB)

## 🚀 Usage
Instructions for running or using the agents can be found in the respective branches.
