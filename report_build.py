import json, statistics
SP = "/tmp/claude-0/-home-user-my-first-project/084f4f98-a17a-5cfa-916a-ba274181e384/scratchpad"
d = json.load(open(f"{SP}/report.json"))
S, M = d["servers"], d["meta"]

def rnd(v):
    """Медианы минимального заказа тянут за собой мусор плавающей точки."""
    return None if v is None else round(v, 1)

rows = []
for x in S:
    rows.append({
        "s": x["server"], "n": x["lots"], "on": x["online"],
        "p25": round(x["p25"], 2),
        "po": round(x["p25_online"], 2) if x["p25_online"] else None,
        "med": round(x["median"], 2), "sup": round(x["supply"]),
        "mo": rnd(x["min_order_med"]), "lo": rnd(x["min_order_lo"]), "hi": rnd(x["min_order_hi"]),
        "band": [{"p": round(b["price"], 2), "u": b["seller"], "a": round(b["amount"]),
                  "r": b["reviews"], "st": b["stars"], "m": rnd(b["min_order"])}
                 for b in x["band"]],
    })
po = [r["po"] for r in rows if r["po"]]
prem = statistics.median([r["po"] - r["p25"] for r in rows if r["po"]])
mo = [r["mo"] for r in rows if r["mo"]]
meta = {
    "ts": M["ts"], "lots": M["lots_total"], "used": M["lots_used"], "servers": M["servers"],
    "cards": M["cards_read"], "cardsOk": M["cards_with_min"],
    "mean": round(M["mean"], 2), "median": round(M["median"], 2),
    "meanOn": round(M["mean_online"], 2), "medianOn": round(M["median_online"], 2),
    "best": rows[0]["s"], "bestPo": rows[0]["po"],
    "near": sum(1 for p in po if p >= max(po) * 0.95),
    "prem": round(prem, 2), "poLo": round(min(po), 2), "poHi": round(max(po), 2),
    "moMed": rnd(statistics.median(mo)), "moLo": rnd(min(mo)), "moHi": rnd(max(mo)),
}
payload = json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, separators=(",", ":"))
html = open(f"{SP}/report/template.html", encoding="utf-8").read()
html = html.replace("/*__DATA__*/", payload)
open(f"{SP}/report/index.html", "w", encoding="utf-8").write(html)
print("meta:", json.dumps(meta, ensure_ascii=False))
print("размер:", len(html) // 1024, "КБ")
