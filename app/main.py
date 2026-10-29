import csv
import hashlib
import hmac
import io
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import crypt, db, routing
from .config import Settings, settings
from .extract import Unreadable, extract_text
from .metering import impact
from .screening import Scorer, decide, parse_keywords

BASE = Path(__file__).parent
OCR_NOTE = "OCR ile okundu; Türkçe harflerde hata olabilir, bu yüzden elenmedi, incelemeye alındı."
LABELS = {"llm": "→ LLM", "rejected": "× elendi", "review": "◌ inceleme", "unreadable": "! okunamadı"}


def tr(n, d=0):
    """1234.5 → 1.234,5"""
    s = f"{n:,.{d}f}"
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def run_llm(cfg: Settings, app_id: int) -> None:
    """Arka plan: süzgeçten geçen adayı LLM'e gönderir."""
    c = db.connect(cfg.db_path)
    try:
        a = c.execute("SELECT * FROM application WHERE id=?", (app_id,)).fetchone()
        p = c.execute("SELECT * FROM position WHERE id=?", (a["position_id"],)).fetchone() if a else None
        if not a or not p or a["llm_state"] != "pending":
            return
        if not a["cv_text"]:
            return  # saklama süresi dolmuş, metin silinmiş
        r = routing.analyze(cfg, p["title"], p["jd_text"], crypt.dec(cfg, a["cv_text"]))
        if r.ok:
            c.execute(
                "UPDATE application SET llm_state='done', llm_score=?, llm_summary=?, llm_tokens_in=?, llm_tokens_out=? WHERE id=?",
                (r.score, r.summary, r.tokens_in, r.tokens_out, app_id),
            )
        else:
            c.execute("UPDATE application SET llm_state='failed', note=? WHERE id=?", (r.error, app_id))
        c.commit()
    finally:
        c.close()


def llm_state_for(cfg: Settings) -> str:
    return "pending" if cfg.api_key else "disabled"


