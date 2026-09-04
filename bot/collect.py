#!/usr/bin/env python3
"""Собирает данные для бота: игровой онлайн серверов + цены виртов на FunPay.

Запускается по расписанию в GitHub Actions и кладёт результат в data/servers.json.

Онлайн берётся не с сайта, а прямо у игровых серверов по штатному протоколу
SA-MP: сервер сам отвечает на такой запрос, это дёшево и не трогает сайт с его
защитой от ботов.

Цены снимаются двумя числами. Абсолютный минимум — то, что видно первой
строкой списка, — на каждом пятом сервере недостижим: его держат продавцы
без единого отзыва или с оптовым минимумом заказа. Поэтому рядом считается
минимум у продавца, у которого реально можно купить.
"""

import json
import os
import re
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "data", "servers.json")

SAMP_PORT = 7777
FUNPAY_URL = "https://funpay.com/chips/186/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
# Без куки cy=rub FunPay отдаёт прайс в долларах.
HEADERS = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9", "Cookie": "cy=rub"}

# Порог доверия к лоту: продавец с отзывами и минимумом, до которого дотянется
# обычный покупатель, а не только оптовик.
MIN_REVIEWS = 1
MAX_MIN_ORDER_KK = 20

ROW_RE = re.compile(r'<a\b[^>]*class="[^"]*\btc-item\b[^"]*"[^>]*>.*?</a>', re.S)
SERVER_RE = re.compile(r'<div class="tc-server[^"]*">([^<]*)</div>')
# data-s у tc-price — ключ сортировки, а не цена: у промо-лотов там 0, 1, 2…
PRICE_RE = re.compile(r'<div class="tc-price"[^>]*>\s*<div>([\d.,\s]+)<span class="unit">')
AMOUNT_RE = re.compile(r'<div class="tc-amount"[^>]*data-s="([\d.]+)"')
NAME_RE = re.compile(r'<div class="media-user-name">\s*([^<]*)</div>')
REVIEWS_RE = re.compile(r'<span class="rating-mini-count">(\d+)</span>')
HREF_RE = re.compile(r'href="([^"]+)"')
MIN_ORDER_RE = re.compile(r"Минимум\s+([\d.,]+)\s*кк", re.I)
TAG_RE = re.compile(r"<[^>]+>")


# --------------------------------------------------------------- игровой онлайн

def samp_query(host, port=SAMP_PORT, timeout=3.0):
    """Спрашивает у игрового сервера его онлайн по протоколу SA-MP.

    Пакет: 'SAMP' + 4 байта IP + 2 байта порта + опкод 'i'. В ответе после
    того же заголовка идут флаг пароля, текущий онлайн и лимит слотов.
    """
    try:
        ip = socket.gethostbyname(host)
    except socket.gaierror:
        return None
    packet = b"SAMP" + bytes(int(x) for x in ip.split(".")) + struct.pack("<H", port) + b"i"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(packet, (ip, port))
        data, _ = s.recvfrom(4096)
    except (socket.timeout, OSError):
        return None
    finally:
        s.close()
    if len(data) < 17 or not data.startswith(b"SAMP"):
        return None
    online, cap = struct.unpack("<HH", data[12:16])
    return {"online": online, "cap": cap}


def collect_online(servers, workers=24):
    """Опрашивает все серверы параллельно; неответившие остаются без онлайна."""
    def one(srv):
        for port in (SAMP_PORT, 5125):      # у части серверов нестандартный порт
            got = samp_query(srv["host"], port)
            if got:
                return {**srv, **got, "port": port}
        return {**srv, "online": None, "cap": None, "port": None}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, servers))


# --------------------------------------------------------------------- цены

def fetch(url, timeout=45, attempts=4):
    """Тянет страницу, переживая обрывы: сеть у раннера не всегда ровная."""
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError):
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def num(s):
    m = re.search(r"\d+(?:[.,]\d+)?", s.replace(" ", "").replace("\xa0", ""))
    return float(m.group().replace(",", ".")) if m else None


