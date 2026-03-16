"""
App category classifier — DB-backed, Claude-powered.

Lookup order:
  1. In-memory _cache dict (fast path, populated lazily from DB)
  2. app_category_cache table
  3. Hardcoded APP_CATEGORIES dict
  4. Claude API call → writes result to DB + in-memory cache

Classification runs at INGESTION TIME only (called from tracking_history.py).
Report endpoints read only from the cache — they never trigger API calls.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Hardcoded well-known mappings (source='hardcoded')
APP_CATEGORIES: dict[str, str] = {
    # Browsers
    "chrome.exe": "Browser",
    "firefox.exe": "Browser",
    "msedge.exe": "Browser",
    "iexplore.exe": "Browser",
    "opera.exe": "Browser",
    "brave.exe": "Browser",
    # Development
    "code.exe": "Development",
    "devenv.exe": "Development",
    "pycharm64.exe": "Development",
    "idea64.exe": "Development",
    "webstorm64.exe": "Development",
    "androidstudio64.exe": "Development",
    "eclipse.exe": "Development",
    "notepad++.exe": "Development",
    "sublime_text.exe": "Development",
    "atom.exe": "Development",
    "cursor.exe": "Development",
    "git-bash.exe": "Development",
    "powershell.exe": "Development",
    "cmd.exe": "Development",
    "windowsterminal.exe": "Development",
    "wt.exe": "Development",
    # Productivity / Office
    "winword.exe": "Productivity",
    "excel.exe": "Productivity",
    "powerpnt.exe": "Productivity",
    "onenote.exe": "Productivity",
    "msaccess.exe": "Productivity",
    "mspub.exe": "Productivity",
    "outlook.exe": "Productivity",
    "thunderbird.exe": "Productivity",
    "acrobat.exe": "Productivity",
    "acrord32.exe": "Productivity",
    "foxitreader.exe": "Productivity",
    "soffice.exe": "Productivity",
    "libreoffice.exe": "Productivity",
    "notion.exe": "Productivity",
    "obsidian.exe": "Productivity",
    # Communication
    "teams.exe": "Communication",
    "slack.exe": "Communication",
    "zoom.exe": "Communication",
    "discord.exe": "Communication",
    "skype.exe": "Communication",
    "msteams.exe": "Communication",
    "webex.exe": "Communication",
    "signal.exe": "Communication",
    "telegram.exe": "Communication",
    "whatsapp.exe": "Communication",
    # Entertainment
    "spotify.exe": "Entertainment",
    "vlc.exe": "Entertainment",
    "wmplayer.exe": "Entertainment",
    "steam.exe": "Entertainment",
    "epicgameslauncher.exe": "Entertainment",
    "netflix.exe": "Entertainment",
    "itunes.exe": "Entertainment",
    # Utility / System
    "explorer.exe": "Utility",
    "taskmgr.exe": "Utility",
    "mmc.exe": "Utility",
    "regedit.exe": "Utility",
    "control.exe": "Utility",
    "msiexec.exe": "Utility",
    "svchost.exe": "Utility",
    "werfault.exe": "Utility",
    "7zfm.exe": "Utility",
    "winrar.exe": "Utility",
    "python.exe": "Development",
    "python3.exe": "Development",
    "node.exe": "Development",
    "npm.exe": "Development",
    "java.exe": "Development",
    "javaw.exe": "Development",
}

# In-memory cache (populated lazily; shared across the process lifetime)
_cache: dict[str, str] = {}
_cache_loaded = False
_cache_lock = threading.Lock()  # Guards cold-start race in multi-threaded Flask


def _load_cache_from_db() -> None:
    """Populate in-memory cache from app_category_cache table on first call.
    Thread-safe: only the first thread to acquire _cache_lock loads from DB.
    """
    global _cache_loaded
    if _cache_loaded:
        return
    with _cache_lock:
        if _cache_loaded:  # double-checked locking
            return
        try:
            from extensions import db
            from models.app_category_cache import AppCategoryCache
            rows = db.session.query(AppCategoryCache.app_name, AppCategoryCache.category).all()
            for row in rows:
                _cache[row.app_name.lower()] = row.category
            _cache_loaded = True
        except Exception:
            logger.debug("app_category_cache table not yet available — skipping cache load")
            _cache_loaded = True  # prevent retry storm; cache stays empty, Claude fallback still works


def _save_to_db(app_name: str, category: str, source: str) -> None:
    try:
        from extensions import db
        from models.app_category_cache import AppCategoryCache
        existing = db.session.get(AppCategoryCache, app_name)
        if not existing:
            db.session.add(AppCategoryCache(
                app_name=app_name,
                category=category,
                source=source,
                created_at=datetime.utcnow(),
            ))
            db.session.commit()
    except Exception:
        try:
            from extensions import db
            db.session.rollback()
        except Exception:
            pass
        logger.debug("Failed to persist app category for %s", app_name)


def classify_app(app_name: str) -> str:
    """
    Return a category string for an app name.

    Categories: Productivity, Communication, Development, Browser, Entertainment, Utility, Unknown

    Never raises — falls back to 'Unknown' on any error.
    Called at ingestion time only (tracking_history.py); never called from report endpoints.
    """
    if not app_name:
        return "Unknown"

    key = app_name.strip().lower()

    # 1. In-memory cache (fast path)
    _load_cache_from_db()
    if key in _cache:
        return _cache[key]

    # 2. Hardcoded dict
    if key in APP_CATEGORIES:
        cat = APP_CATEGORIES[key]
        _cache[key] = cat
        _save_to_db(app_name.strip(), cat, "hardcoded")
        return cat

    # 3. Claude API
    cat = _classify_via_claude(app_name.strip())

    _cache[key] = cat
    _save_to_db(app_name.strip(), cat, "claude")
    return cat


def _classify_via_claude(app_name: str) -> str:
    """Call claude-haiku-4-5 to classify an app. Returns 'Unknown' on any failure."""
    try:
        import os
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return "Unknown"

        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            messages=[{
                "role": "user",
                "content": (
                    "Classify this Windows application executable into exactly one word from this list: "
                    "Productivity, Communication, Development, Browser, Entertainment, Utility, Unknown. "
                    f"App name: {app_name}"
                ),
            }],
        )
        raw = response.content[0].text.strip().split()[0].capitalize()
        valid = {"Productivity", "Communication", "Development", "Browser", "Entertainment", "Utility", "Unknown"}
        return raw if raw in valid else "Unknown"
    except Exception as _exc:
        # Surface authentication errors prominently so admins know the API key is broken
        exc_name = type(_exc).__name__
        if "Authentication" in exc_name or "Permission" in exc_name:
            logger.warning(
                "ANTHROPIC_API_KEY is invalid or missing — app classification via Claude disabled "
                "(error: %s). Set ANTHROPIC_API_KEY in .env to enable AI classification.",
                exc_name,
            )
        else:
            logger.debug("Claude classify_app failed for %r: %s", app_name, exc_name)
        return "Unknown"
