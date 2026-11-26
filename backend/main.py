"""METEORO TRADER SOLANA — Backend autonomous entrypoint."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.config import Settings, get_settings
from src.orchestrator import TradingOrchestrator
from src.security import install_secret_redaction


def configure_logging(settings: Settings) -> None:
    """Configure console + rotating file logging inside ``backend/logs/`` with secret redaction."""
    log_dir = settings.resolved_log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "meteoro.log"

    level = getattr(logging, settings.log_level, logging.INFO)
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    if not root_logger.handlers:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging.Formatter(fmt))
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(fmt))
        root_logger.addHandler(stream_handler)
        root_logger.addHandler(file_handler)
    install_secret_redaction(settings)


async def run_bot() -> None:
    """Initialize settings, logging, and start ``TradingOrchestrator``."""
    settings = get_settings()
    configure_logging(settings)
    logger = logging.getLogger("meteoro.main")

    orchestrator = TradingOrchestrator(settings=settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, orchestrator.stop)
        except NotImplementedError:
            pass

    logger.info(
        "Starting METEORO TRADER SOLANA | mode=%s | trade_size=%.4f SOL | db=%s",
        settings.trading_mode.value,
        settings.trade_size_sol,
        settings.resolved_database_path,
    )
    await orchestrator.run_forever()


def main() -> None:
    """CLI entrypoint for launching the autonomous trading engine."""
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
