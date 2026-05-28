"""
Logging configuration for the LinkedIn MCP server.

Log file: ~/.linkedin_mcp.log  (rotates at 5 MB, keeps 3 backups)
Format  : ISO timestamp | level | logger | message

Usage:
    import log_config
    log_config.setup()   # call once at server startup

To tail logs:
    tail -f ~/.linkedin_mcp.log
"""

from __future__ import annotations

import logging
import logging.handlers
import os


LOG_FILE = os.path.expanduser(os.environ.get("LINKEDIN_MCP_LOG", "~/.linkedin_mcp.log"))
LOG_LEVEL = os.environ.get("LINKEDIN_MCP_LOG_LEVEL", "INFO").upper()

_FORMATTER = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


def setup() -> None:
    """Configure root logger with a rotating file handler and optional stderr output."""
    root = logging.getLogger("linkedin_mcp")
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    if root.handlers:
        return  # already configured (e.g. tests)

    # Rotating file handler — always on
    os.makedirs(os.path.dirname(os.path.abspath(LOG_FILE)), exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(_FORMATTER)
    root.addHandler(fh)

    # Console handler only when LINKEDIN_MCP_DEBUG=1
    if os.environ.get("LINKEDIN_MCP_DEBUG", "").strip() == "1":
        ch = logging.StreamHandler()
        ch.setFormatter(_FORMATTER)
        root.addHandler(ch)

    root.info("LinkedIn MCP log started — level=%s file=%s", LOG_LEVEL, LOG_FILE)
