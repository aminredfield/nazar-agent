"""The assistant on the demo construction company, in one command — no NAZAR needed.

    python -m nazar_agent.demo                # http://127.0.0.1:8100
    python -m nazar_agent.demo --port 8200

Only MODEL_API_KEY (a Meta Model API key) is needed, in the environment or in
.env. The Telegram bot stays off here even if .env has a token: a demo must
not answer someone else's chats.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn
from pydantic import ValidationError

from nazar_agent.config import Settings
from nazar_agent.service.app import create_app

DEFAULT_DB = "nazar_agent_demo.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser(prog="nazar_agent.demo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to open it to the network")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()

    try:
        settings = Settings(
            demo_mode=True,
            telegram_bot_token=None,
            service_host=args.host,
            service_port=args.port,
            agent_db_path=os.environ.get("AGENT_DB_PATH", DEFAULT_DB),
        )
    except ValidationError as exc:
        if any(error["loc"] == ("model_api_key",) for error in exc.errors()):
            print(
                "MODEL_API_KEY is not set. Put your Meta Model API key (https://dev.meta.ai) in .env:\n"
                "    MODEL_API_KEY=...",
                file=sys.stderr,
            )
            return 2
        raise

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    print(f"\n  NAZAR Agent on the demo company: http://{args.host}:{args.port}\n", flush=True)
    uvicorn.run(create_app(settings), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
