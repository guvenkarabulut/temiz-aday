import re

from app import routing
from tests.conftest import make_pdf

JD = "Go ve PostgreSQL ile yüksek hacimli servisler geliştirecek, Kubernetes bilen kıdemli backend mühendisi"
GOOD = "Kıdemli backend mühendisi. Go ve PostgreSQL ile yüksek hacimli servisler geliştirdim. Kubernetes ve Docker."
JUNK = "Restoranda aşçı olarak on yıl çalıştım. Pasta, ekmek ve tatlı yapımı konusunda deneyimliyim."


def new_position(c, kw="go, postgresql"):
    r = c.post("/positions", data={"title": "Backend", "dept": "Müh", "jd_text": JD, "keywords": kw})
    assert r.status_code == 303
    return int(re.search(r"/positions/(\d+)", r.headers["location"]).group(1))


def upload(c, pid, files):
    return c.post(f"/positions/{pid}/upload", files=[("files", (n, d, "application/pdf")) for n, d in files])


def test_requires_login(client):
    for path in ["/", "/positions", "/filter", "/llm", "/report"]:
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/login").status_code == 200


def test_wrong_password(client):
    r = client.post("/login", data={"email": "a@b.c", "password": "no"})
    assert r.status_code == 200 and "hatalı" in r.text


def test_full_flow(logged):
    c = logged
    pid = new_position(c)
    r = upload(c, pid, [("iyi.pdf", make_pdf(GOOD)), ("cop.pdf", make_pdf(JUNK)), ("bozuk.pdf", b"bu pdf degil"),
                        ("iyi-kopya.pdf", make_pdf(GOOD))])
    msg = r.headers["location"]
    assert "okunamad" in msg or "okunamad" in __import__("urllib.parse", fromlist=["x"]).unquote(msg)
    page = c.get(f"/positions/{pid}")
    assert page.status_code == 200
    assert "iyi.pdf" in page.text and "cop.pdf" in page.text and "bozuk.pdf" in page.text
    assert "iyi-kopya.pdf" not in page.text            # aynı içerik: kopya atlandı
    # junk elendi, iyi LLM'e (LLM bağlı değil)
    assert 'chip rejected">× elendi' in page.text and "LLM bağlı değil" in page.text
    assert "! okunamadı" in page.text
    # panel, rapor, csv
    d = c.get("/")
    assert d.status_code == 200 and "Kurtarılan enerji" in d.text
    assert "Wh" in c.get("/report").text
    csv = c.get(f"/positions/{pid}/export.csv")
    assert csv.status_code == 200 and "iyi.pdf" in csv.text and "dosya,benzerlik" in csv.text
    assert c.get("/llm").status_code == 200 and c.get("/positions").status_code == 200


def test_filter_preview_and_apply(logged):
    c = logged
    pid = new_position(c)
    upload(c, pid, [("iyi.pdf", make_pdf(GOOD)), ("cop.pdf", make_pdf(JUNK))])
    # önizleme kaydetmez
    prev = c.get(f"/filter?position={pid}&thr=0.9&margin=0")
    assert prev.status_code == 200
    assert c.get(f"/positions/{pid}?k=llm").text.count("iyi.pdf") == 1
    # çok sıkı eşik uygulanınca iyi CV de elenir
    r = c.post("/filter", data={"position": pid, "thr": "0.95", "margin": "0", "keywords": "go, postgresql"})
    assert r.status_code == 303
    assert "iyi.pdf" not in c.get(f"/positions/{pid}?k=llm").text
    assert "iyi.pdf" in c.get(f"/positions/{pid}?k=rejected").text


