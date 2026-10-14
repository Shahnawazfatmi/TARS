# FATMEE Guide

A simple guide to how FATMEE is built: every step we took, why, and how it works.
Read it top to bottom once. Later, jump to the step you need.

*Last updated: 14 Oct 2026, after Step 11.*

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

Agents a step can use: research (web), think (reason), document (Word file), image (picture)
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

### Step 11: Image agent, pictures for LinkedIn (14 Oct)

**What:** FATMEE makes a picture, sized exactly for LinkedIn, and saves it as a PNG.

**Try:**
- "Make a square LinkedIn image about AI agents helping small shops."
- "Make an image of a robot reading a newspaper, watercolor style, wide."
- "Research AI in retail and make a LinkedIn image about it." (research → image)

**Real example** (tested live, 25.6 seconds, 5 AI calls, 1 of them an image call):
```
s1  create_image  shape: square                     Wave 1   (17.7 s)
      1. writes a picture description   (1 small text call)
      2. makes the picture              (1 image call, gpt-image-1-mini)
      3. code crops it to 1200×1200 and saves
      → Desktop\FATMEE Images\AI Agents for Retail.png
```

**How it works:**
1. **The picture description.** If you describe the picture clearly, it's used as it is, so no extra call is spent. If the picture must come from earlier results (like research), or you only gave a short idea, one small text call writes a good description first.
2. **Fixed rules are added in code**, so they always apply. The main one: **no text in the picture** unless you ask for it, because image AIs often misspell words. If you want words, say so: "with the title 'AI in Retail'".
3. **The image call** goes through `image_gen.py`. It works like `llm.py` does for text: **OpenAI first** (`gpt-image-1-mini`, the cheapest), **Gemini as backup**.
4. **Exact LinkedIn size.** The code crops from the centre and resizes, using Pillow:

| Shape | Size | Best for |
|---|---|---|
| `square` (default) | 1200 × 1200 | A normal post |
| `landscape` | 1200 × 627 | A link or article post |
| `portrait` | 1080 × 1350 | A tall post that takes more space in the feed |

You can also say "wide", "banner", "tall", "16:9" or "4:5". The code understands those words.

**Rules:**
- **Credit guard:** at most **2 images per question**, counted in code. Even a recovery plan can't get around it.
- Saved in `Desktop\FATMEE Images\` with the same safe-saving rules as Word files (only your folders, never overwrite). Those rules now live in one shared file, `agents/files.py`.
- No approval needed for making the picture. Approval comes when it's **posted** (Step 13).
- The result includes the file path, so a later step can use it, e.g. a LinkedIn post attaching `$s2.path`.

**Styles:** say "photo", "illustration", "3d", "isometric" or "graphic", or your own word, like "watercolor". If you don't say one, FATMEE picks the style that suits the topic.

**Fix after your tests: the pictures looked generic.** Your first pictures all looked the same: people in a meeting, glowing brains, floating charts, all in teal. The research said something specific ("AI virtual scientists in drug discovery"), but the picture showed "AI in general".

*Why:* the picture-description instructions said "professional, suitable for LinkedIn", so the AI played it safe with stock-photo clichés. Nothing told it to pick one specific idea.

*What changed:*
1. The description writer now works in **3 steps**:
   - **concept:** the single most concrete finding
   - **scene:** who, what and where, with real objects from that field
   - **style:** photo, illustration, 3d, isometric or graphic
2. **Clichés are banned:** glowing brains, circuit lines, floating holograms, robot handshakes, meeting rooms, all-blue "tech" looks.
3. **Bright and clear:** the picture must read well as a small thumbnail in a fast feed.
4. Links and file paths are no longer sent to the description writer (they don't help it draw), which also saves tokens.
5. **The log shows the idea:** `Image idea: <concept> | style: photo | <description>`. If a picture looks wrong, you can see why.
6. **The answer keeps the research:** when research is done, the answer always lists its key findings with [1], [2] and the Sources, even when a file was also made.

*Result of the same question after the fix:* "two AI agents discovering a hidden biomedical link", shown as a real lab photo with a microscope, petri dish and notes. It was specific, not generic, but too dark, so a "bright and clear" rule was added afterwards.

**Second fix: the AI was invisible.** The next test ("AI in healthcare, photo style") gave a nice, bright hospital photo, but you couldn't see any AI: a nurse with papers and a blurry screen. The cliché ban had gone too far and removed every way of showing AI.

*What changed:*
1. **When the topic is AI, the AI is the hero:** clearly visible, in focus and near the centre. It's shown the way AI really looks today: a screen where an assistant is visibly working, a robot or robotic arm, a smart device.
2. **AI agents are shown doing a task by themselves** while people supervise.
3. **The ban is narrower:** holograms floating in empty air are still banned, but real screens showing AI at work are fine.
4. **Your own words reach the writer.** The executor passes your question in as `user_request`, so words like "agentic" aren't lost in the planner's summary.
5. **Realistic photos are the default** (`FATMEE_IMAGE_DEFAULT_STYLE=photo`): real people, real places, real devices, nothing science-fiction. If your own description already names a look ("a cartoon nurse", "watercolor"), the default isn't added on top of it.

*Result:* "Research agentic AI in healthcare this week and make a wide LinkedIn image" gave the idea **"an AI agent clears prior-authorization requests by itself while a clinician reviews only the exceptions"**. The picture shows a realistic hospital office: a big monitor where an AI agent avatar sorts a queue of patient cases, flagging them green or red, and a nurse checking the exceptions on a tablet.

**How the picture prompt is made** (half AI, half code):
```
research results (no links) + your words
        → AI writer (1 text call): concept, scene, style, description   ← creative, changes every time
        → code adds fixed rules: shape, style line, "no text"           ← always the same
        → image model draws → code crops to the LinkedIn size
