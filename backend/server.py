"""METEORO TRADER SOLANA — Command Center server (API + dashboard + trading bot)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

BACKEND_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from main import configure_logging  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.dashboard_api import create_app  # noqa: E402

settings = get_settings()
configure_logging(settings)

# RUN_BOT=false serves only the dashboard/API without starting the autonomous scanner.
app = create_app(
    settings=settings,
    run_orchestrator=os.getenv("RUN_BOT", "true").strip().lower() not in {"0", "false", "no"},
)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.api_host, port=settings.api_port, log_level="info")
