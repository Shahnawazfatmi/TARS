# FATMEE Guide

A simple guide to how FATMEE is built: every step we took, why, and how it works.
Read it top to bottom once. Later, jump to the step you need.

*Last updated: 14 Oct 2026, after Step 10.*

---

## 1. What FATMEE will be

A **voice-controlled AI coworker**, like TARS from Interstellar:

- You talk to it. It understands what you mean.
- It has a personality: funny, honest, a bit sarcastic. You can tune it.
- It remembers today's conversation, yesterday's tasks, and the people you know.
- It does real work, several jobs at the same time: web research, Word documents, images, LinkedIn posts, Gmail.
- Anything public (a post, an email) **always waits for your OK**.

Example: *"Hello FATMEE, research 5 trending AI topics, pick one, and post it on LinkedIn."*

---

## 2. How we work

Every part follows the same six steps:

1. **Explain the approach** in simple words.
2. **You approve.**
3. **Build it.** Test offline first (free), then 1–2 live questions only, because the OpenAI key has limited credit.
4. **Update the docs**: this guide and the change log.
5. **You test it.**
6. **Commit only if you say yes.**

---

## 3. The big picture: what happens when you ask something

```
You ask a question
   │
   ▼
main.py            receives it, reads the recent conversation (memory)
   │
   ▼
graph.py           FATMEE's "brain map" (LangGraph). Runs these steps:
   │
   ├─ 1. Plan      planner.py makes a step list (which tools, in which order)
   ├─ 2. Check     plan_check.py fixes small mistakes, works out "waves"
   ├─ 3. Run       executor.py runs the steps wave by wave
   │                 - independent steps run at the same time
   │                 - "$s1" inputs are filled in from earlier results
   ├─ 4. Verify    verifier.py: did every tool work?
   ├─ 5. Goal?     goal_verifier.py: does this answer the question?
   ├─ 6. Fix       replanner.py tries other tools if something is missing
   └─ 7. Answer    writes the final answer (with sources for research)
   │
   ▼
memory.py          saves the conversation + a diary entry (after you get the answer)

Agents a step can use: research (web), think (reason), document (Word file)
```

All AI calls go through `llm.py`: **OpenAI first, Gemini as a backup**.

---

## 4. The steps so far

### Step 1: Clean foundation (12 Oct)

**What:** Fixed the bugs in the original code and made it faster.

**Why:** Everything else is built on this. A shaky base means problems later.

**What changed:**
- Errors from the AI no longer crash the server; you get a clear message.
- If the planner's answer was broken, FATMEE used to guess. Now it reports an error.
- The CPU name and real GPU memory are read correctly.
- The requirements file was broken (wrong format, missing a package). Fixed.
- Tools run at the same time instead of one by one.
- 8 new Windows tools: battery, network, internet check, Wi-Fi, top programs, uptime, installed programs, all drives.
- Tools can take inputs, e.g. "list files in `~/Desktop`".

**Files:** `config.py`, `llm.py`, `executor.py`, `tools/`, `main.py`

---

### Step 2: OpenAI first, Gemini as backup (12 Oct)

**What:** Every AI call tries OpenAI (`gpt-5.4-mini`). If it fails, it uses Gemini.

**Why:** If one AI service is down, FATMEE keeps working.

**Learned:** Gemini's free tier allows only 20 requests a day. When the limit was hit, the Gemini library waited for hours. Now it fails in about 1 second instead.

**Files:** `llm.py`, `config.py`

---

### Step 3: Clear reasons for rejected requests (12 Oct)

**What:** When a request is badly formatted, the log says why.

**Example:** `JSON decode error | body: 'List my files'` means the text is missing `{"question": ...}`.

**Files:** `main.py`

---

### Step 4: Fixes from your first test (13 Oct)

- **"I can't do that" got faster.** If no tool can answer (like GPU temperature), FATMEE says so instead of trying random tools.
- **"No" is an answer.** "Folder not found" or "not installed" is reported plainly, not as a failure.
- **Top programs: 6.5 s → 0.5 s.** One Windows call reads all programs at once.
- **Similar names.** "Microsoft Office" not found? It mentions WPS Office.

---

### Step 5: Smart planner with LangGraph (13 Oct)

**What:** The flow became a LangGraph "brain map", and plan steps can wait for each other.

**Why LangGraph:** FATMEE will become a multi-agent system with approval pauses and memory. LangGraph is built for exactly that. Using it now avoids rewriting everything later.

**How a plan looks now:**
```
s1  get_hardware_info                    (waits for nothing)
s2  get_ram_usage     depends_on: [s1]   (waits for s1)
```

**Waves:** everything that's ready runs together.
```
Wave 1: s1
Wave 2: s2
```