```

**Change the model or quality** in `backend/.env`:
```
FATMEE_OPENAI_IMAGE_MODEL=gpt-image-2      # better looking, costs more
FATMEE_OPENAI_IMAGE_QUALITY=high           # low / medium / high
FATMEE_IMAGE_DEFAULT_STYLE=photo           # photo / illustration / 3d / isometric / graphic, or empty = AI picks
FATMEE_MAX_IMAGES_PER_TASK=2
```

**Files:** `agents/image.py`, `image_gen.py`, `agents/files.py`, `tools/registry.py` (the `max_per_task` flag), `executor.py`, `graph.py` (answer keeps research)

---

## 5. How FATMEE grows: adding things later

FATMEE is built from **separate pieces that plug together**. Adding something new means adding one piece, without rewriting the others.

```
          ┌──────────── doors ────────────┐
          │ web API now · voice · chat UI │   all call the same run()
          └───────────────┬───────────────┘
                          ▼
              brain: graph.py (plan → run → check → answer)
                          │ only knows "tools"
                          ▼
             tool list: tools/registry.py
     ┌─────────┬──────────┼──────────┬──────────────┐
     ▼         ▼          ▼          ▼              ▼
  system    agents/     MCP tools   memory       (future tools)
  tools     research,   (Tavily,    (diary,
            think, doc, LinkedIn,   later facts /
            image       Gmail...)   vector DB)
                          │
       shared services: llm.py (text) · image_gen.py (images)
                        agents/files.py (safe saving) · config.py
```

| To add… | What you do | What doesn't change |
|---|---|---|
| **A new built-in tool** | Write the function, then add **one `Tool(...)` entry** in `tools/registry.py`. The planner sees it automatically. | The planner, executor, graph |
| **A tool that needs your OK** | Set `needs_approval=True` on its entry | The AI can't switch it off |
| **A tool that reads earlier results** | Set `takes_inputs=True` | The executor passes them in |
| **A paid or limited tool** | Set `max_per_task=N` | Counted in code |
| **A new agent** | Write `agents/<name>.py`, give it a `configure()` if it needs the AI, add it in `agents/__init__.py` and in the tool list | Other agents |
| **An outside service (MCP)** | Add an entry in `mcp_servers.json` and its key in `.env`. **No code.** `expose` picks the tools; `approval` marks the ones that need your OK | Everything |
| **A new AI model company** | Text: one class in `llm.py`. Images: one class in `image_gen.py`. Pick the order in `.env` | The agents |
| **An agent that saves files** | Use `agents/files.py` for the folder, name and no-overwrite rules | The safety rules |
| **A vector database** (for "remember people and facts", Step 14) | One more store class in `memory.py` (next to the conversation and the diary), plus one tool such as `recall_facts`. Options: `sqlite-vec` (stays in the same file) or Chroma / Qdrant if it grows | The planner, other memories |
| **Voice or a chat screen** | A new "door" that calls the same `run()` | The brain and the tools |

**Rule of thumb:** each piece only talks to the pieces next to it. The brain knows "tools", not Tavily or OpenAI. The agents know `llm` and `image_gen`, not which company is behind them. That's why we can swap or add parts safely.

---

## 6. Words you'll see

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
| **Inputs** | Earlier steps' results that a think, document or image step reads |
| **PNG** | A picture file |
| **Provider** | The AI company behind a call (OpenAI, Gemini). FATMEE tries them in order |
| **Vector database** | Memory that finds things by meaning, not exact words ("my boss" ≈ "my sir"). Planned for Step 14 |

---

## 7. Files at a glance

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
| `agents/image.py` | Pictures for LinkedIn |
| `agents/files.py` | Safe saving rules for every file an agent makes |
| `image_gen.py` | Talks to the OpenAI / Gemini image models |
| `tools/registry.py` | Catalog of all tools |
| `tools/system_tools.py` | Windows tools (RAM, disk, Wi-Fi…) |
| `tools/memory_tools.py` | Diary search tool |
| `config.py` | All settings |
| `tests/` | Automated tests (offline, free) |

For more detail on each file, see the **file guide** in the change log:
https://claude.ai/artifact/7rda8xaTM8AEm7YUvBKY7j

---

## 8. How to run and test

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

## 9. What's next

Done: Steps 1–11.

| # | Step | What you'll get |
|---|---|---|
| 12 | Approval + chat screen | FATMEE waits for your Approve click |
| 13 | LinkedIn | Your full example works |
| 14 | Semantic memory | Remembers people ("my sir") and facts |
| 15 | Gmail | Draft → you approve → send |
| 16 | Voice | Talk to FATMEE, wake word |
| 17 | TARS personality | Humor / honesty settings |
| 18 | Desktop app | Tray icon, hotkey |
| 19+ | More | Jev trial, coding agent, GitHub |
