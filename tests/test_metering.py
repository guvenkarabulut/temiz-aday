from types import SimpleNamespace

from app.metering import impact

CFG = SimpleNamespace(wh_per_call=0.3, grid_g_per_kwh=475, cpu_watts=15, prompt_tokens=300, out_tokens=300)


def test_numbers_add_up():
    rows = [{"decision": "rejected", "tokens_est": 2000, "filter_ms": 10}] * 9 + [
        {"decision": "llm", "tokens_est": 2000, "filter_ms": 10}]
    i = impact(rows, CFG)
    assert i.total == 10 and i.rejected == 9
    assert i.wh_saved == 9 * 0.3
    assert i.tokens_avoided == 9 * (2000 + 600)
    assert abs(i.filter_wh - 100 * 15 / 3.6e6) < 1e-12      # 100 ms · 15 W
    assert abs(i.co2_g - i.net_wh / 1000 * 475) < 1e-9
    assert i.ratio > 1000


def test_empty():
    i = impact([], CFG)
    assert i.total == 0 and i.ratio is None and i.net_wh == 0
