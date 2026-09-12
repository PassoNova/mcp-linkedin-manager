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


class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler whose log files are always created with mode 0600.

    The stock handler opens the base file with the process umask, on creation
    and again after every rollover. The log can carry aliases, profile paths
    and API error bodies, so every file it creates is opened via ``os.open``
    with an explicit ``0o600`` and tightened on the descriptor if it already
    existed with looser permissions.
    """

    def __init__(self, filename: str, **kwargs) -> None:
        super().__init__(filename, **kwargs)
        self._tighten_backups()

    def _open(self):  # type: ignore[override]
        flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND
        if self.mode.startswith("w"):
            flags |= os.O_TRUNC
        fd = os.open(self.baseFilename, flags, 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
        except OSError as exc:
            # Fail closed: never keep writing to a log we could not make owner-only
            # (e.g. a file owned by another user). Point LINKEDIN_MCP_LOG elsewhere.
            os.close(fd)
            raise OSError(
                f"refusing to log to {self.baseFilename}: cannot make it owner-only ({exc}); "
                "fix its ownership/permissions or set LINKEDIN_MCP_LOG to another path"
            ) from exc
        return open(fd, self.mode, encoding=self.encoding, errors=self.errors)

    def _tighten_backups(self) -> None:
        """Re-apply 0600 to rotated backups (``<log>.1`` … ``<log>.N``).

        Backups written by an older version, or by a stock handler, keep the
        mode they were created with; rollover only renames them. Run at
        setup and after every rollover.
        """
        for i in range(1, self.backupCount + 1):
            backup = self.rotation_filename(f"{self.baseFilename}.{i}")
            if os.path.exists(backup):
                os.chmod(backup, 0o600)

    def doRollover(self) -> None:  # noqa: N802 - logging API
        super().doRollover()
        self._tighten_backups()


def setup() -> None:
    """Configure root logger with a rotating file handler and optional stderr output."""
    root = logging.getLogger("linkedin_mcp")
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    if root.handlers:
        return  # already configured (e.g. tests)

    # Rotating file handler — always on
    os.makedirs(os.path.dirname(os.path.abspath(LOG_FILE)), exist_ok=True)
    fh = _PrivateRotatingFileHandler(
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
