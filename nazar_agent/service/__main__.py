import logging

import uvicorn

from nazar_agent.service.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
# Localhost only: the window has no sign-in yet.
uvicorn.run(create_app(), host="127.0.0.1", port=8100)
