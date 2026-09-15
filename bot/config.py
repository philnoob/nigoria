"""Bot configuration.

All secrets come from environment variables so nothing sensitive lives in the
repo. IDs below match the deployment described in the task and can be
overridden via environment variables.
"""

from __future__ import annotations

import os

# --- required secret --------------------------------------------------------
TOKEN = os.environ.get("DISCORD_TOKEN", "")

# --- access control ---------------------------------------------------------
# Only this user may deploy panels with /panel-free and /panel-pro.
ADMIN_USER_ID = int(os.environ.get("PANEL_ADMIN_ID", "392050489505873931"))

# Members carrying this role are treated as Pro / paid users.
PRO_ROLE_ID = int(os.environ.get("PRO_ROLE_ID", "1549480180672626688"))

# --- limits -----------------------------------------------------------------
FREE_DAILY_LIMIT = int(os.environ.get("FREE_DAILY_LIMIT", "3"))

# Largest script (characters) accepted through a paste modal or file upload.
MAX_SCRIPT_CHARS = int(os.environ.get("MAX_SCRIPT_CHARS", "200000"))

# Where per-user daily usage is persisted.
DATA_DIR = os.environ.get("NIGORIA_DATA_DIR",
                          os.path.join(os.path.dirname(__file__), "..", "data"))
USAGE_FILE = os.path.join(DATA_DIR, "usage.json")
WHITELIST_FILE = os.path.join(DATA_DIR, "whitelist.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

# Optional: restrict slash-command sync to one guild for instant availability.
GUILD_ID = os.environ.get("GUILD_ID")

# Optional: fixed channel to point newly-whitelisted users to. If unset, the
# channel where /panel-pro was last posted is used.
PRO_PANEL_CHANNEL_ID = os.environ.get("PRO_PANEL_CHANNEL_ID")
