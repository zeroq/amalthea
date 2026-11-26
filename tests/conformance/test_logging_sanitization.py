from __future__ import annotations

import logging

from amalthea.logging import SanitizeRequestPathFilter


def test_sanitize_request_path_filter_escapes_crlf_in_args_and_msg():
    record = logging.LogRecord(
        name="django.request",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="Not Found: %s\ninjected",
        args=("/bad\r\npath",),
        exc_info=None,
    )

    assert SanitizeRequestPathFilter().filter(record) is True
    assert record.msg == "Not Found: %s\\ninjected"
    assert record.args == ("/bad\\r\\npath",)
