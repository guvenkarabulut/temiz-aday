"""CPU ön filtresi. Saf fonksiyonlar: G/Ç yok, ağ yok."""
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Python'un lower()'ı "İ" ve "I" harflerini Türkçe için bozar; önce katla, sonra küçült.
_FOLD = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iissgguuoocc")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_FOLD).lower()).strip()


def keyword_present(norm_text: str, keyword: str) -> bool:
    kw = normalize(keyword)
    if not kw:
        return True
    # shortcut: kısa kelimeler ("go") tam kelime, uzunlar önek eşleşir (Türkçe ekleri için).
    tail = r"(?![a-z0-9])" if len(kw) <= 2 else ""
    return re.search(r"(?<![a-z0-9])" + re.escape(kw) + tail, norm_text) is not None


def parse_keywords(raw: str) -> list[str]:
    return [k.strip() for k in re.split(r"[,\n]", raw or "") if k.strip()]


# Sık geçen Türkçe bağlaç ve edatlar: benzerliğe gürültü katar.
_STOP = set("ve ile bir bu su icin olarak da de ki mi gibi daha cok en ya veya ama ancak kadar sonra once tum her bazi yil".split())


def _analyzer(text: str) -> list[str]:
    # Türkçe ekli kelimeleri önek (ilk 5 harf) ile eşleştirir; ardışık ikililer de eklenir.
    # shortcut: gerçek morfolojik analiz yok. Denendi: karakter n-gram'ı ortak ek gürültüsünden dolayı
    # alakasız CV'lere ~0,25 taban skor verdi; önek yöntemi 0,0 verdi.
    toks = [w[:5] for w in re.findall(r"[a-z0-9+#]+(?:\.[a-z0-9]+)*", text) if w not in _STOP and (len(w) > 2 or w in ("go", "c", "r"))]
    return toks + [a + " " + b for a, b in zip(toks, toks[1:])]


class Scorer:
    """İş tanımına göre bir kez eğitilir, her CV için kosinüs benzerliği üretir."""

    def __init__(self, jd_text: str, keywords: list[str]):
        self.keywords = keywords
        ref = normalize(jd_text + " " + " ".join(keywords))
        self._vec = None
        if _analyzer(ref):
            # sublinear_tf: aynı kelimeyi tekrarlayarak puan şişirmeyi sönümler.
            self._vec = TfidfVectorizer(analyzer=_analyzer, sublinear_tf=True)
            self._ref = self._vec.fit_transform([ref])

    def score(self, cv_text: str) -> tuple[float, list[str], bool]:
        """(benzerlik 0-1, eşleşen anahtar kelimeler, zorunlu kelimelerin hepsi var mı)"""
        cv = normalize(cv_text)
        matched = [k for k in self.keywords if keyword_present(cv, k)]
        must_ok = len(matched) == len(self.keywords)
        if not cv or self._vec is None:
            return 0.0, matched, must_ok
        sim = float(cosine_similarity(self._vec.transform([cv]), self._ref)[0, 0])
        return sim, matched, must_ok


def decide(score: float, must_ok: bool, thr: float, margin: float) -> str:
    """rejected | review | llm. Sınıra yakın ya da zorunlu kelimesi eksik aday elenmez, incelemeye düşer."""
    if score < thr - margin:
        return "rejected"
    if score >= thr + margin and must_ok:
        return "llm"
    return "review"
