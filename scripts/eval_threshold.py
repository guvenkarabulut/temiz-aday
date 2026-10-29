"""Etiketli CV kümesinde eşik taraması: recall (nitelikli aday elenmesin) ve elenme oranı.
Kullanım: PYTHONPATH=. .venv/bin/python scripts/eval_threshold.py [eval/labelled.json] [--margin 0.08]
"""
import json
import sys

from app.screening import Scorer, decide, parse_keywords

path = next((a for a in sys.argv[1:] if a.endswith(".json")), "eval/labelled.json")
margin = float(sys.argv[sys.argv.index("--margin") + 1]) if "--margin" in sys.argv else 0.08
data = json.load(open(path))
rows = []  # (etiket, skor, zorunlu_kelime_tam)
for p in data["positions"]:
    sc = Scorer(p["jd"], parse_keywords(p["keywords"]))
    for label, text in p["cvs"]:
        s, _, ok = sc.score(text)
        rows.append((label, s, ok))
pos = [r for r in rows if r[0] == 1]
neg = [r for r in rows if r[0] == 0]
print(data.get("note", ""))
print(f"{len(pos)} nitelikli, {len(neg)} niteliksiz CV · bant ±{margin}\n")
print("eşik   recall(elenmeyen nitelikli)   LLM'e giden   elenen niteliksiz   LLM'e giden niteliksiz")
for t in [x / 100 for x in range(5, 61, 5)]:
    kept = lambda r: decide(r[1], r[2], t, margin) != "rejected"
    to_llm = lambda r: decide(r[1], r[2], t, margin) == "llm"
    rec = sum(kept(r) for r in pos) / len(pos)
    print(f"{t:.2f}   {rec:>6.0%}                       {sum(to_llm(r) for r in rows):>3}/{len(rows)}"
          f"          {sum(not kept(r) for r in neg)/len(neg):>6.0%}              {sum(to_llm(r) for r in neg):>3}")
print("\nSkor aralıkları: nitelikli", f"{min(r[1] for r in pos):.2f}–{max(r[1] for r in pos):.2f}",
      "· niteliksiz", f"{min(r[1] for r in neg):.2f}–{max(r[1] for r in neg):.2f}")
