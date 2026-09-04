#!/usr/bin/env python3
"""Анализ биржи виртов Black Russia на FunPay.

Считает по каждому серверу: число лотов, число продавцов онлайн, объём
предложения и цены за 1 млн виртов (мин / медиана / среднее), затем
ранжирует серверы и предлагает лучший для продажи.

Использование:
    python3 funpay_virts.py                  # тянет страницу с funpay.com
    python3 funpay_virts.py --file page.html # разбирает сохранённую страницу
    python3 funpay_virts.py --url https://funpay.com/chips/186/
"""

import argparse
import re
import statistics
import sys
import urllib.request
from html import unescape

DEFAULT_URL = "https://funpay.com/chips/186/"  # Black Russia — Mobile, RP: вирты
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

ITEM_RE = re.compile(r'<a\b[^>]*class="[^"]*\btc-item\b[^"]*"[^>]*>.*?</a>', re.S)
ATTR_RE = re.compile(r'(data-[\w-]+)="([^"]*)"')
CELL_RE = re.compile(r'<div[^>]*class="[^"]*\btc-(server|amount|price|user)\b[^"]*"[^>]*>(.*?)</div>\s*(?=<div|</a)', re.S)
TAG_RE = re.compile(r"<[^>]+>")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def text(fragment):
    return unescape(TAG_RE.sub(" ", fragment)).replace("\xa0", " ").strip()


def number(s):
    s = re.sub(r"[^\d.,]", "", s.replace(" ", "")).replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def parse(html):
    rows = []
    for block in ITEM_RE.findall(html):
        attrs = dict(ATTR_RE.findall(block))
        cells = {k: text(v) for k, v in CELL_RE.findall(block)}
        price = number(attrs.get("data-s") or cells.get("price", ""))
        if price is None:
            continue
        rows.append({
            "server": cells.get("server") or attrs.get("data-server") or "(не указан)",
            "seller": cells.get("user", ""),
            "amount": number(cells.get("amount", "")) or 0.0,
            "price": price,
            "online": attrs.get("data-online") == "1",
        })
    return rows


def report(rows, top):
    by_server = {}
    for r in rows:
        by_server.setdefault(r["server"], []).append(r)

    stats = []
    for server, items in by_server.items():
        prices = sorted(i["price"] for i in items)
        stats.append({
            "server": server,
            "lots": len(items),
            "online": sum(1 for i in items if i["online"]),
            "supply": sum(i["amount"] for i in items),
            "min": prices[0],
            "median": statistics.median(prices),
            "mean": statistics.fmean(prices),
        })

    # Для продавца важны и цена, и спрос. Прокси спроса — активность рынка
    # (число лотов), прокси конкуренции — сколько продавцов онлайн держат цену.
    max_median = max(s["median"] for s in stats)
    max_lots = max(s["lots"] for s in stats)
    for s in stats:
        s["score"] = 0.7 * (s["median"] / max_median) + 0.3 * (s["lots"] / max_lots)

    stats.sort(key=lambda s: s["score"], reverse=True)

    head = f'{"сервер":<28}{"лотов":>7}{"онлайн":>8}{"мин ₽":>10}{"медиана":>10}{"средняя":>10}{"скор":>7}'
    print(head)
    print("-" * len(head))
    for s in stats[:top]:
        print(f'{s["server"][:27]:<28}{s["lots"]:>7}{s["online"]:>8}'
              f'{s["min"]:>10.2f}{s["median"]:>10.2f}{s["mean"]:>10.2f}{s["score"]:>7.3f}')

    best = stats[0]
    all_prices = [r["price"] for r in rows]
    print(f'\nВсего лотов: {len(rows)} на {len(stats)} серверах')
    print(f'Средняя цена за 1 млн виртов по всей бирже: {statistics.fmean(all_prices):.2f} ₽ '
          f'(медиана {statistics.median(all_prices):.2f} ₽)')
    print(f'\nЛучший сервер для продажи: {best["server"]}')
    print(f'  средняя цена за 1 млн: {best["mean"]:.2f} ₽ (медиана {best["median"]:.2f} ₽, '
          f'минимум по рынку {best["min"]:.2f} ₽)')
    print(f'  активных лотов: {best["lots"]}, продавцов онлайн: {best["online"]}')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--file", help="разобрать сохранённый HTML вместо загрузки")
    p.add_argument("--top", type=int, default=15, help="сколько серверов показать (по умолчанию 15)")
    args = p.parse_args()

    html = open(args.file, encoding="utf-8").read() if args.file else fetch(args.url)
    rows = parse(html)
    if not rows:
        sys.exit("Лоты не найдены: страница отдала защиту от ботов или вёрстка изменилась. "
                 "Сохраните страницу из браузера и запустите с --file.")
    report(rows, args.top)


if __name__ == "__main__":
    main()