**Plan checker:** fixes small AI mistakes ("1" instead of "s1") and refuses plans where steps wait on each other forever.

**Approval is decided in code:** each tool has a fixed "needs approval" setting. The AI can't switch it off.

**Files:** `graph.py`, `planner.py`, `plan_check.py`, `executor.py`

---

### Step 6: Working memory, the conversation (13 Oct)

**What:** FATMEE remembers what was said, so follow-ups work.
```
You:    How much RAM do I have?        → 14.83 GB
You:    And how much of it is free?    → 4.69 GB   (it knows "it" = RAM)
```

**How:** each question and answer is saved in a local file, `backend/data/fatmee.db`. FATMEE reads the last 10 messages before answering.

**About SQLite:** it's a database that's **built into Python**: no install, no server, no password. The whole database is one file. Git ignores it, so it's never committed.

**Sessions:** add `"session_id": "work"` to keep separate conversations. `GET /sessions/{id}` shows one, and `DELETE /sessions/{id}` forgets it.

**Files:** `memory.py` (ConversationStore)

---

### Step 7: Episodic memory, the diary (13 Oct)

**What:** FATMEE keeps a diary of every task: when, what, which tools, did it work.

**Try:** "What did I ask you today?", "Did I check my battery this week?", "What failed yesterday?"

**How:**
- One diary row per task, in the same SQLite file. Failed tasks are included.
- A tool called `recall_tasks` searches the diary.
- **Dates are worked out by code**, not the AI, so "yesterday" is always right.
- The diary is saved **after** you get your answer, so you never wait for it.
- It's fast even when large: with 50,000 tasks, a search takes 5–80 ms.

**Files:** `memory.py` (EpisodeStore), `tools/memory_tools.py`

---

### Step 8: MCP + research agent (13 Oct)

**What is MCP?** A standard plug for outside tools. Instead of custom code for every service, FATMEE connects to "MCP servers". Today: Tavily (web search). Later: LinkedIn, Gmail, GitHub.

**Why research matters:** a plain AI call only knows what it learned during training. It doesn't know this week's news and may make things up. The research agent searches the **live web** and gives **source links** you can check.

**How `research_topic` works:**
1. Searches the web through Tavily.
2. The AI writes 3–6 key points **using only what it found**.
3. Each point ends with a source number, like [1], and the answer lists the links.

**Several topics run at the same time:** "Research AI agents and AI rules in India" means 2 steps in the same wave.

**Safety:**
- A missing key just skips the service, and `GET /mcp` tells you which key to add.
- The key is sent hidden in a header and never written to logs.
- FATMEE's own settings decide which outside tools may run and which need approval.

**Files:** `mcp_client.py`, `mcp_servers.json`, `agents/research.py`

---

### Step 9: Steps that use earlier results (13 Oct)

**What:** A step can now **use the answer** of an earlier step, not just wait for it.

**Two new pieces:**

**1. The `think` step:** the AI reads earlier results and does one job: compare, pick, shortlist, summarize, write.

**2. References like `$s2`:** a step's input can say "use s2's answer". **The code fills it in**, so it's exact and free.

**Real example** (tested live):
```
You: "Find the drive with the most free space, then list the files on that drive"

s1  get_all_disks                                   Wave 1
s2  think: "Pick the drive with most free space.    Wave 2
           Return only its path."        → "C:\"
s3  list_files(path = "$s2")                        Wave 3
                       ↑ code fills in "C:\"
```

**Rules:**
- `$s2` = the whole answer. `$s1.free_gb` = one field of it.
- Using `$s2` automatically makes the step wait for s2.
- If s2 failed, the step that needs it is **skipped** with a clear message, never run with wrong input.
- `$5` or `$USD` in normal text is left alone.

**Why it matters:** this is what makes your LinkedIn example possible:
"research 5 topics → **think**: pick the best → research **$s6** deeper → write → post".

**Files:** `references.py`, `agents/thinker.py`, `executor.py`, `plan_check.py`

---

### Step 10: Document agent, Word reports (14 Oct)

**What:** FATMEE can make a real Word (`.docx`) file from what it found.

**Try:** "Research the latest AI agent trends this week and make a short Word report."

**Real example** (tested live, 22 seconds, about 5 AI calls):
```
s1  research_topic "AI agent trends this week"     Wave 1   (10.7 s)
s2  create_document  depends_on: [s1]              Wave 2   (4.3 s)
      → "AI Agent Trends This Week.docx"
        title, 7 sections, 442 words, 5 clickable sources
```

