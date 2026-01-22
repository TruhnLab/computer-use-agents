> This repository is an adapted version of the original project available at: [https://github.com/NON906/omniparser-autogui-mcp9](https://github.com/NON906/omniparser-autogui-mcp). We gratefully acknowledge the original authors for their work and contributions.

# OmniParser MCP Server 

This is an [MCP server](https://modelcontextprotocol.io/introduction) that analyzes the screen with [OmniParser](https://github.com/microsoft/OmniParser) and automatically operates the GUI.  
Confirmed on Windows.

## License notes

This is MIT license, but Excluding submodules and sub packages.  
OmniParser's repository is CC-BY-4.0.  
Each OmniParser model has a different license ([reference](https://github.com/microsoft/OmniParser?tab=readme-ov-file#model-weights-license)).

## Installation

1. Please do the following:

```
git clone --branch omniparser_mcp --recursive --single-branch hhttps://github.com/TruhnLab/computer-use-agents.git
cd computer-use-agents
uv sync
set OCR_LANG=en
uv run download_models.py
```

(Other than Windows, use ``export`` instead of ``set``.)  
(If you want ``langchain_example.py`` to work, ``uv sync --extra langchain`` instead.)

2. Add this to your ``claude_desktop_config.json``:

```claude_desktop_config.json
{
  "mcpServers": {
    "omniparser_autogui_mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "D:\\CLONED_PATH\\computer-use-agents",
        "run",
        "computer-use-agents"
      ],
      "env": {
        "PYTHONIOENCODING": "utf-8",
        "OCR_LANG": "en"
      }
    }
  }
}
```

(Replace ``D:\\CLONED_PATH\\computer-use-agents`` with the directory you cloned.)

``env`` allows for the following additional configurations:

- ``OMNI_PARSER_BACKEND_LOAD``  
If it does not work with other clients (such as [LibreChat](https://github.com/danny-avila/LibreChat)), specify ``1``.

- ``TARGET_WINDOW_NAME``  
If you want to specify the window to operate, please specify the window name.  
If not specified, operates on the entire screen.

- ``OMNI_PARSER_SERVER``  
If you want OmniParser processing to be done on another device, specify the server's address and port, such as ``127.0.0.1:8000``.  
The server can be started with ``uv run omniparserserver``.

- ``SSE_HOST``, ``SSE_PORT``  
If specified, communication will be done via SSE instead of stdio.

- ``SOM_MODEL_PATH``, ``CAPTION_MODEL_NAME``, ``CAPTION_MODEL_PATH``, ``OMNI_PARSER_DEVICE``, ``BOX_TRESHOLD``  
These are for OmniParser configuration.  
Usually, they are not necessary.

