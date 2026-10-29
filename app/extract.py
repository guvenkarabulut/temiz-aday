import io

from pypdf import PdfReader


class Unreadable(Exception):
    pass


def extract_text(data: bytes, max_pages: int) -> tuple[str, bool]:
    """(metin, OCR kullanıldı mı)"""
    if not data.startswith(b"%PDF"):
        raise Unreadable("PDF değil")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise Unreadable("şifreli PDF")
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages])
    except Unreadable:
        raise
    except Exception as e:  # bozuk dosya: pypdf çok çeşitli hata atar
        raise Unreadable(f"okunamadı: {type(e).__name__}")
    if text.strip():
        return text, False
    text = _ocr(data, min(max_pages, OCR_MAX_PAGES))
    if not text.strip():
        raise Unreadable("metin yok (taranmış görüntü; OCR kapalı ya da sonuç boş)")
    return text, True


OCR_MAX_PAGES = 5  # OCR yavaştır: yalnızca ilk sayfalar
_engine = None


def _ocr(data: bytes, max_pages: int) -> str:
    """İsteğe bağlı yedek: requirements-ocr.txt kuruluysa taranmış PDF'leri okur. Kurulu değilse boş döner.
    Ölçüm: Türkçe harflerde ciddi hata yapar; bu yüzden OCR'lı CV otomatik elenmez."""
    global _engine
    try:
        import numpy as np
        import pypdfium2 as pdfium
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return ""
    try:
        _engine = _engine or RapidOCR()
        pdf = pdfium.PdfDocument(data)
        out = []
        for i in range(min(len(pdf), max_pages)):
            res, _ = _engine(np.array(pdf[i].render(scale=2).to_pil().convert("RGB")))
            out.extend(r[1] for r in (res or []))
        return "\n".join(out)
    except Exception:
        return ""
