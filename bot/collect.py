#!/usr/bin/env python3
"""Собирает данные для бота: игровой онлайн серверов + цены виртов на FunPay.

Запускается по расписанию в GitHub Actions и кладёт результат в data/servers.json.

Онлайн берётся с сайта blackrussia.online: одна загрузка страницы отдаёт все
серверы разом, то есть 96 обращений в сутки при сборе каждые 15 минут.
Прямой запрос к игровым серверам по протоколу SA-MP был бы легче, но у разных
серверов проекта разные порты, и подтвердить их не удалось — непроверенный
путь в рабочем боте хуже, чем проверенный и чуть более тяжёлый.

Страница закрыта JS-челленджем антиддоса, поэтому нужен настоящий браузер;
он пускает примерно с третьей попытки, отсюда повторы.

Цены снимаются двумя числами. Абсолютный минимум — то, что видно первой
строкой списка, — на каждом пятом сервере недостижим: его держат продавцы
без единого отзыва или с оптовым минимумом заказа. Поэтому рядом считается
минимум у продавца, у которого реально можно купить.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "data", "servers.json")

BR_URL = "https://blackrussia.online/"
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

SRV_ROW_RE = re.compile(
    r"#(\d{1,2})\s*\n\s*([A-ZА-Я0-9 \-.]{2,20})\s*\n\s*([\d  ]{1,6})\s*/\s*([\d  ]{3,6})")


def _proxy_routing(ctx):
    """В песочнице с агент-прокси браузер не доверяет её CA, а отключать
    проверку сертификата нельзя. Тогда запросы исполняет urllib, а браузер
    только рисует. В GitHub Actions прокси нет и эта ветка не включается."""
    def handler(route, request):
        pass_through = {"cookie", "referer", "accept", "accept-language", "user-agent"}
        hdrs = {k: v for k, v in request.headers.items() if k.lower() in pass_through}
        hdrs.setdefault("User-Agent", UA)
        try:
            req = urllib.request.Request(request.url, headers=hdrs, method=request.method)
            with urllib.request.urlopen(req, timeout=45) as r:
                body, hs = r.read(), r.headers
            out = {"content-type": hs.get("Content-Type", "text/html; charset=utf-8")}
            for sc in hs.get_all("Set-Cookie") or []:
                out["set-cookie"] = sc
            route.fulfill(status=200, body=body, headers=out)
        except Exception:
            route.abort()
    ctx.route("**/*", handler)


def _chromium_path():
    """В образах с предустановленным браузером его версия может не совпасть с
    той, что ждёт playwright. Путь можно задать явно через CHROMIUM_PATH."""
    explicit = os.environ.get("CHROMIUM_PATH")
    if explicit and os.path.exists(explicit):
        return explicit
    import glob
    found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return found[-1] if found else None


def collect_online(attempts=6):
    """Снимает онлайн всех серверов со страницы проекта."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    with sync_playwright() as p:
        browser = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        try:
            for n in range(1, attempts + 1):
                ctx = browser.new_context(locale="ru-RU", user_agent=UA)
                if os.environ.get("HTTPS_PROXY"):
                    _proxy_routing(ctx)
                page = ctx.new_page()
                try:
                    page.goto(BR_URL, wait_until="commit", timeout=60000)
                    for _ in range(12):
                        page.wait_for_timeout(3000)
                        try:
                            title = page.title()
                        except Exception:
                            continue
                        if title.strip() and "Check your browser" not in title:
                            break
                    else:
                        print(f"попытка {n}: челлендж не пройден", file=sys.stderr)
                        continue
                    try:
                        page.wait_for_function(
                            "() => /#\\d{1,2}[^]{0,40}\\d+\\s*\\/\\s*\\d{3,}/.test(document.body.innerText)",
                            timeout=45000)
                    except Exception:
                        pass
                    text = re.sub(r"<[^>]+>", "\n", page.content())
                    rows = {}
                    for m in SRV_ROW_RE.finditer(re.sub(r"\n\s*\n+", "\n", text)):
                        digits = lambda g: int(re.sub(r"\D", "", m.group(g)))
                        rows[int(m.group(1))] = {"online": digits(3), "cap": digits(4)}
                    if rows:
                        print(f"попытка {n}: снят онлайн {len(rows)} серверов", file=sys.stderr)
                        return rows
                    print(f"попытка {n}: список пуст", file=sys.stderr)
                except Exception as exc:
                    print(f"попытка {n}: {type(exc).__name__}", file=sys.stderr)
                finally:
                    ctx.close()
        finally:
            browser.close()
    return {}


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

    online = collect_online()
    answered = len(online)
    print(f"снят онлайн: {answered}/{len(servers)}", file=sys.stderr)

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
    for srv in servers:
        key = f'№{srv["num"]:02d} {srv["name"]}'
        group = next((v for k, v in by_server.items() if k.lower() == key.lower()), None)
        stats = price_stats(group, checked) if group else None
        live = online.get(srv["num"], {})
        row = {"num": srv["num"], "name": srv["name"],
               "online": live.get("online"), "cap": live.get("cap")}
        if stats:
            row.update(stats)
            if live.get("online") and stats["safe"]:
                # Выручка фармера ≈ скорость фарма × цена; онлайн — прокси первого.
                row["index"] = round(live["online"] * stats["safe"] / 1000)
        out.append(row)

    payload = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"prices": FUNPAY_URL, "online": BR_URL},
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