def test_review_human_decision(logged):
    c = logged
    pid = new_position(c, kw="go, postgresql, rust")   # 'rust' yok -> iyi CV incelemeye düşer
    upload(c, pid, [("iyi.pdf", make_pdf(GOOD))])
    rev = c.get(f"/positions/{pid}?k=review")
    assert "iyi.pdf" in rev.text and "LLM'e gönder" in rev.text
    aid = int(re.search(r"/applications/(\d+)/decide", rev.text).group(1))
    assert c.post(f"/applications/{aid}/decide", data={"to": "rejected"}).status_code == 303
    assert "sizin kararınız" in c.get(f"/positions/{pid}?k=rejected").text
    # artık inceleme değil: ikinci karar reddedilir
    assert c.post(f"/applications/{aid}/decide", data={"to": "llm"}).status_code == 400


def test_llm_background(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "l.db"))
    monkeypatch.setenv("ADMIN_EMAIL", "a@b.c"); monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen = {}

    def fake(cfg, title, jd, cv):
        seen["cv"] = cv
        return routing.LlmResult(True, 77, "Güçlü aday.", 1200, 80)
    monkeypatch.setattr(routing, "analyze", fake)
    from fastapi.testclient import TestClient
    from app.main import create_app
    c = TestClient(create_app(), follow_redirects=False)
    from tests.conftest import login
    login(c)
    pid = new_position(c)
    upload(c, pid, [("iyi.pdf", make_pdf(GOOD))])
    page = c.get(f"/positions/{pid}")
    assert "77/100" in page.text and "Güçlü aday." in page.text
    assert "BAĞLI" in c.get("/llm").text and "1.200" in c.get("/llm").text
    assert "backend" in seen["cv"].lower()


def test_upload_limits(logged, monkeypatch):
    c = logged
    pid = new_position(c)
    big = b"%PDF" + b"0" * (5 * 1024 * 1024 + 10)
    r = upload(c, pid, [("buyuk.pdf", big)])
    from urllib.parse import unquote
    assert "MB üstü" in unquote(r.headers["location"])


def test_csrf_required(logged):
    c = logged
    assert c.raw_post("/positions", data={"title": "x", "jd_text": "y"}).status_code == 403
    assert c.raw_post("/positions", data={"title": "x", "jd_text": "y", "csrf": "yanlis"}).status_code == 403
    assert c.raw_post("/logout").status_code == 403


def test_cv_text_encrypted_at_rest(logged, tmp_path):
    import sqlite3, os
    c = logged
    pid = new_position(c)
    upload(c, pid, [("iyi.pdf", make_pdf(GOOD))])
    raw = sqlite3.connect(os.environ["DATABASE_PATH"]).execute("SELECT cv_text FROM application").fetchone()[0]
    assert raw and "backend" not in raw.lower() and "PostgreSQL" not in raw
    assert (tmp_path / ".datakey").exists() and oct((tmp_path / ".datakey").stat().st_mode)[-3:] == "600"
    # şifreli olsa da süzgeç yeniden değerlendirebilir
    assert c.post("/filter", data={"position": pid, "thr": "0.25", "margin": "0.08", "keywords": "go"}).status_code == 303
    assert "iyi.pdf" in c.get(f"/positions/{pid}?k=llm").text


def test_delete_position_cascades(logged):
    import sqlite3, os
    c = logged
    pid = new_position(c)
    upload(c, pid, [("iyi.pdf", make_pdf(GOOD))])
    assert c.post(f"/positions/{pid}/delete").status_code == 303
    db = sqlite3.connect(os.environ["DATABASE_PATH"])
    assert db.execute("SELECT COUNT(*) FROM application").fetchone()[0] == 0
    assert c.get(f"/positions/{pid}").status_code == 404


