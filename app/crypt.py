"""CV metnini diskte şifreler (Fernet). Anahtar: DATA_KEY ortam değişkeni, yoksa data/.datakey dosyası (0600)."""
import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet


def load_key(db_path: str) -> str:
    if os.environ.get("DATA_KEY"):
        return os.environ["DATA_KEY"]
    f = Path(db_path).parent / ".datakey"
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.exists():
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(Fernet.generate_key().decode())
    return f.read_text().strip()


@lru_cache(maxsize=4)
def _fernet(key: str) -> Fernet:
    return Fernet(key.encode())


def enc(cfg, text: str) -> str:
    return _fernet(cfg.data_key).encrypt(text.encode()).decode() if text else ""


def dec(cfg, token: str) -> str:
    return _fernet(cfg.data_key).decrypt(token.encode()).decode() if token else ""
