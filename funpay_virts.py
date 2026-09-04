#!/usr/bin/env python3
"""Анализ биржи виртов Black Russia на FunPay (funpay.com/chips/186/).

Зачем не просто «средняя цена»: медиана почти у всех серверов одинаковая,
потому что крупные продавцы выставляют один и тот же прайс сразу на все
серверы. Серверы различает только конкурентный низ списка — покупатель
сортирует по возрастанию цены, поэтому продавец зарабатывает столько,
сколько стоят лоты в нижней четверти, а не по медиане.

Свободного описания у лотов-чипсов нет, условия лежат в карточке оффера:
минимальный заказ, способы оплаты, рейтинг продавца. Скрипт дочитывает
карточки конкурентной полосы у лучших серверов и показывает эту разницу.

Использование:
    python3 funpay_virts.py                  # полный разбор с дочитыванием карточек
    python3 funpay_virts.py --no-cards       # только таблица, без карточек
    python3 funpay_virts.py --file page.html # разобрать сохранённую страницу
"""

import argparse
import concurrent.futures as futures
import re
import statistics
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from html import unescape

LISTING_URL = "https://funpay.com/chips/186/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
# Без этой куки FunPay отдаёт прайс в долларах.
HEADERS = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9", "Cookie": "cy=rub"}

ROW_RE = re.compile(r'<a\b[^>]*class="[^"]*\btc-item\b[^"]*"[^>]*>.*?</a>', re.S)
HREF_RE = re.compile(r'href="([^"]+)"')
SERVER_RE = re.compile(r'<div class="tc-server[^"]*">([^<]*)</div>')
# data-s у tc-price — ключ сортировки, а не цена: у промо-лотов там 0,1,2…
PRICE_RE = re.compile(r'<div class="tc-price"[^>]*>\s*<div>([\d.,\s]+)<span class="unit">')
AMOUNT_RE = re.compile(r'<div class="tc-amount"[^>]*data-s="([\d.]+)"')
NAME_RE = re.compile(r'<div class="media-user-name">\s*([^<]*)</div>')
REVIEWS_RE = re.compile(r'<span class="rating-mini-count">(\d+)</span>')
STARS_RE = re.compile(r'rating-stars rating-(\d)')
TAG_RE = re.compile(r"<[^>]+>")

MIN_ORDER_RE = re.compile(r"Минимум\s+([\d.,]+)\s*кк", re.I)
PAY_RE = re.compile(r"(Банковская карта[^\n<]*|СБП[^\n<]*|Криптовалюта[^\n<]*|"
                    r"Visa[^\n<]*|Mastercard[^\n<]*)", re.I)


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def num(s):
    m = re.search(r"\d+(?:[.,]\d+)?", s.replace(" ", "").replace("\xa0", ""))
    return float(m.group().replace(",", ".")) if m else None


def parse_listing(html):
    rows = []
    for block in ROW_RE.findall(html):
        server = SERVER_RE.search(block)
        price = PRICE_RE.search(block)
        if not (server and price):
            continue
        amount = AMOUNT_RE.search(block)
        name = NAME_RE.search(block)
        stars = STARS_RE.search(block)
        reviews = REVIEWS_RE.search(block)
        href = HREF_RE.search(block)
        rows.append({
            "server": unescape(server.group(1)).strip(),
            "price": num(price.group(1)),
            "amount": float(amount.group(1)) if amount else 0.0,
            "seller": unescape(name.group(1)).strip() if name else "",
            "stars": int(stars.group(1)) if stars else None,
            "reviews": int(reviews.group(1)) if reviews else 0,
            "online": 'data-online="1"' in block,
            "url": unescape(href.group(1)) if href else "",
        })
    return rows


def quantile(values, pct):
    v = sorted(values)
    k = (len(v) - 1) * pct / 100
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


JUNK_FACTOR = 3.0


def drop_junk(items):
    """Отсеивает лоты-заглушки, стоящие кратно дороже рынка сервера.

    Это ~1% лотов с ценой в десятки и сотни раз выше медианы, обычно с
    нулём отзывов. Купить по ним никто не может, но в среднем они дают
    перекос в полтора раза, поэтому в расчёт цены не идут.

    Отсекаем по кратности медиане, а не по остатку на складе: заглушки
    попадаются и с наличием в сотни тысяч кк, так что объём их не выдаёт.
    """
    if not items:
        return []
    ceiling = statistics.median([i["price"] for i in items]) * JUNK_FACTOR
    return [i for i in items if i["price"] <= ceiling]