def parse_lots(html):
    lots = []
    for block in ROW_RE.findall(html):
        server, price = SERVER_RE.search(block), PRICE_RE.search(block)
        if not (server and price):
            continue
        amount, seller, reviews = (AMOUNT_RE.search(block), NAME_RE.search(block),
                                   REVIEWS_RE.search(block))
        href = HREF_RE.search(block)
        lots.append({
            "server": unescape(server.group(1)).strip(),
            "price": num(price.group(1)),
            "amount": float(amount.group(1)) if amount else 0.0,
            "seller": unescape(seller.group(1)).strip() if seller else "",
            "reviews": int(reviews.group(1)) if reviews else 0,
            "online": 'data-online="1"' in block,
            "url": unescape(href.group(1)) if href else "",
        })
    return lots


def read_min_order(url):
    """Минимальный заказ виден только внутри карточки оффера, не в списке."""
    try:
        text = unescape(TAG_RE.sub("\n", fetch(url, timeout=30, attempts=2)))
    except Exception:
        return None
    m = MIN_ORDER_RE.search(text)
    return num(m.group(1)) if m else None


def drop_junk(lots):
    """Убирает лоты-заглушки: цена кратно выше рынка сервера, купить нельзя."""
    if not lots:
        return []
    prices = sorted(l["price"] for l in lots)
    median = prices[len(prices) // 2]
    return [l for l in lots if l["price"] <= median * 3]


def price_stats(lots, checked):
    """Абсолютный минимум и минимум у продавца, которому можно доверять."""
    live = sorted([l for l in lots if l["online"]], key=lambda l: l["price"])
    if not live:
        return None
    cheapest = live[0]
    trusted = None
    for l in live:
        mo = checked.get(l["url"])
        if l["reviews"] >= MIN_REVIEWS and mo is not None and mo <= MAX_MIN_ORDER_KK:
            trusted = {**l, "min_order": mo}
            break
    prices = [l["price"] for l in live]
    return {
        "min": round(cheapest["price"], 2),
        "min_seller": cheapest["seller"],
        "min_reviews": cheapest["reviews"],
        "min_order": checked.get(cheapest["url"]),
        "safe": round(trusted["price"], 2) if trusted else None,
        "safe_seller": trusted["seller"] if trusted else None,
        "safe_reviews": trusted["reviews"] if trusted else None,
        "safe_min_order": trusted["min_order"] if trusted else None,
        "median": round(sorted(prices)[len(prices) // 2], 2),
        "sellers_online": len(live),
        "lots": len(lots),
    }


def main():
    servers = json.load(open(os.path.join(HERE, "servers.json"), encoding="utf-8"))
    print(f"серверов в справочнике: {len(servers)}", file=sys.stderr)

    online = collect_online(servers)
    answered = sum(1 for s in online if s["online"] is not None)
    print(f"ответили по SA-MP: {answered}/{len(online)}", file=sys.stderr)

    lots = drop_junk(parse_lots(fetch(FUNPAY_URL)))
    print(f"лотов после отсева заглушек: {len(lots)}", file=sys.stderr)

    by_server = {}
    for l in lots:
        by_server.setdefault(l["server"], []).append(l)

    # Карточки читаем только у самых дешёвых онлайн-лотов каждого сервера:
    # именно среди них ищется и абсолютный минимум, и безопасный.
    to_check = []
    for group in by_server.values():
        live = sorted([l for l in group if l["online"]], key=lambda l: l["price"])
        to_check += [l["url"] for l in live[:6] if l["url"]]
    print(f"карточек к чтению: {len(to_check)}", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=4) as pool:
        checked = dict(zip(to_check, pool.map(read_min_order, to_check)))

    out = []
    for srv in online:
        key = f'№{srv["num"]:02d} {srv["name"]}'
        group = next((v for k, v in by_server.items() if k.lower() == key.lower()), None)
        stats = price_stats(group, checked) if group else None
        row = {"num": srv["num"], "name": srv["name"],
               "online": srv["online"], "cap": srv["cap"]}
        if stats:
            row.update(stats)
            if srv["online"] and stats["safe"]:
                # Выручка фармера ≈ скорость фарма × цена; онлайн — прокси первого.
                row["index"] = round(srv["online"] * stats["safe"] / 1000)
        out.append(row)

    payload = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"prices": FUNPAY_URL, "online": "SA-MP query"},
        "servers_total": len(out),
        "online_answered": answered,
        "priced": sum(1 for r in out if r.get("min")),
        "servers": out,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"записано: {OUT} ({payload['priced']} серверов с ценой)", file=sys.stderr)


if __name__ == "__main__":
    main()
