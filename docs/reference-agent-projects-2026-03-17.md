# GitHub Similar Agent Projects Reference

Date: 2026-03-17

## Selected Projects

### 1. OpenManus
- GitHub: https://github.com/FoundationAgents/OpenManus
- Local path: `C:\Users\35456\reference-projects\OpenManus`
- Local venv: `C:\Users\35456\reference-projects\OpenManus\.venv`
- Installed status: dependency environment installed, config bootstrap validated, import check passed
- Reference value:
  - task-oriented multi-step agent flow
  - tool orchestration and browser/computer-use style execution
  - sandboxed execution and MCP entrypoints

### 2. Mem0
- GitHub: https://github.com/mem0ai/mem0
- Local path: `C:\Users\35456\reference-projects\mem0`
- Local venv: `C:\Users\35456\reference-projects\mem0\.venv`
- Installed status: editable install completed, import check passed
- Reference value:
  - long-term memory layer for AI assistants
  - user/session/agent scoped memory design
  - search/add/update style memory API patterns

### 3. Open WebUI
- GitHub: https://github.com/open-webui/open-webui
- Local path: `C:\Users\35456\reference-projects\open-webui`
- Installed status: source cloned only
- Reference value:
  - strong chat UX and message rendering
  - multi-model / provider routing patterns
  - file, knowledge, tool, and session management UI patterns

## Local Installation Notes

### OpenManus
- Official `requirements.txt` currently has a dependency conflict:
  - `crawl4ai~=0.6.3` requires `Pillow < 11`
  - the same file pins `pillow~=11.1.0`
- Local workaround used on this machine:
  - generated `requirements.local.txt` without the `Pillow` line
  - installed with `Pillow==10.4.0`
  - added missing runtime dependencies `structlog` and `daytona==0.21.8`
  - added a placeholder `config/config.toml` with dummy keys only for bootstrap validation
- Result:
  - `from app.agent.manus import Manus` import passed

### Mem0
- Installed with editable mode into an isolated venv
- Result:
  - `from mem0 import Memory` import passed

### Open WebUI
- Only cloned for now
- Docker CLI exists on this machine, but Docker backend was not running during this setup
- If needed next, it can be started and run as a local reference UI service

## What To Borrow Into true-learning-system

### Borrow from OpenManus
- task lifecycle and execution loop
- tool registry organization
- agent to sandbox / environment boundary

### Borrow from Mem0
- durable memory extraction rules
- user/session scoped memory retrieval
- memory search before response generation

### Borrow from Open WebUI
- session list and message UI polish
- model/provider switch UX
- knowledge/tool surface layout

## Recommended Next Borrowing Order

1. Borrow Mem0-style memory extraction and retrieval into the current agent runtime
2. Borrow OpenManus-style task execution loop and tool dispatch shape
3. Borrow Open WebUI interaction details for agent frontend refinement
