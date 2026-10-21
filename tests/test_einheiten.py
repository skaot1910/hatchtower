"""RAM and disk as the panel counts them: limits are set in MiB, so GB means 1024³ bytes (16384 MiB = 16 GB)."""
from hatchtower import i18n
from hatchtower.views import fmt_gb

MIB = 1024 ** 2


def test_gb_wie_im_panel():
    token = i18n._current.set("de")
    try:
        assert fmt_gb(16384 * MIB) == "16"           # was 17,2 (divided by 10⁹)
        assert fmt_gb(8704 * MIB) == "8,5"
        assert fmt_gb(0.53 * 1024 ** 3) == "0,53"
        assert fmt_gb(None) == "0"
    finally:
        i18n._current.reset(token)
