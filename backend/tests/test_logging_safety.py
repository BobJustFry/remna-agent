import io
import logging

from app.logging_safety import TokenMaskFilter, setup_log_safety


def test_httpx_not_info():
    setup_log_safety()
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)


def test_filter_masks_token():
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.addFilter(TokenMaskFilter())
    lg = logging.getLogger("t.mask")
    lg.propagate = False
    lg.setLevel(logging.INFO)
    lg.addHandler(h)
    lg.info('HTTP Request: POST %s "HTTP/1.1 200 OK"',
            "https://api.telegram.org/bot123456:AAE_x-yZ9/sendMessage")
    out = buf.getvalue()
    assert "bot***/sendMessage" in out
    assert "123456" not in out and "AAE_x" not in out
