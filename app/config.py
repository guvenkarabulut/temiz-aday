import os
from dataclasses import dataclass

from .crypt import load_key


@dataclass(frozen=True)
class Settings:
    db_path: str
    secret: str
    admin_email: str
    admin_password: str
    api_key: str
    model: str
    max_upload_mb: int
    max_pages: int
    wh_per_call: float
    grid_g_per_kwh: float
    cpu_watts: float
    prompt_tokens: int
    out_tokens: int
    data_key: str
    retention_days: int


def settings() -> Settings:
    e = os.environ.get
    return Settings(
        db_path=e("DATABASE_PATH", "data/temizaday.db"),
        secret=e("SECRET_KEY", os.urandom(16).hex()),  # her açılışta değişir: oturumlar düşer
        admin_email=e("ADMIN_EMAIL", "admin@temizaday.local"),
        admin_password=e("ADMIN_PASSWORD", "temizaday"),
        api_key=e("ANTHROPIC_API_KEY", ""),
        model=e("LLM_MODEL", "claude-haiku-5-5"),
        max_upload_mb=int(e("MAX_UPLOAD_MB", "5")),
        max_pages=int(e("MAX_PAGES", "30")),
        wh_per_call=float(e("WH_PER_CALL", "0.3")),
        grid_g_per_kwh=float(e("GRID_G_PER_KWH", "475")),
        cpu_watts=float(e("CPU_WATTS", "15")),
        prompt_tokens=int(e("PROMPT_TOKENS", "300")),
        out_tokens=int(e("OUT_TOKENS", "300")),
        data_key=load_key(e("DATABASE_PATH", "data/temizaday.db")),
        retention_days=int(e("RETENTION_DAYS", "0")),  # 0: CV metnini silme
    )
