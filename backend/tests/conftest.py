"""Compatibility overrides apply ONLY to original v1 API tests.

New security/domain tests use actual authentication or explicitly provision their
own principals. Production dependencies never inspect pytest or environment flags.
"""
import uuid
import pytest


@pytest.fixture(autouse=True)
def legacy_authenticated_identity(request):
    if request.node.path.name not in {'test_api_pipeline.py', 'test_integration.py', 'test_detection.py', 'test_platform.py'}:
        yield
        return
    from app.main import app
    from app.core.security import get_current_user, Principal
    previous = app.dependency_overrides.get(get_current_user)
    async def principal():
        return Principal(uuid.UUID('00000000-0000-0000-0000-000000000001'), 'legacy-test-investigator', 'ADMIN')
    app.dependency_overrides[get_current_user] = principal
    yield
    if previous is None:
        app.dependency_overrides.pop(get_current_user, None)
    else:
        app.dependency_overrides[get_current_user] = previous
