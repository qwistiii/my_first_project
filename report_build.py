import json, re, statistics
SP = "/tmp/claude-0/-home-user-my-first-project/084f4f98-a17a-5cfa-916a-ba274181e384/scratchpad"
d = json.load(open(f"{SP}/report.json"))
S, M = d["servers"], d["meta"]

def rnd(v):
    """Медианы минимального заказа тянут за собой мусор плавающей точки."""
    return None if v is None else round(v, 1)

# Игровой онлайн снят с blackrussia.online — числа лотов на бирже он не
# заменяет, а опровергает: населённые серверы стоят дешевле.
GO = {g["num"]: g for g in json.load(open(f"{SP}/game_online.json"))}

rows = []
for x in S:
    g = GO.get(int(re.match(r"\u2116(\d+)", x["server"]).group(1)))
    rows.append({
        "s": x["server"], "n": x["lots"], "on": x["online"],
        "p25": round(x["p25"], 2),
        "po": round(x["p25_online"], 2) if x["p25_online"] else None,
        "med": round(x["median"], 2), "sup": round(x["supply"]),
        "mo": rnd(x["min_order_med"]), "lo": rnd(x["min_order_lo"]), "hi": rnd(x["min_order_hi"]),
        "go": g["online"] if g else None, "cap": g["cap"] if g else None,
        "band": [{"p": round(b["price"], 2), "u": b["seller"], "a": round(b["amount"]),
                  "r": b["reviews"], "st": b["stars"], "m": rnd(b["min_order"])}
                 for b in x["band"]],
    })
for r in rows:
    # Выручка фармера ≈ скорость фарма × цена; игровой онлайн — прокси первого.
    r["idx"] = round(r["go"] * r["po"] / 1000) if (r["go"] and r["po"]) else None
rows.sort(key=lambda r: -(r["idx"] or -1))
po = [r["po"] for r in rows if r["po"]]
prem = statistics.median([r["po"] - r["p25"] for r in rows if r["po"]])
mo = [r["mo"] for r in rows if r["mo"]]
meta = {
    "ts": M["ts"], "lots": M["lots_total"], "used": M["lots_used"], "servers": M["servers"],
    "cards": M["cards_read"], "cardsOk": M["cards_with_min"],
    "mean": round(M["mean"], 2), "median": round(M["median"], 2),
    "meanOn": round(M["mean_online"], 2), "medianOn": round(M["median_online"], 2),
    "best": rows[0]["s"], "bestPo": rows[0]["po"],
    "bestGo": rows[0]["go"], "bestIdx": rows[0]["idx"],
    "priceKing": max((r for r in rows if r["po"]), key=lambda r: r["po"])["s"],
    "priceKingPo": max(po), "priceKingGo": max((r for r in rows if r["po"]), key=lambda r: r["po"])["go"],
    "goLo": min(r["go"] for r in rows if r["go"]), "goHi": max(r["go"] for r in rows if r["go"]),
    "goTotal": sum(r["go"] for r in rows if r["go"]),
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
