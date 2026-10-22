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

## Images

Ask "Make a square LinkedIn image about AI agents helping small shops". The `create_image` tool makes one picture, crops it to the exact LinkedIn size and saves a PNG in `Desktop\FATMEE Images\`.

- Shapes: `square` 1200×1200 (default), `landscape` 1200×627, `portrait` 1080×1350.
- Models: OpenAI `gpt-image-1-mini` first, Gemini `gemini-2.5-flash-image` as backup (`FATMEE_IMAGE_PROVIDERS`).
- No text in the picture unless you ask for it. Only 1 image per question (`FATMEE_MAX_IMAGES_PER_TASK`), and a paid image call is never retried automatically.
- Styles: `photo` (realistic, the default: `FATMEE_IMAGE_DEFAULT_STYLE`), `illustration`, `3d`, `isometric`, `graphic`, or your own word ("watercolor").
- When the topic is AI, the AI is shown clearly at work (a screen, robot or device), and agents are shown doing tasks by themselves.
- The image model gets three things: the picture description, FATMEE's fixed rules, and a short research brief (topic, summary, key facts, at most `FATMEE_IMAGE_CONTEXT_CHARS`), so the picture matches what was actually found. The log line `Full image prompt:` shows it.
- The picture shows ONE concrete idea from the results, with no AI clichés (glowing brains, floating dashboards). The log line `Image idea:` shows what was chosen.
- It can show earlier results: "Research AI in retail and make a LinkedIn image about it".

## Approval (FATMEE waits for your OK)

Tools that act on the outside world (posting, sending) are marked `needs_approval` in code. When a plan reaches one, the task **pauses**: `/ask` returns `"status": "waiting_approval"` with the exact content and a `task_id`. The paused task is saved in `backend/data/checkpoints.db`, so it survives a restart.

- `GET /approvals`: tasks waiting for your OK.
- `POST /approvals/{task_id}` with `{"decision": "approve"}`, `{"decision": "reject"}`, or `{"decision": "approve", "changes": {"text": "..."}}`. Add `"step_id"` if several steps wait.
- Finished steps are reused, never run twice. Rejecting a step also skips everything built on it. Recovery plans never pause.
- Try it safely with `practice_post`: "Write a short practice LinkedIn post about my RAM". It saves the post to `Desktop\FATMEE Practice Posts\` after you approve and publishes nothing.

## LinkedIn

FATMEE posts to **your personal LinkedIn profile** through **its own MCP server** (`backend/local_mcp/linkedin_server.py`), using only the official LinkedIn API and OAuth 2.0. LinkedIn has no official MCP server, and the community ones use cookies or the retired `ugcPosts` API.

- Tools: `linkedin_status`, `linkedin_get_profile`, `linkedin_create_post` 🔒 (always needs your approval, 1 per question), plus the `write_post` agent.
- "…post about it on LinkedIn" plans research → `write_post` + `create_image` → `linkedin_create_post` 🔒. "Write a post" only writes it.
- **Dry run is on by default** (`FATMEE_LINKEDIN_DRY_RUN=true`): it checks everything and publishes nothing.
- The login token is saved encrypted for your Windows account (DPAPI) in `backend/data/`, never in `.env` or git. The server process only gets the settings in `pass_env` (it is started with `FATMEE_NO_DOTENV=1`, so it never reads `.env` itself).
- Supported: login, your name/picture/email, posts with text or text + 1 image. Not supported: company pages, reading feeds, analytics (LinkedIn must approve those products).

**Set up your LinkedIn developer app (once, about 10 minutes):**
1. **A LinkedIn Company Page.** LinkedIn only lets you create an app linked to a page you manage. A free page for yourself is fine: LinkedIn → *For Business* → *Create a Company Page*.
2. **Create the app** at https://www.linkedin.com/developers/apps → *Create app*. Give it a name (e.g. "FATMEE"), pick your Company Page and upload a logo. If LinkedIn asks, verify the app from the *Settings* tab (a page admin, i.e. you, clicks the link).
3. **Products tab:** request **"Share on LinkedIn"** and **"Sign In with LinkedIn using OpenID Connect"**. These are self-serve and normally approved straight away.
4. **Auth tab → Authorized redirect URLs:** add exactly `http://127.0.0.1:8000/linkedin/callback`. If LinkedIn doesn't accept it, add `http://localhost:8000/linkedin/callback` instead and put `LINKEDIN_REDIRECT_URI=http://localhost:8000/linkedin/callback` in `.env`.
5. **Auth tab:** check that the scopes list shows `openid`, `profile`, `email` and `w_member_social`.
6. **Auth tab:** copy the **Client ID** and **Primary Client Secret** into `backend/.env`:
   ```
   LINKEDIN_CLIENT_ID=...
   LINKEDIN_CLIENT_SECRET=...
   ```
   Never share the secret or put it anywhere else. `.env` is ignored by git.