def server_stats(rows, min_lots=10):
    by = defaultdict(list)
    for r in rows:
        by[r["server"]].append(r)

    stats = []
    for server, items in by.items():
        items = drop_junk(items)
        if len(items) < min_lots:
            continue
        prices = [i["price"] for i in items]
        stats.append({
            "server": server,
            "items": items,
            "lots": len(items),
            "online": sum(1 for i in items if i["online"]),
            "p10": quantile(prices, 10),
            "p25": quantile(prices, 25),
            "median": statistics.median(prices),
            "mean": statistics.fmean(prices),
            "supply": sum(i["amount"] for i in items),
        })
    # Ранжируем по конкурентной цене: медиана у серверов почти совпадает и
    # ничего не различает, а p25 — то, за что реально уходят вирты.
    stats.sort(key=lambda s: -s["p25"])
    return stats


def read_card(row):
    """Условия сделки из карточки оффера: их нет в строке таблицы."""
    try:
        html = get(row["url"])
    except (urllib.error.URLError, OSError) as exc:
        return {**row, "min_order": None, "pay": [], "error": str(exc)[:60]}
    text = unescape(TAG_RE.sub("\n", html)).replace("\xa0", " ")
    mo = MIN_ORDER_RE.search(text)
    pays = []
    for p in PAY_RE.findall(text):
        p = re.sub(r"\s+", " ", p).strip()
        if p not in pays:
            pays.append(p)
    return {**row, "min_order": num(mo.group(1)) if mo else None,
            "pay": pays[:3], "error": None}


def fetch_cards(rows, workers=5):
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(read_card, rows))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", help="разобрать сохранённый HTML вместо загрузки")
    p.add_argument("--top", type=int, default=12, help="серверов в таблице")
    p.add_argument("--cards", type=int, default=20,
                   help="сколько самых дешёвых карточек дочитать у лидера")
    p.add_argument("--no-cards", action="store_true", help="не ходить в карточки офферов")
    args = p.parse_args()

    html = open(args.file, encoding="utf-8").read() if args.file else get(LISTING_URL)
    rows = parse_listing(html)
    if not rows:
        sys.exit("Лоты не найдены: вёрстка страницы изменилась.")

    stats = server_stats(rows)
    kept = sum(s["lots"] for s in stats)
    print(f'Разобрано {len(rows)} лотов на {len(stats)} серверах; '
          f'в расчёт цены взято {kept}, отсеяно заглушек {len(rows) - kept}.\n')

    head = (f'{"сервер":<20}{"лотов":>6}{"онлайн":>7}{"p10 ₽":>8}'
            f'{"p25 ₽":>8}{"медиана":>9}{"предложение, кк":>17}')
    print(head)
    print("-" * len(head))
    for s in stats[:args.top]:
        supply = f'{s["supply"]:,.0f}'.replace(",", " ")
        print(f'{s["server"][:19]:<20}{s["lots"]:>6}{s["online"]:>7}{s["p10"]:>8.2f}'
              f'{s["p25"]:>8.2f}{s["median"]:>9.2f}{supply:>17}')

    worst = stats[-1]
    print(f'\nХудший из зачётных: {worst["server"]} — p25 {worst["p25"]:.2f} ₽')

    best = stats[0]
    allp = [r["price"] for s in stats for r in s["items"]]
    print(f'\n{"=" * 62}\nЛУЧШИЙ СЕРВЕР ДЛЯ ПРОДАЖИ: {best["server"]}')
    print(f'  конкурентная цена (p25): {best["p25"]:.2f} ₽ за 1 кк')
    print(f'  средняя по серверу: {best["mean"]:.2f} ₽ | медиана: {best["median"]:.2f} ₽')
    print(f'  лотов {best["lots"]}, продавцов онлайн {best["online"]}')
    print(f'\nСредняя цена за 1 млн виртов по всей бирже: {statistics.fmean(allp):.2f} ₽')
    print(f'Медиана по бирже: {statistics.median(allp):.2f} ₽')

    if args.no_cards:
        return

    band = sorted(best["items"], key=lambda r: r["price"])[:args.cards]
    print(f'\nКонкурентная полоса «{best["server"]}» — условия из карточек '
          f'({len(band)} самых дешёвых лотов):')
    cards = fetch_cards(band)
    for c in cards:
        mo = f'мин {c["min_order"]:g} кк' if c["min_order"] else "мин не указан"
        pay = ", ".join(c["pay"]) or "оплата не указана"
        rating = f'{c["stars"]}★/{c["reviews"]}отз' if c["stars"] else f'{c["reviews"]}отз'
        flag = " [!]" if c["error"] else ""
        print(f'  {c["price"]:>7.2f} ₽  {c["seller"][:18]:<19} {c["amount"]:>6.0f} кк  '
              f'{rating:<10} {mo:<14} {pay[:40]}{flag}')

    mins = [c["min_order"] for c in cards if c["min_order"]]
    if mins:
        print(f'\n  Минимальный заказ в этой полосе: от {min(mins):g} до {max(mins):g} кк '
              f'(медиана {statistics.median(mins):g} кк)')


if __name__ == "__main__":
    main()
