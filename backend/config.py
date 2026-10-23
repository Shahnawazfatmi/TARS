import json
import os

from dotenv import load_dotenv


# ============================================================
# ENVIRONMENT
# ============================================================

# FATMEE's own local MCP servers (e.g. LinkedIn) get only the settings
# listed in their "pass_env", so they must not read .env themselves.
if os.getenv("FATMEE_NO_DOTENV") != "1":
    load_dotenv()


# ============================================================
# MODELS
# ============================================================

# Providers are tried in this order; the next one is used when
# a call fails. Default: the local model first, Gemini as fallback.
# OpenAI still works when added here (e.g. "local,openai,gemini").
LLM_PROVIDERS = [
    name.strip().lower()
    for name in os.getenv("FATMEE_LLM_PROVIDERS", "local,gemini").split(",")
    if name.strip()
]

# Local model on our own server (llama.cpp, vLLM, Ollama: any server
# with an OpenAI-style /v1/chat/completions). Empty = skipped.
LOCAL_LLM_URL = os.getenv("FATMEE_LOCAL_LLM_URL", "").strip()

# The model name the server reports at <url>/models.
LOCAL_LLM_MODEL = os.getenv("FATMEE_LOCAL_LLM_MODEL", "local")

# Most local servers need no key.
LOCAL_LLM_API_KEY = os.getenv("FATMEE_LOCAL_LLM_API_KEY", "not-needed")

# Qwen-style models "think" before answering, which is slow and can
# use up the whole answer. Off by default.
LOCAL_LLM_THINKING = os.getenv("FATMEE_LOCAL_LLM_THINKING", "false").lower() == "true"

# Local models write slower than cloud ones, so they get more time.
LOCAL_LLM_TIMEOUT = float(os.getenv("FATMEE_LOCAL_LLM_TIMEOUT", "180"))

# OPEN_AI_API_KEY is also accepted because that is the name
# used in the existing .env.
OPENAI_API_KEY = (
    os.getenv("OPENAI_API_KEY")
    or os.getenv("OPEN_AI_API_KEY")
)

OPENAI_MODEL = os.getenv("FATMEE_OPENAI_MODEL", "gpt-5.4-mini")

# GOOGLE_API_KEY is still accepted so older setups keep working.
GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
)

# FATMEE_MODEL was the old name for the Gemini model setting.
GEMINI_MODEL = (
    os.getenv("FATMEE_GEMINI_MODEL")
    or os.getenv("FATMEE_MODEL")
    or "gemini-3.6-flash"
)


# ============================================================
# SETTINGS
# ============================================================

# Seconds to wait for one model call before giving up.
LLM_TIMEOUT = float(os.getenv("FATMEE_LLM_TIMEOUT", "60"))

# Set to "false" to skip the LLM goal check and save one call
# per tool request.
GOAL_CHECK = os.getenv("FATMEE_GOAL_CHECK", "true").lower() != "false"

# Parallel workers used to run tools.
TOOL_WORKERS = int(os.getenv("FATMEE_TOOL_WORKERS", "8"))

LOG_LEVEL = os.getenv("FATMEE_LOG_LEVEL", "INFO").upper()


# ============================================================
# MEMORY
# ============================================================

# SQLite file holding conversations (ignored by git).
DB_PATH = os.getenv(
    "FATMEE_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fatmee.db"),
)

# Task logs (observability, not memory): one JSON line per finished
# task in logs/tasks.jsonl. Ignored by git.
TASK_LOG = os.getenv("FATMEE_TASK_LOG", "true").lower() != "false"

TASK_LOG_DIR = os.getenv(
    "FATMEE_TASK_LOG_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"),
)

# Longest text kept per field (tool results, errors); the final answer
# gets its own, larger limit. Longer values are cut and marked.
TASK_LOG_MAX_CHARS = int(os.getenv("FATMEE_TASK_LOG_MAX_CHARS", "2000"))
TASK_LOG_RESPONSE_CHARS = int(os.getenv("FATMEE_TASK_LOG_RESPONSE_CHARS", "8000"))


def _prices() -> dict:
    """
    Optional prices for cost estimates, in USD per 1M tokens, e.g.
    FATMEE_LLM_PRICES={"gpt-5.4-mini": {"input": 0.25, "output": 2.0}}.
    Not set (the default): costs are logged as null, never guessed.
    """

    try:
        prices = json.loads(os.getenv("FATMEE_LLM_PRICES", "") or "{}")
    except ValueError:
        return {}

    return prices if isinstance(prices, dict) else {}


LLM_PRICES = _prices()

# Saved state of running and paused tasks (LangGraph checkpoints), so
# a task waiting for approval survives a restart. Ignored by git.
CHECKPOINT_DB_PATH = os.getenv(
    "FATMEE_CHECKPOINT_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "checkpoints.db"),
)

# How many recent messages (questions + answers) FATMEE reads
# before answering. 10 = the last 5 exchanges.
HISTORY_MESSAGES = int(os.getenv("FATMEE_HISTORY_MESSAGES", "10"))

