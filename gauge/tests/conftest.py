import pytest


@pytest.fixture(autouse=True)
def isolate_capture_configuration(monkeypatch):
    """Tests must never export to a developer's configured real backend."""
    for name in ("GAUGE_ENDPOINT", "GAUGE_API_KEY", "GAUGE_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GAUGE_JUDGE", "off")
