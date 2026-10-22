# FATMEE Guide

A simple guide to how FATMEE is built: every step we took, why, and how it works.
Read it top to bottom once. Later, jump to the step you need.

*Last updated: 14 Oct 2026, after Step 13.*

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
   │                 - a step that needs your OK PAUSES the task 🔒
   │                   (saved in SQLite; you approve → it continues)
   ├─ 4. Verify    verifier.py: did every tool work?
   ├─ 5. Goal?     goal_verifier.py: does this answer the question?
   ├─ 6. Fix       replanner.py tries other tools if something is missing
   └─ 7. Answer    writes the final answer (with sources for research)
   │
   ▼
memory.py          saves the conversation + a diary entry (after you get the answer)

Agents a step can use: research (web), think (reason), document (Word file), image (picture), post writer
Outside services (MCP): Tavily (web search), LinkedIn (FATMEE's own MCP server, official API)
Live progress: every step is reported at /tasks/{task_id}/events (for the future screen)
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
- **Credit guard:** **1 image per question**, counted in code. Even a recovery plan can't get around it.
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

**Third fix: one image per question, and more realistic.**
- **Only 1 image per question** (it was 2), to keep the cost low. Extra image steps are skipped, counted in code.
- **No automatic retry of a paid image call.** A retry after a timeout could pay twice for the same picture. Gemini is tried only if OpenAI fails.
- **More realistic photo style:** a candid documentary photo, full-frame camera, natural window light, film grain, everyday clutter. Screens are photographed at an angle with their content soft, not as a flat "UI mockup".
- **Heads are no longer cut off.** The model draws 1536×1024 and the banner is 1200×627, so about 20% must be cut. The prompt now asks to keep heads in the middle band, and the crop cuts more from the bottom (desks and floors) than the top (heads).

*Result:* a real hospital room with an IV pole and bed, a monitor of medical scans with one flagged by the AI, and a nurse checking colour-coded priorities on a tablet. Exactly **1 image call**.

**Fourth fix: the image model now gets the research too.** Before, the image model only received the short picture description, so it never knew what the research found (for example what the JEV model actually does). Now the prompt it receives has **three parts**:

```
1. CREATE THIS PICTURE: <description from the AI writer>          ← the scene
2. Fixed rules (code): shape, framing, realistic photo style,
   no text, screens show only shapes and colored bars              ← always the same
3. BACKGROUND CONTEXT (code, from the research):                    ← the facts
   The user asked: ...
   Topic: ...  Summary: ...  Key facts: - ... - ...
   (no links, no [1] numbers, at most 1,500 characters)
```

*Result:* "Research JEV model by TypeSafe and make a wide LinkedIn image". The research said JEV takes **messy input and returns typed, sorted decisions**. The picture shows messy items on the left of a screen being sorted into colour-coded categories on the right, with an engineer supervising. It's the real idea behind JEV, in a realistic office, with 1 image call. One flaw: the screen showed a misspelled title ("Ai decision Dachboard"). Now screens may only show shapes and coloured bars, and the writer never uses words like "dashboard" or "title".

**See exactly what was sent:** the log line `Full image prompt:` shows the whole prompt for every picture.

**How the picture prompt is made** (half AI, half code):
```
research results (no links) + your words
        → AI writer (1 text call): concept, scene, style, description   ← creative, changes every time
        → code adds fixed rules + the research context                  ← always the same
        → image model draws (1 image call) → code crops to the LinkedIn size
```

**Change the model or quality** in `backend/.env`:
```
FATMEE_OPENAI_IMAGE_MODEL=gpt-image-2      # better looking, costs more
FATMEE_OPENAI_IMAGE_QUALITY=high           # low / medium / high
FATMEE_IMAGE_DEFAULT_STYLE=photo           # photo / illustration / 3d / isometric / graphic, or empty = AI picks
FATMEE_MAX_IMAGES_PER_TASK=1
```

**Files:** `agents/image.py`, `image_gen.py`, `agents/files.py`, `tools/registry.py` (the `max_per_task` flag), `executor.py`, `graph.py` (answer keeps research)

---

### Step 12: Approval, FATMEE waits for your OK (14 Oct)

**What:** when a step needs your OK (a post, an email), FATMEE **stops, shows you exactly what it will do, and waits**. It continues only when you approve. You can also reject, or approve with your own edits. There's no screen yet: you use `/docs`. The proper screen comes in Step 18.

**Try it now with the practice tool** (nothing is published):
1. `POST /ask`: `{"question": "Write a short practice LinkedIn post about how much RAM my PC has"}`
2. The answer says `"status": "waiting_approval"` and shows the post text and a `task_id`.
3. `GET /approvals` lists everything that's waiting.
4. `POST /approvals/{task_id}` with one of:
   - `{"decision": "approve"}`
   - `{"decision": "reject"}`
   - `{"decision": "approve", "changes": {"text": "my own words"}}` to edit first
5. The task finishes. The practice post is saved in `Desktop\FATMEE Practice Posts\`.

**Shortcut, added 21 Oct: no task_id needed when only one task is waiting.**
- `POST /approvals` (no id in the address) with `{"decision": "approve"}` or `{"decision": "reject"}`.
- **1 task waiting:** FATMEE picks that one.
- **2 or more waiting:** it **doesn't guess**. You get a 409 that lists each `task_id` and its question. Then use `POST /approvals/{task_id}`, or reject the old ones first.
- **Nothing waiting:** 404 "Nothing is waiting for approval."
- In `/docs`, the example body now starts as `{"decision": "approve"}`. Before, it showed `"string"` values that always failed.

**Common /docs mistakes** (all seen in the 21 Oct log):
| Error | Cause | Fix |
|---|---|---|
| 409 on `/ask` | An old `task_id` was left in the box | Send only `{"question": "..."}` |
| 422 JSON decode error | A comma inside the quotes: `"...LinkedIn,"` | Put the comma after the closing quote |
| 422 on `/approvals` | The example `"step_id": "string"` left in the box (`"ok"` was refused too, until 22 Oct) | Delete the example lines. Since 22 Oct `ok`, `yes`, `no`, `cancel` also work |
| 400 on `/linkedin/callback` | The login page was reopened after it had worked | Nothing to fix; each login link works once |

**Fix, 21 Oct: a dry run is a finished job, not a failure.**
- *What went wrong:* you approved a post with dry run on. The post step ran correctly ("nothing was published"). But the AI goal checker read that as "goal not done", made a recovery plan, and tried to post **again**. The 1-post limit blocked it, which is good, but the answer blamed the limit, and 3 OpenAI calls were wasted.
- *21 Oct fix:* the code marked the goal as done whenever a result said `"dry_run": true`, without asking the AI.
- *Replaced on 22 Oct (Step 13.5)* by an agentic version that also covers rejected steps. The AI is told which steps are **settled**, and code makes sure they're never redone. See Step 13.5.

**Real test** (live):
```
ask      → plan: get_hardware_info → think (writes the post) → practice_post 🔒
         → PAUSED after 5 s, showing: "Quick PC check: this machine has 14.83 GB of RAM."
restart FATMEE  (to prove the pause survives)
approve  → continued in 3.4 s: only practice_post ran, nothing ran twice
         → saved "PC RAM LinkedIn Practice Post.txt" with exactly the approved text
```

**How it works:**
1. The executor reaches a step marked "needs approval" and **doesn't run it**. It also holds back every step that depends on it. Steps that don't depend on it still run.
2. The graph moves to a new step, **"wait for approval"**. LangGraph's built-in pause (`interrupt`) stops the task there.
3. **The task's state is saved in SQLite** (`backend/data/checkpoints.db`), so it survives a restart or a shutdown.
4. When you decide, the task **continues from the pause**. Steps that already ran are reused, never run twice (so no paid image is made twice).

**Safety rules (in code, not up to the AI):**
- **Approve:** the step runs, with your edits if you gave any.
- **Reject:** the step doesn't run, and **nothing built on it runs either** (e.g. "post, then email the link": the email is skipped too). A "no" is a decision, not an error, so FATMEE doesn't try another way.
- **Recovery plans never pause.** Only the first plan can ask for approval.
- **A double click can't run a step twice.** A second decision while one is running is refused.
- **The answer quotes exactly what was saved or posted**, never a rewritten version. The live test caught the AI "improving" the approved post in its answer, which is fixed now.

**Live progress (for the future screen and voice):** every task reports what it's doing:
```
planning → planned → wave → step → approval_needed → waiting_approval
approved → wave → step → checking → answering → done
```
- `GET /tasks/{task_id}/events` shows them, and `?after=N` shows only the newer ones.
- `GET /tasks/{task_id}/stream` follows them live.
- To follow a task while it runs, choose its id yourself: `{"question": "...", "task_id": "my-task-1"}`.
- Events are kept in memory for recent tasks only. The diary is the long-term record.

**Files:**
- `graph.py`: the wait-for-approval step, `resume()` and `waiting_task()`
- `executor.py`: `run_plan()` with pause, reuse, reject and edit
- `progress.py` (new)
- `tools/practice_tools.py` (new)
- `main.py`: `/approvals` and `/tasks`
- `verifier.py`, `config.py`

---

### Step 13: LinkedIn, through our own MCP server (14 Oct)

**What:** FATMEE can write a LinkedIn post, make an image for it, show you both, and, **only after you approve**, publish it on your personal profile and give you the link.

**Try** (once LinkedIn is set up):
- "Research AI agents in retail and post about it on LinkedIn" → research → (post + image at the same time) → 🔒 your OK → posted.
- "Write a LinkedIn post about Jev AI, no image" → FATMEE only **writes** it. It publishes only when you say "post", "publish" or "share".
- "Is FATMEE connected to LinkedIn?" / "What's my LinkedIn name?" → `linkedin_status` / `linkedin_get_profile`.

**Why our own MCP server:** I looked for a LinkedIn MCP server first. LinkedIn hasn't published one. The community ones either log in with your **cookies** (against LinkedIn's rules, and your account could be blocked) or use LinkedIn's **old `ugcPosts` API**, which the Posts API replaces. So FATMEE has its own small MCP server that uses only the **official API with OAuth 2.0**. Any MCP client could use it too.

**How it fits together:**
```
FATMEE (main.py)                         FATMEE's LinkedIn MCP server (local_mcp/linkedin_server.py)
 /linkedin/connect  → you log in once         linkedin_status        (no network)
 /linkedin/callback → token saved, encrypted  linkedin_get_profile   GET  /v2/userinfo
                                              linkedin_create_post 🔒 POST /rest/images (initializeUpload)
 planner → write_post + create_image                                  PUT  <upload link> (the PNG)
         → linkedin_create_post 🔒 (pauses)                           POST /rest/posts  → post link
            you approve → the MCP server publishes
```
- **The login is in FATMEE,** because it needs your browser to come back to `/linkedin/callback`. **Posting is in the MCP server,** which reads the saved login.
- **The login (token) is encrypted for your Windows account** (DPAPI) in `backend/data/linkedin_token.bin`. It's never in `.env`, never in git, never in logs.
- **The MCP server only gets the settings it needs** (`pass_env` in `mcp_servers.json`). Your OpenAI key, for example, never reaches it. *Until 22 Oct this wasn't fully true: the server process read `.env` itself. Fixed in Step 13.5.*

**Safety:**
- **Publishing always waits for your OK** (`"approval": ["create_post"]` in `mcp_servers.json`, set in code, so the AI can't skip it), and it's limited to **1 post per question**.
- **Dry run is on by default:** it checks the login, text and image, then publishes nothing. You switch it off yourself when you're ready.
- The text is checked before anything is sent: not empty, at most 3,000 characters, image must be PNG, JPG or GIF.
- LinkedIn's special characters, like `( ) [ ] * _ @`, are escaped by code, so a post is never cut off. `#hashtags` stay hashtags.
- LinkedIn errors come back in plain words, e.g. "login expired, connect again at /linkedin/connect" or "LinkedIn's daily limit was reached".

**What it can and can't do** (only what LinkedIn grants a normal self-serve app):

| ✅ Can | ❌ Can't (needs products LinkedIn must approve) |
|---|---|
| Log in with OAuth 2.0 | Post as a Company Page |
| Read your name, picture, email | Read your feed or other people's posts |
| Post text, or text + 1 image, on your profile | Post analytics, comments, messages |

**The post writer** (`write_post`, 1 AI call) writes in LinkedIn style:
- a strong first line (it shows before "see more")
- short paragraphs, plain text (LinkedIn shows no markdown), only facts from the research
- a question at the end, 3–5 hashtags, and up to 2 source links

The code removes `**bold**`, `# headings` and research numbers like `[1]`. The live test caught `[1][2]` left in the post, which is now fixed.

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

**Tested:** 26 offline tests, using a **fake LinkedIn server on your PC** and the **real MCP server process**. They check the login (including a forged login being refused), encrypted saving, the profile, dry run publishing nothing, a live post with an image (exact request body and headers), errors, escaping, approval before publishing, and that your other keys never reach the server. 1 live AI test: "Research Jev AI … write a LinkedIn post, no image" → research → write_post → a good post, not published (you said "write").

**Files:**
- `connectors/linkedin.py` (all official API calls + encrypted token)
- `local_mcp/linkedin_server.py` (the MCP server)
- `agents/writer.py` (the post writer)
- `mcp_servers.json`
- `mcp_client.py` (`pass_env`, local `python`, limits)
- `main.py` (`/linkedin/...`)
- `planner.py`, `tools/registry.py`

### Task logs: what FATMEE did, how long it took, what failed (22 Oct)

**What:** every task becomes **one JSON line** in `backend/logs/tasks.jsonl`. The line holds your question, the plan, every tool call (inputs, time, result or error), every check, every recovery attempt, every AI call (which model, how long, tokens), the final answer and the outcome. Then `analyze_logs.py` turns the whole file into a summary: how often FATMEE succeeds, what fails most, which tools break, how slow it is, and how many tokens it uses.

**Why:** to use FATMEE on real tasks and *see* its weak spots, instead of guessing. The same file can later be used to evaluate FATMEE, or to build a training dataset.

**This is not memory.** FATMEE never reads these logs back. The diary (`/episodes`) is still what FATMEE remembers.

**Try it:**
1. Ask FATMEE a few questions as usual.
2. Run:
   ```powershell
   cd backend
   ..\.venv\Scripts\python.exe analyze_logs.py
   ```
   Add `--json` for machine-readable output, or give another file path.
3. To look at one task, open `backend/logs/tasks.jsonl`. Each line is one task.

**How it fits in** (it only watches, it changes nothing):
```
graph.run() / resume()  → start or continue the task's record
  each graph stage      → timed: planning, execution, goal check, recovery, answer
  executor              → every tool call: inputs, time, ok, result or error
  llm.py / image_gen.py → every AI call: provider, model, time, tokens (if reported)
  checks + recovery     → verification, goal checks, each replan (why, what, outcome)
finish                  → work out the status, clean out secrets, append 1 line
```
- **Concurrent tasks never mix.** The record travels with the task in a *context variable*. LangGraph passes it into each stage, and the executor copies it into each tool thread.
- **Pause for approval:** the task stays **one record** across both requests (ask and approve), even across a restart. While it waits, it lives in `backend/logs/open/<task_id>.json`.
- **Crash:** the open file is rewritten after each stage. If FATMEE stops mid-task, the next startup writes it as `"incomplete"`, with the stage it was in.
- **Logging can't break FATMEE.** Every logging step is wrapped: a problem shows as `TASK LOG ERROR` in the console, and the task carries on.

**The status fields** (all set by code, from real data):

| Field | Meaning |
|---|---|
| `execution_success` | `true` if every tool call worked; `false` if any failed; `null` if no tools ran |
| `goal_satisfied` | The **last** goal check's verdict; `null` if no goal check ran |
| `task_status` | `success`, `partial`, `failed` or `incomplete` (rules below) |
| `failure_category` | The first failure that happened; `failure_categories` lists them all |

`task_status` rules:
- **success:** answered; the goal check passed (or, with no goal check, every tool call worked or no tool was needed); nothing reported missing.
- **partial:** answered, but the goal check failed, something is missing, or some tool calls failed, while at least one tool call worked.
- **failed:** an error stopped the task, or tools were needed and none worked while the goal wasn't met.
- **incomplete:** never finished, because FATMEE stopped while it ran.

A task can be `success` while `execution_success` is `false`. Example: "what's in C:\no\such\folder?" The tool fails, but "that folder doesn't exist" is the right answer, and the goal check agrees. The failure is still listed. A 200 reply alone never means success.

**Failure categories:** `planning_error` (planner's answer unusable), `invalid_plan` (e.g. steps waiting on each other in a loop), `unknown_tool`, `tool_execution_error`, `step_skipped` (never ran: a per-question limit, or a missing input), `execution_verification_failed`, `goal_verification_failed`, `replanning_error`, `max_recovery_attempts` (goal still not met after a recovery that ran tools), `llm_error` (no AI reachable), `timeout`, `unknown_error`. Each failure says its stage and gives the evidence (tool, step, errors, missing information). A step **you rejected** is a decision, not a failure.

**Times** (`timings`, in seconds): `planning_s`, `execution_s`, `verification_s` (goal checks), `recovery_s` (includes `replanning_s`, the replanner's own calls), `answer_s` and `active_s` (FATMEE working) use a monotonic clock. `wall_s` (start to finish, including any wait for your approval) and `approval_wait_s` use the PC's clock, because they can span a restart.

**Tokens and cost: only real numbers.**
- Tokens come from the provider's own response (OpenAI reports them). If a provider doesn't report them, the value is `null`, never 0 or a guess.
- Cost is `null` unless **you** enter prices in `.env` (USD per 1 million tokens, from your provider's price page):
  ```
  FATMEE_LLM_PRICES={"gpt-5.4-mini": {"input": 0.25, "output": 2.0}}
  ```
  The example numbers are placeholders; check your real prices. A task's total cost is only given if every one of its AI calls is priced.
- Image calls are counted and timed. Their cost isn't estimated, because the image API reports no tokens.

**Safety:**
- Removed before writing: values of fields named like `api_key`, `token`, `secret`, `password`, `authorization` or `cookie`; `Bearer …`; OpenAI, Google and Tavily keys; `?code=` / `?token=` in links; and the actual values of your secret `.env` settings wherever they appear.
- Long values are cut at 2,000 characters (the answer at 8,000) and marked `… [truncated: N chars]`. Lists stop at 50 items, and the record gets `"truncated": true`.
- Earlier results passed to a tool appear only as `<results of s1>`.
- No hidden reasoning is logged, only plans, actions, results, checks and short reasons.
- `backend/logs/` is ignored by git. It does hold your questions and answers, so don't share it without looking first.

**Settings** (`.env`): `FATMEE_TASK_LOG=false` switches it off; `FATMEE_TASK_LOG_DIR`, `FATMEE_TASK_LOG_MAX_CHARS`, `FATMEE_TASK_LOG_RESPONSE_CHARS`, `FATMEE_LLM_PRICES`.

**Limits:**
- One FATMEE process writes the file. Several uvicorn workers would need a file lock (not added).
- Retries inside the OpenAI SDK aren't visible. Only provider fallbacks (`llm_failed_attempts`) are.
- The file grows forever. Move or delete it when it gets big.

**Tested:** 16 new offline tests, 212 in total. They cover: a successful task, a tool failure, a goal failure then replanning, two recovery attempts, a planner outage, invalid plan JSON, the fallback provider with real token counts and prices, approval pause and resume as one record, 8 tasks at the same time with separate records, secret removal and cutting, logging failing without breaking the task, a crash leaving an incomplete record, switching logging off, and the analysis script with broken lines and empty or missing files.

**Files:**
- `task_logger.py` (new)
- `analyze_logs.py` (new)
- `tests/test_task_log.py` (new)
- `graph.py`, `executor.py`, `llm.py`, `image_gen.py`, `main.py`, `config.py`, `tests/conftest.py`, `.gitignore`

### Step 13.5: Hardening, fixes from the full review (22 Oct)

**What:** a full review of the backend found 14 bugs (see the "FATMEE Status Check" page). This step fixes the ones we could fix with confidence, before Gmail reuses the same code.

**The rule we followed:** **judgments stay with the AI, code only guards.**
- *AI decides:* is the goal met, should we try another way, what to say. The AI gets **better facts** to decide with, not hard-coded answers.
- *Code guarantees:* never redo what you rejected, never lose a result, never publish text you didn't approve, never post twice.

**What changed:**

| # | Problem | Fix |
|---|---|---|
| 1 | Two "think" steps with the same instruction over **different** results were merged; one never ran | Steps that read earlier results count as different when they wait for different steps |
| 2 | After you **rejected** a step, the AI goal check could call it "missing", so FATMEE replanned, hit the limit, and the answer blamed the limit | **Agentic:** the goal checker, replanner and answer writer are told which steps are **SETTLED** (you rejected them, or they ran in dry run) and that this is your decision. **Guard:** recovery never runs a tool you rejected for that question. This replaces the 21 Oct dry-run shortcut, which skipped the AI |
| 3 | A recovery step replaced an earlier result of the same tool | It's kept next to it (`research_topic#2`) |
| 4 | Recovery steps couldn't use the first plan's results (`$s1` stayed as text) | Recovery sees the first plan's results by step id; the replanner is told it can reuse them |
| 5 | An edit with a misspelled field (`post_text`) was ignored and the original text was posted | Refused with "You can change: text, …"; nothing runs |
| 6 | A slow post could time out after 30 s with an empty error, though it may have gone out | LinkedIn gets 120 s (`"timeout"` in `mcp_servers.json`); a timeout says "it may still have finished, check before trying again" |
| 7 | A LinkedIn server error (500/503) made FATMEE post again, maybe twice | No blind retry: "the post may or may not have been published, check your profile". Only a 409 conflict (nothing created) is retried |
| 8 | If the AI went down **after** a post, you only saw "could not reach any AI model" | The goal check and replanner are skipped, and the answer lists the plain results (e.g. the post link) |
| 9 | The LinkedIn server process read `.env` itself and got every key | Local servers start with `FATMEE_NO_DOTENV=1` and get only their `pass_env` settings. Tested with real processes: 4 secret settings before, 0 now |
| 10 | Two identical approval steps were both shown, with confusing results | Shown once; one decision covers both |
| 11 | A task id you chose could reuse an old diary entry after a restart | Refused (409) when the diary already has it |
| 13 | Task log counted a failure twice after approve + restart | Fixed |
| 14 | Task log said "success" with a skipped step and no goal check | Now "partial" |
| - | `ok` / `yes` / `no` / `cancel` were refused as decisions | Accepted |
| - | `.gitignore` ignored every `*.jsonl` file | Only `backend/logs/` |

**Not fixed, on purpose:**
- **#12, waiting tasks never expire.** How long a post may wait is your choice, not a bug fix. Reject old ones yourself for now.
- **The diary and the task log still use different status words** ("answered" vs "success").
- **Each LinkedIn call starts a fresh server process** (about 1.5 s). A long-lived connection is a bigger change.
- **Task log file writes** add about 15–35 ms per task.

**Tested:**
- 23 new offline tests, 235 in total, all passing. Three fixes were switched off on purpose, and their tests failed, so the tests really catch the bugs.
- **2 live runs, about 4 OpenAI calls each,** with the practice post, so nothing was posted:
  - "Write a short practice LinkedIn post about how much RAM my PC has" → paused → **rejected** → the real AI goal check said the goal was met because the post step was the user's decision.
  - There was **no recovery** and nothing was saved.
  - The answer ended: *"The practice post step was not done because you rejected it."*
  - The first run showed the answer didn't say this yet, so the settled steps are now also given to the answer writer.

**Files:**
- `executor.py`, `plan_check.py`, `graph.py`
- `goal_verifier.py`, `replanner.py`
- `main.py`, `memory.py`, `tools/registry.py`
- `mcp_client.py`, `mcp_servers.json`, `config.py`, `connectors/linkedin.py`
- `task_logger.py`, `.gitignore`
- `tests/test_hardening.py` (new), `tests/test_linkedin.py`

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
| **Approval** | Your OK before anything public or sent. FATMEE pauses until you decide |
| **Pause / resume** | A task stops at a step that needs your OK (saved in SQLite) and continues after your decision |
| **Checkpoint** | The saved state of a task, so it can continue later, even after a restart |
| **Progress events** | Live reports of what a task is doing ("wave 2: create_image") |
| **OAuth 2.0** | The safe way to log in to a service: you log in on LinkedIn itself, and FATMEE gets a token, never your password |
| **Token** | A key that lets FATMEE act for you on LinkedIn for about 60 days. Saved encrypted |
| **Dry run** | Check everything, publish nothing |
| **Local MCP server** | An MCP server that FATMEE starts itself on your PC (like our LinkedIn one) |
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
| `progress.py` | Live progress events of each task |
| `task_logger.py` | Task logs: one JSON line per finished task (`backend/logs/tasks.jsonl`) |
| `analyze_logs.py` | Summary of the task logs: success rate, failures, speed, tokens |
| `connectors/linkedin.py` | Official LinkedIn API calls, login, encrypted token |
| `local_mcp/linkedin_server.py` | FATMEE's LinkedIn MCP server |
| `agents/writer.py` | LinkedIn post writer |
| `tools/practice_tools.py` | Practice post: tests approval, publishes nothing |
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
- `GET /approvals`: tasks waiting for your OK; `POST /approvals/{task_id}` to decide
- `GET /tasks/{task_id}/events`: what a task did, step by step
- `GET /linkedin/status`: connected to LinkedIn? dry run on? Open `/linkedin/connect` in a browser to log in

**See how FATMEE is doing** (from the task logs; no AI calls):
```powershell
cd backend
..\.venv\Scripts\python.exe analyze_logs.py
```

**Run the automated tests** (free, offline):
```powershell
.venv\Scripts\python.exe -m pytest backend\tests
```

---

## 9. What's next

Done: Steps 1–13.

We build the whole brain first and **the screen once, at the end** (Step 18), so it's designed around every feature, including live voice. Until then, everything is tested in `/docs`.

| # | Step | What you'll get |
|---|---|---|
| 14 | Semantic memory | Remembers people ("my sir") and facts (vector search, sqlite-vec) |
| 15 | Gmail | Draft → 🔒 you approve → send |
| 16 | Voice (brain side) | Speech → text, text → speech |
| 17 | TARS personality | Humor / honesty settings |
| 18 | **The frontend** | Chat, live talking, live progress, result cards, Approve buttons, desktop app |
| 19+ | More | Jev trial, coding agent, GitHub |
