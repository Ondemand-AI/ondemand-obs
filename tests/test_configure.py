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


def test_default_endpoint_is_the_ondemand_hub(monkeypatch):
    from ondemand_obs.config import ObsConfig

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert ObsConfig().endpoint == "https://otel.ondemand-ai.com.br"


def test_configure_publishes_the_endpoint_for_child_processes(monkeypatch):
    import ondemand_obs

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("HYPERDX_API_KEY", "test-key")
    monkeypatch.setattr(ondemand_obs, "_configured", False)
    for name in ("setup_traces", "setup_metrics", "setup_instruments"):
        monkeypatch.setattr(ondemand_obs, name, lambda *a, **k: None)
    monkeypatch.setattr(ondemand_obs, "setup_logs", lambda *a, **k: (None, None))

    ondemand_obs.configure_observability("svc")

    import os
    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://otel.ondemand-ai.com.br"
