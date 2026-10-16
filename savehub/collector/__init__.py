"""SaveHub browser-automation collectors (LOCAL / self-hosted only).

Logs into a social app in a real Chromium (Playwright) using a saved session, scrapes the user's
saved/bookmarked items and pushes them into a SaveHub instance over its REST API (or a local SQLite DB).
Optionally un-saves them on the app afterwards (OFF by default, dry-run by default).

The web/MCP server only imports the registry + session metadata from here (never Playwright); MCP `collect` runs the
CLI in a subprocess, so the Render image does not need Playwright (it reports the collector as unavailable).
Run it with:  python -m savehub.collector --help
"""
from .base import (Collector, CollectedItem, RunConfig, NeedLogin, CollectorError, NotImplementedPlatform,
                   REGISTRY, register, get_collector, list_platforms)
from . import platforms  # noqa: F401  (registers all collectors)

__all__ = ["Collector", "CollectedItem", "RunConfig", "NeedLogin", "CollectorError", "NotImplementedPlatform",
           "REGISTRY", "register", "get_collector", "list_platforms"]
