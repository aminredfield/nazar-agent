"""Where the agent's NAZAR is: the real one, or the demo one in this process.

Every way to reach the agent — the window, the Telegram bot, the CLI, the
evals — gets its NAZAR clients here, so ``DEMO_MODE`` switches all of them at
once and the agent's code is the same for both:

- the real NAZAR at ``NAZAR_BASE_URL``, with the service account and the
  assistant key from the settings;
- the demo NAZAR (``nazar_agent.demo``): the demo construction company built
  in memory and reached through ``httpx.ASGITransport`` — no network, no
  NAZAR account, nothing to configure but the model key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from nazar_agent.nazar.client import NazarClient

if TYPE_CHECKING:
    from nazar_agent.config import Settings
    from nazar_agent.demo.server import DemoNazar

DEMO_BASE_URL = "http://demo-nazar"


@dataclass(frozen=True, slots=True)
class NazarLink:
    base_url: str
    email: str
    password: str
    agent_key: str
    timeout_seconds: float
    transport: httpx.AsyncBaseTransport | None = None
    demo: DemoNazar | None = None  # set in demo mode: the demo NAZAR's state, e.g. to reset it

    @property
    def has_service_account(self) -> bool:
        return bool(self.email and self.password)

    def client(self, *, access_token: str | None = None) -> NazarClient:
        """The service account's client, or a person's with ``access_token``."""
        if access_token is not None:
            return NazarClient(
                base_url=self.base_url, access_token=access_token,
                timeout_seconds=self.timeout_seconds, transport=self.transport,
            )
        return NazarClient(
            base_url=self.base_url, email=self.email, password=self.password, agent_key=self.agent_key,
            timeout_seconds=self.timeout_seconds, transport=self.transport,
        )


def connect(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> NazarLink:
    """The NAZAR these settings name; ``transport`` replaces the network in tests."""
    if settings.demo_mode:
        return demo_link(settings)
    return NazarLink(
        base_url=settings.nazar_base_url,
        email=settings.nazar_email,
        password=settings.nazar_password.get_secret_value(),
        agent_key=settings.nazar_agent_key.get_secret_value(),
        timeout_seconds=settings.nazar_timeout_seconds,
        transport=transport,
    )


def demo_link(settings: Settings) -> NazarLink:
    # Imported here: the demo pulls in photo generation, which the real setup never needs.
    from nazar_agent.demo.server import DEMO_AGENT_KEY, DEMO_EMAIL, DEMO_PASSWORD, DemoNazar, create_app

    clock = settings.now if settings.demo_clock_runs else None
    demo = DemoNazar.today(clock, agent_url=f"http://{settings.service_host}:{settings.service_port}")
    return NazarLink(
        base_url=DEMO_BASE_URL,
        email=DEMO_EMAIL,
        password=DEMO_PASSWORD,
        agent_key=DEMO_AGENT_KEY,
        timeout_seconds=settings.nazar_timeout_seconds,
        transport=httpx.ASGITransport(app=create_app(demo)),
        demo=demo,
    )
