"""Zero-leak secret redaction utilities and logging filter for METEORO TRADER."""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from src.config import Settings

REDACTED_TOKEN = "[REDACTED]"
REDACTED_KEYPAIR = "[REDACTED_KEYPAIR]"

# Patterns catching leaked credentials even if not registered explicitly in Settings
_PATTERN_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Telegram Bot API URL tokens: https://api.telegram.org/bot123456:ABC...
    (re.compile(r"\bbot\d{6,}:[A-Za-z0-9_-]{20,}"), f"bot{REDACTED_TOKEN}"),
    # OpenRouter / OpenAI-style secret keys
    (re.compile(r"\bsk-or-v1-[A-Za-z0-9]{12,}"), REDACTED_TOKEN),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), REDACTED_TOKEN),
    # Authorization Bearer tokens
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"), f"Bearer {REDACTED_TOKEN}"),
    # Solana 64-byte JSON array private keys ([12, 34, ...])
    (re.compile(r"\[\s*(?:\d{1,3}\s*,\s*){63}\d{1,3}\s*\]"), REDACTED_KEYPAIR),
    # Helius / RPC query string api-key=...
    (re.compile(r"(?i)([?&](?:api[-_]?key|token)=)[^&\s]+"), rf"\1{REDACTED_TOKEN}"),
)


def sanitize_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Redact registered secret strings and common credential patterns from ``text``."""
    if not text:
        return text
    cleaned = text
    # Sort longest-first so shorter substrings cannot leave partial tails behind
    unique_secrets = sorted(
        {s.strip() for s in secrets if isinstance(s, str) and len(s.strip()) >= 6},
        key=len,
        reverse=True,
    )
    for secret in unique_secrets:
        if secret in cleaned:
            cleaned = cleaned.replace(secret, REDACTED_TOKEN)

    for pattern, replacement in _PATTERN_RULES:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


class SecretRedactingFilter(logging.Filter):
    """Logging filter that strips private keys and API keys before any handler emits them."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets: set[str] = {
            s.strip() for s in secrets if isinstance(s, str) and len(s.strip()) >= 6
        }
        self._exc_formatter = logging.Formatter()

    def register_secret(self, secret: str | None) -> None:
        """Add an additional secret value to the redaction set."""
        if isinstance(secret, str) and len(secret.strip()) >= 6:
            self._secrets.add(secret.strip())

    def register_from_settings(self, settings: Settings) -> None:
        """Register all non-empty secrets from a ``Settings`` object."""
        for value in settings.secret_values():
            self.register_secret(value)

    def _sanitize_arg(self, arg: Any) -> Any:
        if isinstance(arg, str):
            return sanitize_text(arg, self._secrets)
        if isinstance(arg, BaseException):
            return sanitize_text(str(arg), self._secrets)
        return arg

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize_text(record.msg, self._secrets)
        elif record.msg is not None:
            record.msg = sanitize_text(str(record.msg), self._secrets)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._sanitize_arg(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._sanitize_arg(v) for v in record.args)

        if record.exc_info and not record.exc_text:
            record.exc_text = self._exc_formatter.formatException(record.exc_info)
            record.exc_info = None
        if record.exc_text:
            record.exc_text = sanitize_text(record.exc_text, self._secrets)

        if record.stack_info:
            record.stack_info = sanitize_text(str(record.stack_info), self._secrets)

        return True


def install_secret_redaction(settings: Settings | None = None) -> SecretRedactingFilter:
    """Attach a ``SecretRedactingFilter`` to the root logger and all attached handlers."""
    secrets = settings.secret_values() if settings is not None else []
    root = logging.getLogger()

    existing = next(
        (f for f in root.filters if isinstance(f, SecretRedactingFilter)),
        None,
    )
    redactor = existing or SecretRedactingFilter(secrets)
    if settings is not None:
        redactor.register_from_settings(settings)

    if redactor not in root.filters:
        root.addFilter(redactor)
    for handler in root.handlers:
        if redactor not in handler.filters:
            handler.addFilter(redactor)
    return redactor
