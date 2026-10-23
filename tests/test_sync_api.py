"""Removed workbench modes must not remain exposed through the API schema."""
import pytest

from webui.backend.app.main import app


@pytest.mark.parametrize('path', ['/api/v1/sync/preview-schedule', '/api/v1/sync/parse-timeline'])
def test_removed_workbench_routes_are_not_registered(path):
    assert path not in {getattr(route, 'path', None) for route in app.routes}
    assert path not in app.openapi()['paths']
