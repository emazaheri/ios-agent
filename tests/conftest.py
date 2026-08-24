from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fake_wda import FakeWda

from ios_mcp.config import Settings
from ios_mcp.wda.client import WdaClient
from ios_mcp.wda.session import WdaSession


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def fake_wda() -> FakeWda:
    return FakeWda()


@pytest.fixture
def wda_client(fake_wda: FakeWda, settings: Settings) -> WdaClient:
    client = WdaClient("http://127.0.0.1:8100", settings.wda)
    client._http = fake_wda.client_factory()
    return client


@pytest.fixture
def wda_session(wda_client: WdaClient, settings: Settings) -> WdaSession:
    return WdaSession(wda_client, settings)
