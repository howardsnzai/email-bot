"""Environment-driven configuration for Jason."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


# --- LLM ---
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "qwen/qwen3.6-flash")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# --- Gmail ---
GMAIL_CREDENTIALS_FILE = os.environ.get("GMAIL_CREDENTIALS_FILE", "credentials.json")
GMAIL_TOKEN_FILE = os.environ.get("GMAIL_TOKEN_FILE", "token.json")
OPERATOR_EMAIL = os.environ.get("OPERATOR_EMAIL", "jasonfromhowards1@gmail.com").lower()
POLL_INTERVAL_SECONDS = _int("POLL_INTERVAL_SECONDS", 30)
GMAIL_SEARCH_QUERY = os.environ.get("GMAIL_SEARCH_QUERY", "in:inbox is:unread")
GMAIL_IGNORED_SENDER_PATTERNS = [
    p.strip().lower()
    for p in os.environ.get(
        "GMAIL_IGNORED_SENDER_PATTERNS",
        "no-reply,noreply,donotreply,do-not-reply",
    ).split(",")
    if p.strip()
]

# --- Stripe ---
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
VIDEO_PRICE_CENTS = _int("VIDEO_PRICE_CENTS", 19900)
VIDEO_CURRENCY = os.environ.get("VIDEO_CURRENCY", "nzd")
VIDEO_PRODUCT_NAME = os.environ.get("VIDEO_PRODUCT_NAME", "Howards AI Marketing Video")

# --- Google search APIs ---
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
GOOGLE_CSE_API_KEY = os.environ.get("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")

# --- Render pipeline ---
RENDER_URL = os.environ.get("RENDER_URL", "")

# --- Webhook server ---
WEBHOOK_HOST = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = _int("WEBHOOK_PORT", 8000)

# --- Storage ---
SUPABASE_SCHEMA = os.environ.get("SUPABASE_SCHEMA", "jason_memory")
SUPABASE_CUSTOMERS_TABLE = os.environ.get("SUPABASE_CUSTOMERS_TABLE", "customers")
MEMORY_DIR = Path(os.environ.get("MEMORY_DIR", "./memory")).resolve()

# --- Behaviour ---
GHOST_DAYS = _int("GHOST_DAYS", 7)
MIN_PHOTOS = _int("MIN_PHOTOS", 12)
MIN_PHOTO_WIDTH = _int("MIN_PHOTO_WIDTH", 1024)
MIN_PHOTO_HEIGHT = _int("MIN_PHOTO_HEIGHT", 683)

# Standing line appended (in code) after every outbound email.
HANDOFF_LINE = 'If you would like to talk to a human, please write "let me talk to a human".'
HANDOFF_TRIGGER = "let me talk to a human"
WAKE_TOKEN = "@jason"

# Gmail labels used for bookkeeping.
LABEL_PROCESSED = "jason-processed"
LABEL_SENT_BY_BOT = "jason-sent"
LABEL_NEEDS_HUMAN = "needs-human"
