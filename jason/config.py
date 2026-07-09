"""Configuration for Jason.

Non-secret settings live in config/email-bot.json (camelCase keys, same
pattern as Main-Pipeline's config/chat-bot.json); secrets stay in the
environment / .env.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _load_nonsecret_config() -> dict:
    path = Path(__file__).resolve().parents[1] / "config" / "email-bot.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


_config = _load_nonsecret_config()


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- LLM ---
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = _config.get("openrouterModel", "qwen/qwen3.6-flash")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# --- Gmail ---
GMAIL_CREDENTIALS_FILE = _config.get("gmailCredentialsPath", "credentials.json")
GMAIL_TOKEN_FILE = _config.get("gmailTokenPath", "token.json")
OPERATOR_EMAIL = _config.get("operatorEmail", "jasonfromhowards1@gmail.com").lower()
POLL_INTERVAL_SECONDS = _int(_config.get("pollIntervalSeconds"), 30)
GMAIL_SEARCH_QUERY = _config.get("gmailSearchQuery", "in:inbox is:unread")
GMAIL_IGNORED_SENDER_PATTERNS = [
    str(p).strip().lower()
    for p in _config.get(
        "gmailIgnoredSenderPatterns",
        ["no-reply", "noreply", "donotreply", "do-not-reply"],
    )
    if str(p).strip()
]

# --- Stripe ---
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
VIDEO_PRICE_CENTS = _int(_config.get("videoPriceCents"), 19900)
VIDEO_CURRENCY = _config.get("videoCurrency", "nzd")
VIDEO_PRODUCT_NAME = _config.get("videoProductName", "Howards AI Marketing Video")

# --- Google search APIs ---
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
GOOGLE_CSE_API_KEY = os.environ.get("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")

# --- Render pipeline ---
RENDER_URL = _config.get("renderUrl") or ""

# --- Webhook server ---
WEBHOOK_HOST = _config.get("webhookHost", "0.0.0.0")
WEBHOOK_PORT = _int(_config.get("webhookPort"), 8000)

# --- Storage ---
SUPABASE_SCHEMA = _config.get("supabaseSchema", "jason_memory")
SUPABASE_CUSTOMERS_TABLE = _config.get("supabaseCustomersTable", "customers")
MEMORY_DIR = Path(_config.get("memoryDir", "./memory")).resolve()

# --- Behaviour ---
GHOST_DAYS = _int(_config.get("ghostDays"), 7)
MIN_PHOTOS = _int(_config.get("minPhotos"), 12)
MIN_PHOTO_WIDTH = _int(_config.get("minPhotoWidth"), 1024)
MIN_PHOTO_HEIGHT = _int(_config.get("minPhotoHeight"), 683)

# Standing line appended (in code) after every outbound email.
HANDOFF_LINE = 'If you would like to talk to a human, please write "let me talk to a human".'
HANDOFF_TRIGGER = "let me talk to a human"
WAKE_TOKEN = "@jason"

# Gmail labels used for bookkeeping.
LABEL_PROCESSED = "jason-processed"
LABEL_SENT_BY_BOT = "jason-sent"
LABEL_NEEDS_HUMAN = "needs-human"
