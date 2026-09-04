import sys, json, time, statistics
sys.path.insert(0, "/home/user/my_first_project")
from funpay_virts import (get, parse_listing, server_stats, fetch_cards,
                          drop_junk, quantile, LISTING_URL)

SP = "/tmp/claude-0/-home-user-my-first-project/084f4f98-a17a-5cfa-916a-ba274181e384/scratchpad"
t0 = time.time()
rows = parse_listing(get(LISTING_URL))
stats = server_stats(rows)
print(f"лотов {len(rows)}, серверов {len(stats)}")

# Карточки: 10 самых дешёвых ОНЛАЙН-лотов на каждом сервере — это и есть
# полоса, в которой продавец реально конкурирует.
band = []
for s in stats:
    online = sorted([i for i in s["items"] if i["online"]], key=lambda r: r["price"])
    s["_band"] = online[:10]
    band.extend(s["_band"])
print(f"карточек к дочитыванию: {len(band)}")

cards = fetch_cards(band, workers=6)
by_url = {c["url"]: c for c in cards}
errs = sum(1 for c in cards if c["error"])
got = sum(1 for c in cards if c["min_order"] is not None)
print(f"дочитано {len(cards)}, с минимумом {got}, ошибок {errs}, {time.time()-t0:.0f} c")

out = []
for s in stats:
    b = [by_url.get(r["url"], {}) for r in s["_band"]]
    mins = [c["min_order"] for c in b if c.get("min_order")]
    out.append({
        "server": s["server"], "lots": s["lots"], "online": s["online"],
        "p10": s["p10"], "p25": s["p25"], "p25_online": s["p25_online"],
        "median": s["median"], "mean": s["mean"], "supply": s["supply"],
        "min_order_med": statistics.median(mins) if mins else None,
        "min_order_lo": min(mins) if mins else None,
        "min_order_hi": max(mins) if mins else None,
        "band": [{"price": r["price"], "seller": r["seller"], "amount": r["amount"],
                  "stars": r["stars"], "reviews": r["reviews"],
                  "min_order": by_url.get(r["url"], {}).get("min_order")}
                 for r in s["_band"]],
    })
allp = [r["price"] for s in stats for r in s["items"]]
allon = [r["price"] for s in stats for r in s["items"] if r["online"]]
meta = {"lots_total": len(rows), "lots_used": sum(s["lots"] for s in stats),
        "servers": len(stats), "cards_read": len(cards), "cards_with_min": got,
        "mean": statistics.fmean(allp), "median": statistics.median(allp),
        "mean_online": statistics.fmean(allon), "median_online": statistics.median(allon),
        "ts": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
json.dump({"meta": meta, "servers": out}, open(f"{SP}/report.json", "w"), ensure_ascii=False)
print("meta:", json.dumps(meta, ensure_ascii=False))
