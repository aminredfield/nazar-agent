import logging

import uvicorn

from nazar_agent.config import get_settings
from nazar_agent.service.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
settings = get_settings()
uvicorn.run(create_app(settings), host=settings.service_host, port=settings.service_port)
