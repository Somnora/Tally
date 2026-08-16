"""API keys must never appear in a URL.

httpx logs every request URL at INFO. A key passed as a query parameter is
therefore copied verbatim into log files, terminal scrollback, and any log
shipper, which is how credentials leak without anyone doing anything wrong.
Both clients send the key as a header instead. These tests fail if that
ever regresses, on either client, without needing a live API.
"""

from typing import Any

import httpx
import pytest

from pipeline import congress_api, fec_api
from pipeline.config import get_settings


class _Recorder:
    """Stand-in for httpx.get that captures how the request was addressed."""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.url: str = ""
        self.headers: dict[str, str] = {}

    def __call__(self, url: str, **kwargs: Any) -> httpx.Response:
        self.headers = dict(kwargs.get("headers") or {})
        request = httpx.Request("GET", httpx.URL(url, params=kwargs.get("params")))
        self.url = str(request.url)
        return httpx.Response(self.status_code, json={"ok": True}, request=request)


@pytest.fixture(autouse=True)
def _isolated_clients(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Fake keys, fresh clients, no throttle.

    Settings are process-cached, so without clearing them a test would read
    the developer's real key out of .env and could print it in a failure
    message. Every assertion below is also written as a boolean rather than
    an equality so that a failure never renders a key either way.
    """
    monkeypatch.setattr(congress_api.CongressApiClient, "_wait_for_slot", lambda self: None)
    monkeypatch.setattr(fec_api.FecApiClient, "_wait_for_slot", lambda self: None)
    get_settings.cache_clear()
    congress_api.client.cache_clear()
    fec_api.client.cache_clear()
    yield
    get_settings.cache_clear()
    congress_api.client.cache_clear()
    fec_api.client.cache_clear()


def test_congress_key_is_header_not_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONGRESS_GOV_API_KEY", "SECRET-CONGRESS-KEY")
    recorder = _Recorder()
    monkeypatch.setattr(httpx, "get", recorder)

    _, canonical = congress_api.client().get("/bill/119/hr/1")

    assert "SECRET-CONGRESS-KEY" not in recorder.url
    assert "api_key" not in recorder.url
    assert recorder.headers.get("X-Api-Key") == "SECRET-CONGRESS-KEY"
    # The provenance URL is what we actually requested, and is safe to store.
    assert canonical == recorder.url
    assert "SECRET-CONGRESS-KEY" not in canonical


def test_fec_key_is_header_not_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEC_API_KEY", "SECRET-FEC-KEY")
    monkeypatch.setenv("FEC_API_KEY_BACKUP", "")
    recorder = _Recorder()
    monkeypatch.setattr(httpx, "get", recorder)

    _, canonical = fec_api.client().get("/candidates/", {"per_page": 1})

    assert "SECRET-FEC-KEY" not in recorder.url
    assert "api_key" not in recorder.url
    assert recorder.headers.get("X-Api-Key") == "SECRET-FEC-KEY"
    assert "SECRET-FEC-KEY" not in canonical


def test_congress_client_does_not_retry_a_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing bill must fail once, not burn four slots of the rate limit."""
    monkeypatch.setenv("CONGRESS_GOV_API_KEY", "SECRET-CONGRESS-KEY")
    calls = {"n": 0}
    recorder = _Recorder(status_code=404)

    def counting_get(url: str, **kwargs: Any) -> httpx.Response:
        calls["n"] += 1
        return recorder(url, **kwargs)

    monkeypatch.setattr(httpx, "get", counting_get)
    with pytest.raises(congress_api.NotFoundError):
        congress_api.client().get("/bill/119/hr/999999")
    assert calls["n"] == 1
