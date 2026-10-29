import pytest


def make_pdf(text: str) -> bytes:
    """Metin içeren en küçük PDF (pypdf eksik xref'i kendisi onarır)."""
    esc = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 50 750 Td ({esc}) Tj ET".encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return out


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("ADMIN_EMAIL", "a@b.c")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("SECRET_KEY", "x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    from app.main import create_app
    c = TestClient(create_app(), follow_redirects=False)
    return c


def login(client):
    """Giriş yapar; sonraki tüm POST'lara CSRF belirtecini kendisi ekler."""
    r = client.post("/login", data={"email": "a@b.c", "password": "pw"})
    assert r.status_code == 303
    import re
    client.csrf = re.search(r'name="csrf" value="([^"]+)"', client.get("/positions").text).group(1)
    client.raw_post = client.post

    def post(url, data=None, **kw):
        return client.raw_post(url, data=dict(data or {}, csrf=client.csrf), **kw)
    client.post = post
    return client


@pytest.fixture
def logged(client):
    return login(client)
