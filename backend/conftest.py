import httpx
import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # A test that forgets to fake TomTom would otherwise call it with the real key from .env.
    # Fakes answer through httpx.MockTransport, which never gets here.
    def refuse(transport, request):
        raise RuntimeError(f"A test tried to reach {request.url.host} for real")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture(autouse=True)
def isolated_cache(settings):
    # The real cache is a file cache in the system temp dir, shared by every process on the
    # machine. Tests get an in-memory one, emptied first, so throttle history and stored plans
    # never leak between tests, earlier runs, or test runs going on at the same time.
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tests"}
    }
    cache.clear()