**How it works:**
1. The document step **reads the results of the steps it waits for**, the same way the think step does.
2. **The AI plans the text** (title, intro, sections, bullets) in one call, using only those results.
3. **The code builds the file** with `python-docx`, a free library that works offline. You don't need Word installed to make the file. Because the code does the formatting, the file is always valid and looks the same every time.
4. Research sources become a **"Sources" list with clickable links**. If several topics were researched, their sources are joined into one list and the [1], [2] numbers are fixed to match it. The same link used twice gets one number.

**Rules:**
- Saved in `Desktop\FATMEE Reports\` unless you name a place ("save it in Documents").
- **Never overwrites.** If the name is taken, it saves "report (2).docx".
- **Only your folders.** It refuses to save in places like `C:\Windows`.
- No approval needed: saving on your own PC is private, unlike a post or an email.
- The planner doesn't add a separate think step to write the text, which saves one AI call.

**Learned from the live test:** the planner chose "save to desktop" and "open it" by itself, even though you didn't ask. The tool's instructions now say to leave those out unless you ask. The log is also shorter now: it no longer prints the whole research result again.

**Files:** `agents/document.py`, `tools/registry.py` (the `takes_inputs` flag), `executor.py`, `config.py`

---

## 5. Words you'll see

| Word | Meaning |
|---|---|
| **LLM** | The AI model (OpenAI or Gemini) that understands and writes text |
| **Tool** | One thing FATMEE can do, like read RAM or search the web |
| **Agent** | A tool that uses the AI to do a bigger job (research, think) |
| **Planner** | Decides which tools to use and in which order |
| **Step / `s1`** | One item in the plan |
| **`depends_on`** | "Wait for these steps first" |
| **Wave** | A group of steps that run at the same time |
| **`$s1`** | "Use s1's answer here" |
| **LangGraph** | The library that runs FATMEE's brain map |
| **MCP** | A standard plug for outside services (search, LinkedIn, Gmail) |
| **SQLite** | The database built into Python; FATMEE's memory file |
| **Session** | One conversation |
| **Task ID** | A unique number for one question/job |
| **Approval** | Your OK before anything public or sent |
| **`.docx`** | A Word document file |
| **Inputs** | Earlier steps' results that a think or document step reads |

---

## 6. Files at a glance

| File | Job |
|---|---|
| `main.py` | Front door: receives questions, sends answers |
| `graph.py` | Brain map: plan → run → check → fix → answer |
| `planner.py` | Makes the step plan |
| `plan_check.py` | Fixes and orders the plan |
| `executor.py` | Runs steps in waves, fills in `$s1` |
| `references.py` | Finds and fills in `$s1` references |
| `verifier.py` | Checks tools worked |
| `goal_verifier.py` | Checks the question is answered |
| `replanner.py` | Plan B when something's missing |
| `llm.py` | Talks to OpenAI / Gemini |
| `memory.py` | Conversation + diary (SQLite) |
| `mcp_client.py` | Connects to MCP servers |
| `mcp_servers.json` | List of MCP servers |
| `agents/research.py` | Research agent |
| `agents/thinker.py` | Think step |
| `agents/document.py` | Word documents |
| `tools/registry.py` | Catalog of all tools |
| `tools/system_tools.py` | Windows tools (RAM, disk, Wi-Fi…) |
| `tools/memory_tools.py` | Diary search tool |
| `config.py` | All settings |
| `tests/` | Automated tests (offline, free) |

For more detail on each file, see the **file guide** in the change log:
https://claude.ai/artifact/7rda8xaTM8AEm7YUvBKY7j

---

## 7. How to run and test

**Start FATMEE:**
```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn main:app --reload
```
Open http://127.0.0.1:8000/docs

**Ask a question:** in `POST /ask`, the box must contain exactly:
```json
{"question": "How much RAM do I have?"}
```

**Useful pages:**
- `GET /tools`: what FATMEE can do
- `GET /mcp`: is web search connected?
- `GET /sessions/default`: the conversation
- `GET /episodes?day=today`: today's diary

**Run the automated tests** (free, offline):
```powershell
.venv\Scripts\python.exe -m pytest backend\tests
```

---

## 8. What's next

Done: Steps 1–10.

| # | Step | What you'll get |
|---|---|---|
| 11 | Image agent | A picture sized for LinkedIn |
| 12 | Approval + chat screen | FATMEE waits for your Approve click |
| 13 | LinkedIn | Your full example works |
| 14 | Semantic memory | Remembers people ("my sir") and facts |
| 15 | Gmail | Draft → you approve → send |
| 16 | Voice | Talk to FATMEE, wake word |
| 17 | TARS personality | Humor / honesty settings |
| 18 | Desktop app | Tray icon, hotkey |
| 19+ | More | Jev trial, coding agent, GitHub |
