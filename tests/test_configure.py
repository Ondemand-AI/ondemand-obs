import importlib
import os
import sys


def _reload_module():
    """Reload ondemand_obs to reset global state between tests."""
    for mod in list(sys.modules.keys()):
        if mod.startswith("ondemand_obs"):
            del sys.modules[mod]
    import ondemand_obs
    return ondemand_obs


def test_noop_without_api_key(monkeypatch):
    monkeypatch.delenv("HYPERDX_API_KEY", raising=False)
    obs = _reload_module()
    obs.configure_observability("test-service")
    assert not obs.is_configured()
    assert obs.get_otlp_log_handler() is None


def test_idempotent(monkeypatch):
    monkeypatch.setenv("HYPERDX_API_KEY", "test-key-123")
    obs = _reload_module()
    obs.configure_observability("svc-a")
    obs.configure_observability("svc-b")  # second call should be no-op
    assert obs.is_configured()
    # If second call ran, it would raise because providers are already set.
    # Reaching here means idempotency guard worked.


def test_configured_with_key(monkeypatch):
    monkeypatch.setenv("HYPERDX_API_KEY", "test-key-456")
    obs = _reload_module()
    obs.configure_observability("my-robot")
    assert obs.is_configured()
    assert obs.get_otlp_log_handler() is not None