# Long earlier answers are shortened to this many characters
# when shown to the model, to keep prompts small.
HISTORY_CHARS = int(os.getenv("FATMEE_HISTORY_CHARS", "1500"))


# ============================================================
# MCP (external tools: search, later LinkedIn, Gmail, GitHub)
# ============================================================

MCP_CONFIG = os.getenv(
    "FATMEE_MCP_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_servers.json"),
)

# Seconds to wait when connecting to an MCP server or calling a tool.
MCP_TIMEOUT = float(os.getenv("FATMEE_MCP_TIMEOUT", "30"))


# ============================================================
# RESEARCH AGENT
# ============================================================

# Web results per research topic (more = better coverage, slower).
RESEARCH_SOURCES = int(os.getenv("FATMEE_RESEARCH_SOURCES", "5"))

# Characters of search results given to the model per topic.
RESEARCH_INPUT_CHARS = int(os.getenv("FATMEE_RESEARCH_INPUT_CHARS", "12000"))

# Characters of earlier results a "think" step may read.
THINK_INPUT_CHARS = int(os.getenv("FATMEE_THINK_INPUT_CHARS", "20000"))


# ============================================================
# DOCUMENT AGENT
# ============================================================

# Where Word documents are saved. Empty = Desktop\FATMEE Reports.
DOCUMENTS_DIR = os.getenv("FATMEE_DOCUMENTS_DIR", "")

# Characters of earlier results the document agent may read.
DOCUMENT_INPUT_CHARS = int(os.getenv("FATMEE_DOCUMENT_INPUT_CHARS", "30000"))


# ============================================================
# IMAGE AGENT
# ============================================================

# Image providers are tried in this order (same keys as the text models).
# The local model cannot make pictures, so Gemini is the default.
# OpenAI still works when added here (e.g. "gemini,openai").
IMAGE_PROVIDERS = [
    name.strip().lower()
    for name in os.getenv("FATMEE_IMAGE_PROVIDERS", "gemini").split(",")
    if name.strip()
]

# gpt-image-1-mini is the cheapest; gpt-image-2 looks better but costs more.
OPENAI_IMAGE_MODEL = os.getenv("FATMEE_OPENAI_IMAGE_MODEL", "gpt-image-1-mini")

# low / medium / high: higher looks better and costs more.
OPENAI_IMAGE_QUALITY = os.getenv("FATMEE_OPENAI_IMAGE_QUALITY", "medium")

GEMINI_IMAGE_MODEL = os.getenv("FATMEE_GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")

# Seconds to wait for one image (images are slower than text).
IMAGE_TIMEOUT = float(os.getenv("FATMEE_IMAGE_TIMEOUT", "120"))

# Where practice posts are saved (approval testing). Empty = Desktop\FATMEE Practice Posts.
PRACTICE_DIR = os.getenv("FATMEE_PRACTICE_DIR", "")

# Where images are saved. Empty = Desktop\FATMEE Images.
IMAGES_DIR = os.getenv("FATMEE_IMAGES_DIR", "")

# Look used when the user names none: photo (realistic), illustration,
# 3d, isometric or graphic. Empty = the AI picks per topic.
IMAGE_DEFAULT_STYLE = os.getenv("FATMEE_IMAGE_DEFAULT_STYLE", "photo")

# Credit guard: at most this many images per question, even if the
# plan asks for more.
MAX_IMAGES_PER_TASK = int(os.getenv("FATMEE_MAX_IMAGES_PER_TASK", "1"))

# Characters of earlier results the image agent may read.
IMAGE_INPUT_CHARS = int(os.getenv("FATMEE_IMAGE_INPUT_CHARS", "8000"))

# Characters of research context sent to the image model itself, next
# to the picture description, so the scene matches the real subject.
IMAGE_CONTEXT_CHARS = int(os.getenv("FATMEE_IMAGE_CONTEXT_CHARS", "1500"))


# ============================================================
# FILE READER (read_file, open_file)
# ============================================================

# Biggest file FATMEE will read, in MB.
READ_MAX_FILE_MB = float(os.getenv("FATMEE_READ_MAX_FILE_MB", "200"))

# Characters of text per AI call. Long files are read in parts of this
# size and the notes combined (32,000 characters is about 8,000 tokens).
READ_CHUNK_CHARS = int(os.getenv("FATMEE_READ_CHUNK_CHARS", "32000"))

# Most parts read for one question. A longer file is read in part:
# the parts that match the question, or a spread across the file.
READ_MAX_PARTS = int(os.getenv("FATMEE_READ_MAX_PARTS", "8"))

# Parts read at the same time (more helps only if the model server
# serves several requests at once).
READ_WORKERS = int(os.getenv("FATMEE_READ_WORKERS", "2"))

# Extracted text and whole-file summaries are kept here, so a file is
# only read once until it changes. Ignored by git.
READ_CACHE_DIR = os.getenv(
    "FATMEE_READ_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "read_cache"),
)
