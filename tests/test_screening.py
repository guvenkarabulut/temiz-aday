from app.screening import Scorer, decide, keyword_present, normalize, parse_keywords

JD = "Go ve PostgreSQL ile yüksek hacimli servisler geliştirecek, Kubernetes bilen kıdemli backend mühendisi"


def test_turkish_normalization():
    assert normalize("İSTANBUL ŞİŞLİ ığdır") == "istanbul sisli igdir"


def test_keyword_short_is_exact_long_is_prefix():
    t = normalize("Golang ve Google deneyimi, go biliyor, Kubernetes'te çalıştı")
    assert keyword_present(t, "go")           # tam kelime "go" var
    assert not keyword_present(normalize("golang google"), "go")  # önek olarak eşleşmemeli
    assert keyword_present(t, "kubernetes")   # ek alsa da önek eşleşir
    assert keyword_present(normalize("Şirket Sistemleri"), "sistem")


def test_relevant_cv_scores_higher_than_junk():
    sc = Scorer(JD, ["go", "postgresql"])
    good, matched, ok = sc.score("Kıdemli backend mühendisi. Go ve PostgreSQL ile yüksek hacimli servisler, Kubernetes.")
    junk, _, ok2 = sc.score("Aşçı olarak restoran mutfağında on yıl çalıştım. Pasta ve ekmek yapımı.")
    assert good > junk + 0.2 and ok and matched == ["go", "postgresql"]
    assert not ok2


def test_trailing_punctuation_does_not_break_short_tokens():
    sc = Scorer("Go ve PostgreSQL geliştirici", ["go"])
    assert sc.score("Biraz Go.")[0] > 0 and sc.score("biraz go")[0] > 0


def test_empty_inputs_do_not_crash():
    assert Scorer("", []).score("x")[0] == 0.0
    assert Scorer(JD, []).score("")[0] == 0.0


def test_decide_bands():
    assert decide(0.05, True, 0.25, 0.08) == "rejected"
    assert decide(0.20, True, 0.25, 0.08) == "review"      # bant içinde
    assert decide(0.40, True, 0.25, 0.08) == "llm"
    assert decide(0.40, False, 0.25, 0.08) == "review"     # zorunlu kelime eksik: elenmez


def test_parse_keywords():
    assert parse_keywords("go, postgresql\n k8s ,") == ["go", "postgresql", "k8s"]
