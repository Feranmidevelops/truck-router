import copy
import io
import logging

from django.utils.log import configure_logging
import pytest


@pytest.fixture
def console(settings):
    """The project's logging config, with the console handler writing to a buffer."""
    stream = io.StringIO()
    config = copy.deepcopy(settings.LOGGING)
    config["handlers"]["console"]["stream"] = stream
    # Django applies its own defaults first, then the project's LOGGING on top
    configure_logging(settings.LOGGING_CONFIG, config)
    try:
        yield stream
    finally:
        configure_logging(settings.LOGGING_CONFIG, settings.LOGGING)


def test_server_errors_and_app_logs_print_with_debug_off(settings, console):
    settings.DEBUG = False
    logging.getLogger("django.request").error("Internal Server Error: /api/plan")
    logging.getLogger("routing.views").error("TomTom failed")
    logging.getLogger("routing.services.planner").info("plan built")
    out = console.getvalue()
    assert "ERROR django.request: Internal Server Error: /api/plan" in out
    assert "ERROR routing.views: TomTom failed" in out
    assert "INFO routing.services.planner: plan built" in out


def test_client_errors_and_library_chatter_stay_out_of_the_log(settings, console):
    settings.DEBUG = False
    logging.getLogger("django.request").warning("Not Found: /favicon.ico")
    logging.getLogger("django.utils.autoreload").info("Watching for file changes")
    # httpx logs every request URL at INFO, and the TomTom key is in the query string
    logging.getLogger("httpx").info("HTTP Request: GET https://api.tomtom.com/...?key=secret")
    assert console.getvalue() == ""
