from __future__ import annotations

import pytest
from fake_wda import FakeWda
from ios_agent.config import AgentSettings

from ios_mcp.config import Settings
from ios_mcp.wda.client import WdaClient
from ios_mcp.wda.session import WdaSession


@pytest.fixture(autouse=True)
def ignore_any_local_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests assert what the code defaults to, not what someone's `.env` says.

    Enabling `.env` bought convenience and cost hermeticity: a developer with
    `IOS_AGENT_PROVIDER=openai` in a gitignored file would otherwise watch the
    test asserting the default is Anthropic fail for a reason nothing in the
    diff explains. Doing it centrally rather than at each construction site
    also covers tests that do not exist yet.

    `tests/unit/test_dotenv.py` turns this back on, because loading the file is
    the thing it is testing.
    """
    for cls in (Settings, AgentSettings):
        monkeypatch.setitem(cls.model_config, "env_file", None)


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