def process_upload(c, cfg: Settings, p, scorer: Scorer, name: str, data: bytes) -> tuple[str, int | None]:
    """Bir PDF'i işler. (sonuç, llm için application id) döner. Sonuç: dup | unreadable | rejected | review | llm"""
    sha = hashlib.sha256(data).hexdigest()
    if c.execute("SELECT 1 FROM application WHERE position_id=? AND sha256=?", (p["id"], sha)).fetchone():
        return "dup", None
    try:
        text, used_ocr = extract_text(data, cfg.max_pages)
    except Unreadable as e:
        c.execute(
            "INSERT INTO application (position_id, filename, sha256, decision, note) VALUES (?,?,?,?,?)",
            (p["id"], name, sha, "unreadable", str(e)),
        )
        return "unreadable", None
    t0 = time.perf_counter()
    score, matched, must_ok = scorer.score(text)
    ms = (time.perf_counter() - t0) * 1000
    decision = decide(score, must_ok, p["thr"], p["margin"])
    note = ""
    if used_ocr and decision == "rejected":
        decision, note = "review", OCR_NOTE  # OCR Türkçe harflerde hata yapar: otomatik eleme yok
    cur = c.execute(
        """INSERT INTO application (position_id, filename, sha256, cv_text, tokens_est, score, must_ok, matched,
           decision, filter_ms, llm_state, ocr, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (p["id"], name, sha, crypt.enc(cfg, text), routing.estimate_tokens(text), score, int(must_ok), ", ".join(matched),
         decision, ms, llm_state_for(cfg) if decision == "llm" else "none", int(used_ocr), note),
    )
    return decision, (cur.lastrowid if decision == "llm" and cfg.api_key else None)


def redecide(c, cfg: Settings, p) -> list[int]:
    """Eşik ya da anahtar kelime değişince, insan kararı ve LLM'e gitmiş olanlar hariç yeniden karar verir."""
    scorer = Scorer(p["jd_text"], parse_keywords(p["keywords"]))
    ids = []
    rows = c.execute(
        "SELECT * FROM application WHERE position_id=? AND decided_by='filter' AND decision!='unreadable' "
        "AND llm_state IN ('none','disabled') AND cv_text!=''", (p["id"],)
    ).fetchall()
    for a in rows:
        score, matched, must_ok = scorer.score(crypt.dec(cfg, a["cv_text"]))
        d = decide(score, must_ok, p["thr"], p["margin"])
        if a["ocr"] and d == "rejected":
            d = "review"
        state = llm_state_for(cfg) if d == "llm" else "none"
        c.execute(
            "UPDATE application SET score=?, must_ok=?, matched=?, decision=?, llm_state=? WHERE id=?",
            (score, int(must_ok), ", ".join(matched), d, state, a["id"]),
        )
        if d == "llm" and state == "pending":
            ids.append(a["id"])
    return ids


def purge_old(cfg: Settings) -> int:
    """KVKK: saklama süresi dolan CV metinlerini siler (karar ve ölçümler kalır)."""
    if cfg.retention_days <= 0:
        return 0
    c = db.connect(cfg.db_path)
    try:
        n = c.execute("UPDATE application SET cv_text='' WHERE cv_text!='' AND created_at < datetime('now', ?)",
                      (f"-{cfg.retention_days} days",)).rowcount
        c.commit()
        return n
    finally:
        c.close()


def recover_pending(cfg: Settings) -> None:
    """Sunucu yeniden başlayınca yarım kalan LLM çağrılarını tamamlar."""
    c = db.connect(cfg.db_path)
    ids = [r["id"] for r in c.execute("SELECT id FROM application WHERE llm_state='pending'")]
    c.close()
    for i in ids:
        run_llm(cfg, i)


def create_app() -> FastAPI:
    cfg = settings()
    db.init(cfg.db_path)

    @asynccontextmanager
    async def lifespan(_):
        purge_old(cfg)
        if cfg.api_key:
            threading.Thread(target=recover_pending, args=(cfg,), daemon=True).start()
        yield

    async def check_csrf(request: Request):
        if request.method == "POST" and request.url.path != "/login":
            form = await request.form()
            if not hmac.compare_digest(str(form.get("csrf", "")), request.session.get("csrf", "\0")):
                raise HTTPException(403, "Geçersiz istek (CSRF)")

    app = FastAPI(title="TemizAday", lifespan=lifespan, dependencies=[Depends(check_csrf)])
    app.add_middleware(SessionMiddleware, secret_key=cfg.secret, same_site="lax", https_only=False, max_age=86400)
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    tpl = Jinja2Templates(directory=BASE / "templates")
    tpl.env.filters["tr"] = tr
    tpl.env.globals["LABELS"] = LABELS

    def get_db():
        c = db.connect(cfg.db_path)
        try:
            yield c
        finally:
            c.close()

    def need_user(request: Request):
        if not request.session.get("u"):
            raise HTTPException(303, headers={"Location": "/login"})

    auth = [Depends(need_user)]

    def render(request, name, **ctx):
        ctx.update(cfg=cfg, path=request.url.path, msg=request.query_params.get("msg", ""), csrf=request.session.get("csrf", ""))
        return tpl.TemplateResponse(request, name, ctx)

    def position_or_404(c, pid):
        p = c.execute("SELECT * FROM position WHERE id=?", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "Pozisyon bulunamadı")
        return p

    def back(path, msg):
        return RedirectResponse(f"{path}?msg={quote(msg)}", status_code=303)

    # ---- giriş
    @app.get("/login")
    def login_form(request: Request):
        return render(request, "login.html", error="")

    @app.post("/login")
    def login(request: Request, email: str = Form(...), password: str = Form(...)):
        ok = hmac.compare_digest(email.strip().lower(), cfg.admin_email.lower()) & hmac.compare_digest(
            password, cfg.admin_password
        )
        if not ok:
            return render(request, "login.html", error="E-posta ya da parola hatalı.")
        request.session["u"] = cfg.admin_email
        request.session["csrf"] = secrets.token_urlsafe(24)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ---- Yeşil Panel
    @app.get("/", dependencies=auth)
    def dashboard(request: Request, position: int | None = None, c=Depends(get_db)):
        positions = c.execute("SELECT id, title FROM position ORDER BY id").fetchall()
        q, args = "SELECT * FROM application", ()
        if position:
            q, args = q + " WHERE position_id=?", (position,)
        rows = c.execute(q, args).fetchall()
        recent = c.execute(
            "SELECT a.*, p.title FROM application a JOIN position p ON p.id=a.position_id "
            + ("WHERE a.position_id=? " if position else "") + "ORDER BY a.id DESC LIMIT 8", args
        ).fetchall()
        counts = {k: sum(1 for r in rows if r["decision"] == k) for k in LABELS}
        return render(request, "dashboard.html", positions=positions, sel=position, imp=impact(rows, cfg),
                      counts=counts, total=len(rows), recent=recent)

    # ---- Pozisyonlar
    @app.get("/positions", dependencies=auth)
    def positions(request: Request, c=Depends(get_db)):
        rows = c.execute("SELECT * FROM position ORDER BY id DESC").fetchall()
        items = []
        for p in rows:
            apps = c.execute("SELECT decision, tokens_est, filter_ms FROM application WHERE position_id=?", (p["id"],)).fetchall()
            imp = impact(apps, cfg)
            items.append({"p": p, "imp": imp})
        return render(request, "positions.html", items=items)

    @app.post("/positions", dependencies=auth)
    def create_position(title: str = Form(...), dept: str = Form(""), jd_text: str = Form(...),
                        keywords: str = Form(""), c=Depends(get_db)):
        if not title.strip() or not jd_text.strip():
            return back("/positions", "Başlık ve iş tanımı zorunlu.")
        cur = c.execute("INSERT INTO position (title, dept, jd_text, keywords) VALUES (?,?,?,?)",
                        (title.strip(), dept.strip(), jd_text.strip(), keywords.strip()))
        c.commit()
        return back(f"/positions/{cur.lastrowid}", "Pozisyon oluşturuldu. CV'leri yükleyebilirsiniz.")

    @app.get("/positions/{pid}", dependencies=auth)
    def position_detail(request: Request, pid: int, k: str = "all", c=Depends(get_db)):
        p = position_or_404(c, pid)
        rows = c.execute("SELECT * FROM application WHERE position_id=? ORDER BY COALESCE(score,-1) DESC, id", (pid,)).fetchall()
        shown = [r for r in rows if k == "all" or r["decision"] == k]
        return render(request, "position.html", p=p, rows=shown, k=k, imp=impact(rows, cfg),
                      counts={x: sum(1 for r in rows if r["decision"] == x) for x in LABELS}, n=len(rows),
                      keywords=parse_keywords(p["keywords"]))

    @app.post("/positions/{pid}/delete", dependencies=auth)
    def delete_position(pid: int, c=Depends(get_db)):
        position_or_404(c, pid)
        c.execute("DELETE FROM position WHERE id=?", (pid,))  # CV'ler cascade ile silinir
        c.commit()
        return back("/positions", "Pozisyon ve tüm CV'leri silindi.")

    @app.post("/positions/{pid}/upload", dependencies=auth)
    async def upload(pid: int, bg: BackgroundTasks, files: list[UploadFile] = File(...), c=Depends(get_db)):
        p = position_or_404(c, pid)
        scorer = Scorer(p["jd_text"], parse_keywords(p["keywords"]))
        limit = cfg.max_upload_mb * 1024 * 1024
        tally: dict[str, int] = {}
        for f in files:
            if not f.filename:
                continue
            data = await f.read(limit + 1)
            if len(data) > limit:
                tally["büyük"] = tally.get("büyük", 0) + 1
                continue
            res, llm_id = process_upload(c, cfg, p, scorer, f.filename, data)
            tally[res] = tally.get(res, 0) + 1
            if llm_id:
                bg.add_task(run_llm, cfg, llm_id)
        c.commit()
        names = {"rejected": "elendi", "review": "inceleme", "llm": "LLM'e", "unreadable": "okunamadı",
                 "dup": "kopya (atlandı)", "büyük": f"{cfg.max_upload_mb} MB üstü (atlandı)"}
        msg = ", ".join(f"{v} {names[k]}" for k, v in tally.items()) or "Dosya seçilmedi."
        return back(f"/positions/{pid}", f"Yüklendi: {msg}.")

    @app.post("/applications/{aid}/decide", dependencies=auth)
    def human_decide(aid: int, bg: BackgroundTasks, to: str = Form(...), c=Depends(get_db)):
        a = c.execute("SELECT * FROM application WHERE id=?", (aid,)).fetchone()
        if not a or to not in ("llm", "rejected") or a["decision"] != "review":
            raise HTTPException(400, "Yalnızca inceleme bekleyen aday için geçerli")
        state = llm_state_for(cfg) if to == "llm" else "none"
        c.execute("UPDATE application SET decision=?, decided_by='human', llm_state=? WHERE id=?", (to, state, aid))
        c.commit()
        if to == "llm" and cfg.api_key:
            bg.add_task(run_llm, cfg, aid)
        return RedirectResponse(f"/positions/{a['position_id']}?k=review", status_code=303)

    @app.get("/positions/{pid}/export.csv", dependencies=auth)
    def export(pid: int, c=Depends(get_db)):
        p = position_or_404(c, pid)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["dosya", "benzerlik", "eslesen_kelimeler", "karar", "karari_veren", "llm_puan", "llm_ozet"])
        for a in c.execute("SELECT * FROM application WHERE position_id=? ORDER BY id", (pid,)):
            w.writerow([a["filename"], "" if a["score"] is None else round(a["score"], 4), a["matched"], a["decision"],
                        a["decided_by"], a["llm_score"] if a["llm_score"] is not None else "", a["llm_summary"]])
        name = f"pozisyon-{p['id']}.csv"
        return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ---- Filtre Ayarları
    @app.get("/filter", dependencies=auth)
    def filter_page(request: Request, position: int | None = None, thr: float | None = None,
                    margin: float | None = None, c=Depends(get_db)):
        positions = c.execute("SELECT id, title FROM position ORDER BY id").fetchall()
        if not positions:
            return render(request, "filter.html", positions=[], p=None)
        p = position_or_404(c, position or positions[0]["id"])
        t = p["thr"] if thr is None else max(0.0, min(1.0, thr))
        m = p["margin"] if margin is None else max(0.0, min(0.5, margin))
        rows = c.execute("SELECT * FROM application WHERE position_id=? AND decision!='unreadable'", (p["id"],)).fetchall()
        prev = {"rejected": 0, "review": 0, "llm": 0}
        for r in rows:
            prev[decide(r["score"] or 0.0, bool(r["must_ok"]), t, m)] += 1
        return render(request, "filter.html", positions=positions, p=p, t=t, m=m, prev=prev, n=len(rows))

    @app.post("/filter", dependencies=auth)
    def filter_apply(bg: BackgroundTasks, position: int = Form(...), thr: float = Form(...), margin: float = Form(...),
                     keywords: str = Form(""), c=Depends(get_db)):
        position_or_404(c, position)
        c.execute("UPDATE position SET thr=?, margin=?, keywords=? WHERE id=?",
                  (max(0.0, min(1.0, thr)), max(0.0, min(0.5, margin)), keywords.strip(), position))
        p = position_or_404(c, position)
        ids = redecide(c, cfg, p)
        c.commit()
        for i in ids:
            bg.add_task(run_llm, cfg, i)
        return back(f"/filter?position={position}", "Ayarlar uygulandı, mevcut CV'ler yeniden değerlendirildi.")

    # ---- LLM Ayarları, rapor
    @app.get("/llm", dependencies=auth)
    def llm_page(request: Request, c=Depends(get_db)):
        s = c.execute(
            "SELECT llm_state, COUNT(*) n, SUM(llm_tokens_in) ti, SUM(llm_tokens_out) to_ FROM application GROUP BY llm_state"
        ).fetchall()
        return render(request, "llm.html", states={r["llm_state"]: r for r in s}, has_key=bool(cfg.api_key))

    @app.get("/report", dependencies=auth)
    def report(request: Request, position: int | None = None, c=Depends(get_db)):
        positions = c.execute("SELECT id, title FROM position ORDER BY id").fetchall()
        q, args = "SELECT * FROM application", ()
        if position:
            q, args = q + " WHERE position_id=?", (position,)
        return render(request, "report.html", positions=positions, sel=position, imp=impact(c.execute(q, args).fetchall(), cfg))

    return app


app = create_app()
