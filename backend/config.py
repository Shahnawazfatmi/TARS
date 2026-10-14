import os

from dotenv import load_dotenv


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# MODELS
# ============================================================

# Providers are tried in this order; the next one is used when
# a call fails. Default: OpenAI first, Gemini as fallback.
LLM_PROVIDERS = [
    name.strip().lower()
    for name in os.getenv("FATMEE_LLM_PROVIDERS", "openai,gemini").split(",")
    if name.strip()
]

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
IMAGE_PROVIDERS = [
    name.strip().lower()
    for name in os.getenv("FATMEE_IMAGE_PROVIDERS", "openai,gemini").split(",")
    if name.strip()
]

# gpt-image-1-mini is the cheapest; gpt-image-2 looks better but costs more.
OPENAI_IMAGE_MODEL = os.getenv("FATMEE_OPENAI_IMAGE_MODEL", "gpt-image-1-mini")

# low / medium / high: higher looks better and costs more.
OPENAI_IMAGE_QUALITY = os.getenv("FATMEE_OPENAI_IMAGE_QUALITY", "medium")

GEMINI_IMAGE_MODEL = os.getenv("FATMEE_GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")

# Seconds to wait for one image (images are slower than text).
IMAGE_TIMEOUT = float(os.getenv("FATMEE_IMAGE_TIMEOUT", "120"))

# Where images are saved. Empty = Desktop\FATMEE Images.
IMAGES_DIR = os.getenv("FATMEE_IMAGES_DIR", "")

# Look used when the user names none: photo (realistic), illustration,
# 3d, isometric or graphic. Empty = the AI picks per topic.
IMAGE_DEFAULT_STYLE = os.getenv("FATMEE_IMAGE_DEFAULT_STYLE", "photo")

# Credit guard: at most this many images per question, even if the
# plan asks for more.
MAX_IMAGES_PER_TASK = int(os.getenv("FATMEE_MAX_IMAGES_PER_TASK", "2"))

# Characters of earlier results the image agent may read.
IMAGE_INPUT_CHARS = int(os.getenv("FATMEE_IMAGE_INPUT_CHARS", "8000"))
