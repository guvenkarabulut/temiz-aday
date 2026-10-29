"""Enerji ve karbon hesabı (GSF SCI yaklaşımı). Katsayılar Settings'ten gelir, raporda açıkça yazılır."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Impact:
    total: int
    rejected: int
    tokens_avoided: int
    wh_saved: float      # elenen çağrıların brüt enerjisi
    filter_wh: float     # filtrenin kendi enerjisi
    net_wh: float
    co2_g: float         # net enerji üzerinden
    ratio: float | None  # brüt kazanç / filtre maliyeti


def impact(rows, cfg) -> Impact:
    """rows: .decision, .tokens_est, .filter_ms alanlı kayıtlar."""
    rows = list(rows)
    rejected = [r for r in rows if r["decision"] == "rejected"]
    tokens = sum(r["tokens_est"] + cfg.prompt_tokens + cfg.out_tokens for r in rejected)
    wh_saved = len(rejected) * cfg.wh_per_call
    filter_wh = sum(r["filter_ms"] or 0 for r in rows) * cfg.cpu_watts / 3.6e6  # ms · W → Wh
    net = wh_saved - filter_wh
    return Impact(
        total=len(rows),
        rejected=len(rejected),
        tokens_avoided=tokens,
        wh_saved=wh_saved,
        filter_wh=filter_wh,
        net_wh=net,
        co2_g=net / 1000 * cfg.grid_g_per_kwh,
        ratio=(wh_saved / filter_wh) if filter_wh > 0 else None,
    )
