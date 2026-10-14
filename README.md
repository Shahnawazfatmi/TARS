# FATMEE

Desktop AI assistant backend. An LLM planner (OpenAI first, Gemini as fallback) chooses system tools, FATMEE runs them in parallel, checks the results, replans if something is missing, and answers in plain language.

**New here? Read [docs/FATMEE_GUIDE.md](docs/FATMEE_GUIDE.md) first.** It explains every step and idea in simple English.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy backend\.env.example backend\.env   # then add OPENAI_API_KEY (and GEMINI_API_KEY for fallback)
```

## Run

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn main:app --reload --timeout-graceful-shutdown 3
```

Open http://127.0.0.1:8000/docs and try `POST /ask`:

```json
{ "question": "Can my PC run a 7B local LLM?" }
```

`GET /tools` lists every tool FATMEE can use.

## Conversation memory

FATMEE remembers the conversation, so follow-ups work ("How much RAM do I have?" then "And how much is free?"). Questions and answers are saved in `backend/data/fatmee.db`, a local SQLite file that's built into Python and ignored by git, so they survive restarts.

- Send `"session_id": "work"` (letters, digits, `-`, `_`) to keep separate conversations. Leave it out to use one ongoing conversation called `default`.
- `GET /sessions/{session_id}` shows a conversation.
- `DELETE /sessions/{session_id}` forgets it and starts fresh.

## Research agent and MCP

FATMEE connects to external tools through **MCP** servers listed in `backend/mcp_servers.json`. Today that's Tavily web search; LinkedIn, Gmail and GitHub come later.

1. Get a key at tavily.com and add `TAVILY_API_KEY=...` to `backend/.env`.
2. Restart FATMEE. `GET /mcp` should show `tavily: connected`.
3. Ask: "Research the latest AI agent trends this week."

The `research_topic` tool searches the web and returns 3–6 key points, each with numbered source links. For several topics, the planner makes one step per topic and they run at the same time.

- A server whose key is missing is skipped (`GET /mcp` says which key to add). FATMEE still starts, and research answers that search isn't set up.
- `"expose"` in `mcp_servers.json` picks which MCP tools the planner may call directly. `"approval"` marks tools that must wait for your OK, set in code so no MCP server can switch it off.
- The API key is sent in a header, not the URL, and isn't written to logs.

## Word documents

Ask "Research the latest AI agent trends and make a Word report". The `create_document` tool takes the results of the steps it waits for (research, think, system info...) and writes a `.docx` with a title, sections, bullets and a clickable **Sources** list.

- Saved in `Desktop\FATMEE Reports\` unless you name a place ("save it in Documents"). Change the default with `FATMEE_DOCUMENTS_DIR`.
- Never overwrites: a second "report.docx" becomes "report (2).docx".
- Only saves inside your own folders (home, Desktop, Documents, Downloads).
- The AI plans the text in one call, and the code builds the file, so the file is always valid.

## Task diary (episodic memory)

Every task, answered or failed, is saved in the same file with its time, duration, tools, status and a short summary. Ask about the past in plain words: "What did I ask you yesterday?", "Did I check my battery this week?", "What failed today?". The `recall_tasks` tool looks it up, and dates are worked out from your PC's clock.

- `GET /episodes?day=today` (or `yesterday`, `monday`, `2026-10-12`), `&days_back=7`, `&keyword=battery`, `&status=failed`, `&session_id=...`, `&limit=20`.
- Saving happens after the reply is sent, so it adds no wait. Searches stay at about 5–80 ms even with 50,000 entries.

## How a request flows

1. **Planner** turns the question into steps (tool + args).
2. **Executor** runs the tools in parallel.
3. **Verifier** checks for failed or missing tools; the **replanner** gets one recovery try.
4. **Goal verifier** (LLM) checks the results answer the question; one more recovery try if not.
5. The LLM writes the final answer from the tool results.

Every model call goes through `backend/llm.py`: it tries OpenAI first and switches to Gemini automatically if OpenAI fails or returns nothing.

## Tools

| Tool | What it returns |
|---|---|
| `get_system_info` | OS, version, Windows edition, computer name |
| `get_current_directory` | FATMEE's working folder |
| `list_files(path, limit)` | Files and folders with sizes |
| `get_cpu_usage` | Total and per-core CPU %, clock speed |
| `get_ram_usage` | RAM and swap usage |
| `get_disk_usage(drive)` | Space on one drive |
| `get_all_disks` | Space on every drive |
| `get_hardware_info` | CPU model, cores, RAM, all GPUs with real VRAM |
| `get_battery_status` | Battery %, charging, time left |
| `get_network_info` | Adapters, IPs, link speed, data used |
| `check_internet_connection` | Online or not, latency |
| `get_wifi_info` | Wi-Fi name, signal, speed |
| `get_top_processes(sort_by, limit)` | Heaviest programs by memory or CPU |
| `get_system_uptime` | Last boot time and uptime |
| `get_installed_programs(name_filter, limit)` | Installed programs and versions |
| `recall_tasks(day, days_back, keyword, status, limit)` | Past tasks from FATMEE's diary |
| `research_topic(topic, max_sources, time_range)` | Web research with numbered sources (needs `TAVILY_API_KEY`) |
| `think(instruction)` | Works on earlier steps' results: compare, pick, summarize, write. Later steps use its answer as `$s2` |
| `create_document(instruction, title, content, file_name, folder, open_after)` | Word report from earlier steps' results, saved on your PC |
| `tavily_extract` (via MCP) | Reads the content of given web pages (needs `TAVILY_API_KEY`) |

## Settings (`backend/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | one key required | OpenAI API key (`OPEN_AI_API_KEY` also works) |
| `GEMINI_API_KEY` | optional | Gemini API key, used as fallback |
| `FATMEE_LLM_PROVIDERS` | `openai,gemini` | Order providers are tried in |
| `FATMEE_OPENAI_MODEL` | `gpt-5.4-mini` | OpenAI model |
| `FATMEE_GEMINI_MODEL` | `gemini-3.6-flash` | Gemini fallback model |
| `FATMEE_LLM_TIMEOUT` | `60` | Seconds per model call |
| `FATMEE_GOAL_CHECK` | `true` | `false` skips the LLM goal check (one fewer call) |
| `FATMEE_TOOL_WORKERS` | `8` | Parallel tool threads |
| `FATMEE_LOG_LEVEL` | `INFO` | `DEBUG` also prints raw model output |
| `FATMEE_DB_PATH` | `backend/data/fatmee.db` | Where conversations are saved |
| `FATMEE_HISTORY_MESSAGES` | `10` | Recent messages FATMEE reads before answering (10 = last 5 exchanges) |
| `FATMEE_HISTORY_CHARS` | `1500` | Longer earlier answers are shortened to this for the model |
| `FATMEE_DOCUMENTS_DIR` | `Desktop\FATMEE Reports` | Where Word documents are saved |

## Tests

```powershell
pip install -r backend\requirements-dev.txt
python -m pytest backend\tests
```

Tests use a fake model provider, so they need no key or internet. To try the real planner by hand: `cd backend; python try_planner.py "your goal"`.
