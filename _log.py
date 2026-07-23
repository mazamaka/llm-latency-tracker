"""Logger: loguru if installed, otherwise stdlib logging via an adapter.

The whole project calls logger.info("... {} ...", a, b) — loguru syntax ({}-placeholders).
Bare stdlib logging formats via %, so without an adapter every call would lose its
message (TypeError inside Handler.emit). The adapter runs msg.format(*args) ahead of time.
"""
from __future__ import annotations

try:
    from loguru import logger  # type: ignore
except ImportError:  # pragma: no cover - fallback for environments without pip
    import logging

    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s | %(levelname)-7s | %(message)s")
    _base = logging.getLogger("ai-latency-tracker")

    class _LoguruStyleAdapter:
        """Converts loguru-style ('{}', *args) into a ready string for stdlib logging."""

        def __init__(self, base: "logging.Logger") -> None:
            self._base = base

        @staticmethod
        def _fmt(message: object, args: tuple, kwargs: dict) -> str:
            msg = str(message)
            if not args and not kwargs:
                return msg
            try:
                return msg.format(*args, **kwargs)
            except (IndexError, KeyError, ValueError):
                return msg + " " + " ".join(str(a) for a in args)  # don't drop the log over a format error

        def debug(self, message: object, *a: object, **k: object) -> None:
            self._base.debug(self._fmt(message, a, k))

        def info(self, message: object, *a: object, **k: object) -> None:
            self._base.info(self._fmt(message, a, k))

        def warning(self, message: object, *a: object, **k: object) -> None:
            self._base.warning(self._fmt(message, a, k))

        def error(self, message: object, *a: object, **k: object) -> None:
            self._base.error(self._fmt(message, a, k))

    logger = _LoguruStyleAdapter(_base)  # type: ignore

__all__ = ["logger"]