def test_purge_old_and_recover_pending(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "p.db")); monkeypatch.setenv("RETENTION_DAYS", "30")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    from app import main, db
    from app.config import settings
    cfg = settings(); db.init(cfg.db_path)
    c = db.connect(cfg.db_path)
    c.execute("INSERT INTO position (id,title,jd_text) VALUES (1,'p','jd')")
    from app import crypt
    for sha, age, st in [("a", "-40 days", "none"), ("b", "-1 days", "pending")]:
        c.execute("INSERT INTO application (position_id,filename,sha256,cv_text,decision,llm_state,created_at) "
                  "VALUES (1,?,?,?,?,?,datetime('now',?))", (sha, sha, crypt.enc(cfg, "metin"), "llm", st, age))
    c.commit()
    assert main.purge_old(cfg) == 1
    monkeypatch.setattr(routing, "analyze", lambda *a, **k: routing.LlmResult(True, 60, "tamam", 5, 5))
    main.recover_pending(cfg)
    rows = {r["sha256"]: r for r in c.execute("SELECT * FROM application")}
    assert rows["a"]["cv_text"] == "" and rows["b"]["llm_state"] == "done" and rows["b"]["llm_score"] == 60


def test_anthropic_request_shape_and_parsing():
    """Gerçek ağ yok: SDK'nın ürettiği HTTP isteğini ve yanıt ayrıştırmayı sahte taşıyıcıyla doğrular."""
    import json, httpx2 as httpx
    from types import SimpleNamespace
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["key"], seen["body"] = str(req.url), (req.headers.get("x-api-key") or req.headers.get("authorization", "").removeprefix("Bearer ")), json.loads(req.content)
        return httpx.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant", "model": "m",
                                         "content": [{"type": "text", "text": '{"puan": 81, "ozet": "Güçlü eşleşme."}'}],
                                         "stop_reason": "end_turn", "stop_sequence": None,
                                         "usage": {"input_tokens": 321, "output_tokens": 45}})
    cfg = SimpleNamespace(api_key="sk-test", model="claude-haiku-5-5")
    r = routing.analyze(cfg, "Backend", "Go", "Ali ali@x.com 0532 123 45 67 Go PostgreSQL",
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert r.ok and r.score == 81 and r.tokens_in == 321 and r.tokens_out == 45
    assert seen["url"].endswith("/v1/messages") and seen["key"] == "sk-test"
    b = seen["body"]
    assert b["model"] == "claude-haiku-5-5" and b["max_tokens"] == 400 and "güvenilmeyen veri" in b["system"]
    u = b["messages"][0]["content"]
    assert "<cv>" in u and "ali@x.com" not in u and "0532" not in u


def test_scanned_pdf_via_ocr(logged):
    import io, pytest
    pytest.importorskip("rapidocr_onnxruntime"); pytest.importorskip("pypdfium2")
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1240, 300), "white"); d = ImageDraw.Draw(img); f = ImageFont.load_default(size=44)
    d.text((40, 40), "Backend developer Go and PostgreSQL", font=f, fill="black")
    d.text((40, 120), "Kubernetes services developer", font=f, fill="black")
    buf = io.BytesIO(); img.save(buf, "PDF")
    c = logged
    pid = new_position(c)
    upload(c, pid, [("tarama.pdf", buf.getvalue())])
    page = c.get(f"/positions/{pid}").text
    assert "tarama.pdf" in page and "okunamadı · 0" in page      # OCR ile okundu, okunamayan yok
    assert "go, postgresql" in page                                # anahtar kelimeler yakalandı


def test_ocr_documents_are_never_auto_rejected(logged):
    import io, pytest
    pytest.importorskip("rapidocr_onnxruntime"); pytest.importorskip("pypdfium2")
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1240, 200), "white"); d = ImageDraw.Draw(img)
    d.text((40, 40), "Pastry chef bakery bread cake", font=ImageFont.load_default(size=44), fill="black")
    buf = io.BytesIO(); img.save(buf, "PDF")
    c = logged
    pid = new_position(c)
    upload(c, pid, [("tarama-alakasiz.pdf", buf.getvalue())])
    assert "tarama-alakasiz.pdf" not in c.get(f"/positions/{pid}?k=rejected").text
    rev = c.get(f"/positions/{pid}?k=review").text
    assert "tarama-alakasiz.pdf" in rev and "OCR ile okundu" in rev