7. **Restart FATMEE.** `GET /linkedin/status` should show `"mcp_server": {"state": "connected"}`.
8. **Log in once:** open http://127.0.0.1:8000/linkedin/connect in your browser → log in → *Allow* → you see "Connected to LinkedIn". This repeats about every 60 days.
9. **Practise in dry run** (the default): "Research X and post it on LinkedIn" → approve → FATMEE checks everything and says "Dry run: nothing was published".
10. **Go live:** set `FATMEE_LINKEDIN_DRY_RUN=false` in `.env` and restart. From then on, an approved post is really published, and you get its link.

## Live progress

Every task reports its progress: planning, planned, wave, step, approval_needed, waiting_approval, approved, checking, answering, done.

- `GET /tasks/{task_id}/events?after=0`: what happened so far.
- `GET /tasks/{task_id}/stream`: follow it live (Server-Sent Events).
- Send your own `"task_id"` in `/ask` to follow a task while it runs. Events are kept in memory for recent tasks only.

## Task diary (episodic memory)

Every task, answered or failed, is saved in the same file with its time, duration, tools, status and a short summary. Ask about the past in plain words: "What did I ask you yesterday?", "Did I check my battery this week?", "What failed today?". The `recall_tasks` tool looks it up, and dates are worked out from your PC's clock.

- `GET /episodes?day=today` (or `yesterday`, `monday`, `2026-10-12`), `&days_back=7`, `&keyword=battery`, `&status=failed`, `&session_id=...`, `&limit=20`.
- Saving happens after the reply is sent, so it adds no wait. Searches stay at about 5–80 ms even with 50,000 entries.

## Task logs

Every task is written as one JSON line to `backend/logs/tasks.jsonl`: the plan, each tool call (inputs, time, result or error), checks, recovery attempts, each AI call (model, time, tokens when the provider reports them), the answer, and the outcome (`task_status`: success / partial / failed / incomplete, plus `failure_category`). Secrets are removed and long values cut before writing. The folder is ignored by git.

```powershell
cd backend
..\.venv\Scripts\python.exe analyze_logs.py          # summary; --json for JSON
```

Record fields, status rules and settings: "Task logs" in [docs/FATMEE_GUIDE.md](docs/FATMEE_GUIDE.md). Costs stay `null` unless you set `FATMEE_LLM_PRICES`.

## Adding things

See "How FATMEE grows" in [docs/FATMEE_GUIDE.md](docs/FATMEE_GUIDE.md): a new tool is one `Tool(...)` entry in `backend/tools/registry.py`, an outside service is one entry in `mcp_servers.json`, and a new AI model company is one class in `llm.py` or `image_gen.py`.

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
| `create_image(description, instruction, shape, style, text_in_image, file_name, folder, open_after)` | A LinkedIn-sized PNG, saved on your PC |
| `write_post(instruction, include_sources)` | Writes one LinkedIn post (plain text, hook, hashtags, sources). Publishes nothing |
| `linkedin_status` / `linkedin_get_profile` (via MCP) | Connected? as whom? until when? / your LinkedIn name, picture, email |
| `linkedin_create_post(text, image_path, alt_text, visibility)` 🔒 (via MCP) | Publishes on your profile after your OK (dry run by default) |
| `practice_post(text, title, image_path)` 🔒 | Practice post: waits for your OK, then saves the text to a file. Publishes nothing |
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
| `FATMEE_IMAGE_PROVIDERS` | `openai,gemini` | Order image models are tried in |
| `FATMEE_OPENAI_IMAGE_MODEL` | `gpt-image-1-mini` | OpenAI image model (`gpt-image-2` looks better, costs more) |
| `FATMEE_OPENAI_IMAGE_QUALITY` | `medium` | `low`, `medium` or `high` |
| `FATMEE_GEMINI_IMAGE_MODEL` | `gemini-2.5-flash-image` | Gemini backup image model |
| `FATMEE_IMAGE_DEFAULT_STYLE` | `photo` | Look used when you name none (empty = the AI picks) |
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` | none | From your LinkedIn app's Auth tab. Without them the LinkedIn server is skipped |
| `LINKEDIN_REDIRECT_URI` | `http://127.0.0.1:8000/linkedin/callback` | Must match the app's Authorized redirect URL |
| `FATMEE_LINKEDIN_DRY_RUN` | `true` | `false` = really publish approved posts |
| `FATMEE_LINKEDIN_VERSION` | `202609` | LinkedIn API version (YYYYMM) |
| `FATMEE_CHECKPOINT_DB_PATH` | `backend/data/checkpoints.db` | Saved state of paused tasks |
| `FATMEE_PRACTICE_DIR` | `Desktop\FATMEE Practice Posts` | Where practice posts are saved |
| `FATMEE_IMAGES_DIR` | `Desktop\FATMEE Images` | Where images are saved |
| `FATMEE_MAX_IMAGES_PER_TASK` | `1` | Most images made for one question |

## Tests

```powershell
pip install -r backend\requirements-dev.txt
python -m pytest backend\tests
```

Tests use a fake model provider, so they need no key or internet. To try the real planner by hand: `cd backend; python try_planner.py "your goal"`.
