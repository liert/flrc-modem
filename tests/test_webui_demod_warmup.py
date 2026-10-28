"""WebUI prepares real-time kernels before serving capture requests."""
from types import SimpleNamespace

import pytest


@pytest.mark.anyio
@pytest.mark.parametrize('failure', [False, True])
async def test_demod_warmup_finishes_before_api_is_ready(monkeypatch, failure):
    from webui.backend.app import main
    from webui.backend.app.events import hub
    from webui.backend.app.spectrum import manager
    from flrc_modem.phy import fec

    calls = []

    async def shutdown():
        pass

    def prune(db):
        return []

    def warm():
        calls.append('warmup')
        if failure:
            raise RuntimeError('optional accelerator unavailable')
        return True

    monkeypatch.setattr(fec, 'warm_up', warm)
    monkeypatch.setattr(hub, 'get_event_hub', lambda: SimpleNamespace(start=lambda: None, shutdown=shutdown))
    monkeypatch.setattr(main, 'get_db', lambda: SimpleNamespace(mark_lost_processes_on_startup=lambda: 0))
    monkeypatch.setattr(main, 'prune_overwritten_runs', prune)
    monkeypatch.setattr(main, 'install_service_diagnostics', lambda: None)
    monkeypatch.setattr(main, 'shutdown_logging', lambda: None)
    monkeypatch.setattr(main, 'get_job_manager', lambda: SimpleNamespace(shutdown=shutdown))
    monkeypatch.setattr(manager, 'get_spectrum_manager', lambda: SimpleNamespace(
        start_watchdog=lambda: calls.append('watchdog'), shutdown=shutdown))
    async with main.lifespan(main.app):
        # Failure still allows unrelated workbenches to start.
        assert calls == ['warmup', 'watchdog']
