import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def isolated_cache(settings):
    # The real cache is a file cache in the system temp dir, shared by every process on the
    # machine. Tests get an in-memory one, emptied first, so throttle history and stored plans
    # never leak between tests, earlier runs, or test runs going on at the same time.
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tests"}
    }
    cache.clear()
