#!/usr/bin/env python3
"""Анализ биржи виртов Black Russia на FunPay.

Разбирает лоты вместе с описанием каждой карточки, потому что голая цена
за 1 млн ни о чём не говорит: у одного продавца это перевод через банк без
условий, у другого — цена «от 10 млн», у третьего в описание зашит вход в
ваш аккаунт. Скрипт достаёт эти условия, отделяет сопоставимые лоты от
несопоставимых и только по сопоставимым считает среднюю цену и лучший
сервер для продажи.

Использование:
    python3 funpay_virts.py                     # тянет страницу с funpay.com
    python3 funpay_virts.py --file page.html    # разбирает сохранённую страницу
    python3 funpay_virts.py --server "Sydney"   # разбор карточек одного сервера
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
DIV_OPEN_RE = re.compile(r'<div[^>]*class="([^"]*)"[^>]*>', re.S)
DIV_TAG_RE = re.compile(r'<(/?)div\b[^>]*>', re.S)
CELL_NAME_RE = re.compile(r'\btc-(server|amount|price|user|desc)\b')
UNIT_RE = re.compile(r'<span[^>]*class="[^"]*\bunit\b[^"]*"[^>]*>(.*?)</span>', re.S)
TAG_RE = re.compile(r"<[^>]+>")

# Множители единиц, которыми продавцы пишут объём в описании: 1кк = 1 млн.
UNITS = {"к": 1e3, "k": 1e3, "кк": 1e6, "kk": 1e6, "ккк": 1e9,
         "тыс": 1e3, "млн": 1e6, "лям": 1e6, "ляма": 1e6, "лямов": 1e6, "млрд": 1e9}
MIN_RE = re.compile(r"(?:от|мин(?:имум|\.)?(?:\s*заказ\w*)?[:\s]*)\s*(\d+(?:[.,]\d+)?)\s*"
                    r"(ккк|кк|к|k|kk|тыс|млн|млрд|лям\w*)\b", re.I)

# Способ передачи виртов — главный источник разницы в цене и в риске.
METHODS = [
    ("банк",      r"банк|перевод|transfer|номер\s*счёт|счет"),
    ("трейд",     r"трейд|обмен|аксессуар|обвес|продажа\s*предмет"),
    ("тайник",    r"тайник|багажник|схрон|выброс|дроп"),
    ("казино",    r"казино|рулетк|ставк"),
    ("аукцион",   r"аукцион"),
    ("админкой",  r"админк|через\s*админ"),
]
# Условия, из-за которых цена лота несопоставима с остальными и лот
# исключается из расчёта: другая единица, другая услуга или скрытая наценка.
BLOCKERS = [
    ("нужен вход в аккаунт", r"вход\s*в\s*(?:ваш|акк)|данные\s*от\s*акк|логин\s*и\s*пароль|доступ\s*к\s*акк"),
    ("комиссия на покупателе", r"комисси\w*\s*(?:с|на)\s*(?:вас|покупател)"),
    ("цена не за 1 млн", r"цена\s*за\s*1\s*(?:к|k|тыс)\b|за\s*100\s*к\b"),
]
# Условия, которые важно показать продавцу, но которые не мешают сравнивать
# цену: большой минимум или предоплата не меняют стоимость 1 млн виртов.
NOTES = [
    ("предоплата", r"предоплат"),
    ("только опт", r"опт\w*\s*(?:от|только)|только\s*крупн"),
]


def slice_div(html, start):
    """Возвращает содержимое <div>, открытого перед позицией start, с учётом вложенности."""
    depth = 1
    for tag in DIV_TAG_RE.finditer(html, start):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return html[start:tag.start()]
    return html[start:]


def cells_of(block):
    """Ячейки строки лота по классам tc-*; первая встреченная выигрывает."""
    out = {}
    for m in DIV_OPEN_RE.finditer(block):
        name = CELL_NAME_RE.search(m.group(1))
        if name and name.group(1) not in out:
            out[name.group(1)] = slice_div(block, m.end())
    return out


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def text(fragment):
    return re.sub(r"\s+", " ", unescape(TAG_RE.sub(" ", fragment)).replace("\xa0", " ")).strip()


def number(s):
    m = re.search(r"\d+(?:[.,]\d+)?", re.sub(r"[  ]", "", s))
    return float(m.group().replace(",", ".")) if m else None


def read_description(desc):
    """Достаёт из описания карточки условия сделки."""
    low = desc.lower()
    info = {"min_amount": None, "methods": [], "flags": [], "notes": []}

    m = MIN_RE.search(low)
    if m:
        info["min_amount"] = float(m.group(1).replace(",", ".")) * UNITS.get(m.group(2).lower().rstrip("аов"), 1)

    for name, pattern in METHODS:
        if re.search(pattern, low):
            info["methods"].append(name)
    for name, pattern in BLOCKERS:
        if re.search(pattern, low):
            info["flags"].append(name)
    for name, pattern in NOTES:
        if re.search(pattern, low):
            info["notes"].append(name)
    return info


def parse(html):
    rows = []
    for block in ITEM_RE.findall(html):
        attrs = dict(ATTR_RE.findall(block))
        cells = cells_of(block)
        price = number(attrs.get("data-s") or cells.get("price", ""))
        if price is None:
            continue
        unit = UNIT_RE.search(cells.get("price", ""))
        desc = text(cells.get("desc", ""))
        row = {
            "server": text(cells.get("server", "")) or attrs.get("data-server") or "(не указан)",
            "seller": text(cells.get("user", "")).split(" ")[0],
            "amount": number(text(cells.get("amount", ""))) or 0.0,
            "price": price,
            "unit": text(unit.group(1)) if unit else "",
            "desc": desc,
            "online": attrs.get("data-online") == "1",
        }
        row.update(read_description(desc))
        rows.append(row)
    return rows


def price_floor(prices):
    """Нижняя граница нормальной цены по медиане и MAD (устойчиво к выбросам)."""
    med = statistics.median(prices)
    mad = statistics.median([abs(p - med) for p in prices]) or med * 0.1
    return med, med - 3 * mad


def split_comparable(rows):
    """Отделяет лоты, сопоставимые по цене, от лотов с особыми условиями.

    Несопоставимы: карточки с флагами из описания и ценовые выбросы вниз —
    это почти всегда цена за другой объём, приманка или скам, и включать их
    в среднее значит занизить его на ровном месте.

    Порог выброса считается внутри сервера, а не по всей бирже: цены разных
    серверов отличаются в разы, и общий разброс маскирует локальные приманки.
    """
    groups = {}
    for r in rows:
        groups.setdefault(r["server"], []).append(r)
    global_ref = price_floor([r["price"] for r in rows])

    good, odd = [], []
    for server, items in groups.items():
        # На выборке меньше четырёх лотов медиана сервера сама себе не судья.
        med, floor = price_floor([i["price"] for i in items]) if len(items) >= 4 else global_ref
        for r in items:
            reasons = list(r["flags"])
            if r["price"] < floor:
                reasons.append(f"выброс: {r['price']:.2f} ₽ против медианы {med:.2f} ₽")
            if r["unit"] and not re.search(r"1\s*000\s*000|1\s*млн|1\s*кк", r["unit"].lower()):
                reasons.append(f"иная единица цены: «{r['unit']}»")
            (odd if reasons else good).append({**r, "reasons": reasons})
    return good, odd


def report(rows, top, server_filter):
    if server_filter:
        rows = [r for r in rows if server_filter.lower() in r["server"].lower()]
        if not rows:
            sys.exit(f"Сервер «{server_filter}» на бирже не найден.")

    good, odd = split_comparable(rows)
    if not good:
        sys.exit("Все лоты попали в «особые условия» — проверьте вёрстку страницы.")

    by_server = {}
    for r in good:
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

    # Продавцу нужна и цена, и спрос: высокая цена на мёртвом сервере — это
    # лот, который просто не купят. Активность рынка берём как прокси спроса.
    max_median = max(s["median"] for s in stats)
    max_lots = max(s["lots"] for s in stats)
    for s in stats:
        s["score"] = 0.7 * (s["median"] / max_median) + 0.3 * (s["lots"] / max_lots)
    stats.sort(key=lambda s: s["score"], reverse=True)

    head = (f'{"сервер":<26}{"лотов":>7}{"онлайн":>8}{"мин ₽":>9}'
            f'{"медиана":>9}{"средняя":>9}{"скор":>7}')
    print(head)
    print("-" * len(head))
    for s in stats[:top]:
        print(f'{s["server"][:25]:<26}{s["lots"]:>7}{s["online"]:>8}{s["min"]:>9.2f}'
              f'{s["median"]:>9.2f}{s["mean"]:>9.2f}{s["score"]:>7.3f}')

    best = stats[0]
    prices = [r["price"] for r in good]
    print(f'\nЛотов разобрано: {len(rows)} — из них сопоставимых {len(good)}, '
          f'с особыми условиями {len(odd)} (исключены из расчёта).')
    print(f'Средняя цена за 1 млн виртов по бирже: {statistics.fmean(prices):.2f} ₽ '
          f'(медиана {statistics.median(prices):.2f} ₽)')
    print(f'\nЛучший сервер для продажи: {best["server"]}')
    print(f'  средняя цена за 1 млн: {best["mean"]:.2f} ₽ (медиана {best["median"]:.2f} ₽)')
    supply = f'{best["supply"]:,.0f}'.replace(",", " ")
    print(f'  активных лотов: {best["lots"]}, продавцов онлайн: {best["online"]}, '
          f'предложение: {supply} виртов')

    print(f'\nЧто пишут в карточках на «{best["server"]}»:')
    for r in sorted(by_server[best["server"]], key=lambda r: r["price"])[:10]:
        bits = []
        if r["min_amount"]:
            bits.append(f'от {r["min_amount"]/1e6:g} млн')
        if r["methods"]:
            bits.append("/".join(r["methods"]))
        bits.extend(r["notes"])
        note = "; ".join(bits) or "условий не указано"
        print(f'  {r["price"]:>7.2f} ₽  {r["seller"][:16]:<17} {note}')
        if r["desc"]:
            print(f'{"":>10}«{r["desc"][:100]}»')

    if odd:
        print(f'\nИсключено из расчёта ({len(odd)}) — цена несопоставима:')
        for r in sorted(odd, key=lambda r: r["price"])[:10]:
            print(f'  {r["price"]:>7.2f} ₽  {r["server"][:18]:<19} {"; ".join(r["reasons"])}')


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--file", help="разобрать сохранённый HTML вместо загрузки")
    p.add_argument("--server", help="показать разбор карточек только этого сервера")
    p.add_argument("--top", type=int, default=15, help="сколько серверов показать")
    args = p.parse_args()

    html = open(args.file, encoding="utf-8").read() if args.file else fetch(args.url)
    rows = parse(html)
    if not rows:
        sys.exit("Лоты не найдены: страница отдала защиту от ботов или вёрстка изменилась. "
                 "Сохраните страницу из браузера (Ctrl+S) и запустите с --file.")
    report(rows, args.top, args.server)


if __name__ == "__main__":
    main()
