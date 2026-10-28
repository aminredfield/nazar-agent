import logging

import uvicorn

from nazar_agent.config import get_settings
from nazar_agent.service.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
# httpx logs every request URL, and the Telegram API carries the bot token in it.
logging.getLogger("httpx").setLevel(logging.WARNING)
settings = get_settings()
uvicorn.run(create_app(settings), host=settings.service_host, port=settings.service_port)
