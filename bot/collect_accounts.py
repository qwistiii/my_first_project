#!/usr/bin/env python3
"""Собирает лоты с аккаунтами Black Russia на FunPay.

Аккаунты устроены не как вирты: каждый лот уникален, и вся суть в описании
и характеристиках — уровень, баланс, стоимость имущества, автовыдача.

Главная сложность — заявленным числам нельзя верить. Поле баланса на FunPay
считается в тысячах, но почти половина продавцов вписывает туда миллионы,
и получается «8 000 000 к», то есть 8 миллиардов виртов, за 200 ₽. Поэтому
каждый лот проверяется на правдоподобие: заявленное добро пересчитывается в
деньги по рыночной цене вирта, и если оно расходится с ценой лота в разы,
лот помечается — а не подаётся как выгодная покупка.

Охват: FunPay отдаёт 3000 лотов из ~8000. Больше получить нельзя: любая
сортировка возвращает те же самые, страница продавца показывает только 20,
серверного фильтра нет. Это записано в выгрузку как есть.
"""

import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html import unescape

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")
OUT = os.path.join(DATA, "accounts.json")
SERVERS = os.path.join(DATA, "servers.json")

URL = "https://funpay.com/lots/1442/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
# Без куки cy=rub FunPay отдаёт прайс в долларах.
HEADERS = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9", "Cookie": "cy=rub"}

ROW_RE = re.compile(r'<a\b[^>]*class="[^"]*\btc-item\b[^"]*"[^>]*>.*?</a>', re.S)
ID_RE = re.compile(r"lots/offer\?id=(\d+)")
SERVER_RE = re.compile(r'<div class="tc-server[^"]*">([^<]*)</div>')
DESC_RE = re.compile(r'<div class="tc-desc-text">(.*?)</div>', re.S)
PRICE_RE = re.compile(r'<div class="tc-price"[^>]*>\s*<div>([\d.,\s]+)<span')
USER_RE = re.compile(r"users/(\d+)/")
NAME_RE = re.compile(r'<div class="media-user-name">\s*([^<]*)</div>')
STARS_RE = re.compile(r"rating-stars rating-(\d)")
# Отзывы показываются двумя способами: числом рядом со звёздами и словами.
REVIEWS_NUM_RE = re.compile(r'<span class="rating-mini-count">(\d+)</span>')
REVIEWS_TXT_RE = re.compile(r'<div class="media-user-reviews">\s*(\d+)\s*отзыв')
TAG_RE = re.compile(r"<[^>]+>")

# Хвост, который FunPay дописывает к описанию из полей формы. Он дублирует
# характеристики и в тексте не нужен.
TAIL_RE = re.compile(r",\s*\d+\s*уровень.*$", re.S)

# Что важно покупателю и встречается в описаниях достаточно часто, чтобы
# считаться признаком, а не случайным словом.
FLAGS = [
    ("без привязок", r"без\s*привяз|не\s*привяз|отвязан|без\s*почт"),
    ("рег. данные", r"рег\.?\s*данн|регдан|почта\s*в\s*комплект"),
    ("донат", r"\bbc\b|\bбк\b|донат"),
    ("авто", r"\bавто\b(?!выдач)|машин|тачк|транспорт"),
    ("дом", r"\bдом\b|квартир|особняк|бизнес"),
    ("гарантия", r"гарант"),
    # Привязанная почта — не украшение, а риск: доступ остаётся у продавца.
    ("⚠ привязана почта", r"привязан\w*\s*(почт|email|мейл|gmail)|почта\s*привязан"),
]

# Ниже этой доли рыночной стоимости заявка перестаёт быть правдоподобной:
# никто не продаёт вирты в сорок раз дешевле биржи.
PLAUSIBLE_RATIO = 0.05


def fetch(url, attempts=4):
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError):
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def num(s):
    m = re.search(r"\d+(?:[.,]\d+)?", str(s).replace(" ", "").replace("\xa0", ""))
    return float(m.group().replace(",", ".")) if m else None


def clean(html):
    return re.sub(r"\s+", " ", unescape(TAG_RE.sub("", html))).strip()


def parse(html):
    lots = []
    for block in ROW_RE.findall(html):
        price = PRICE_RE.search(block)
        server = SERVER_RE.search(block)
        offer = ID_RE.search(block)
        if not (price and server and offer):
            continue
        desc_raw = DESC_RE.search(block)
        desc = clean(desc_raw.group(1)) if desc_raw else ""
        attr = lambda p, d=0: num(m.group(1)) if (m := re.search(p, block)) else d
        reviews = REVIEWS_NUM_RE.search(block) or REVIEWS_TXT_RE.search(block)
        stars = STARS_RE.search(block)
        user = USER_RE.search(block)
        name = NAME_RE.search(block)
        srv_num = re.match(r"№\s*(\d+)", server.group(1))
        lots.append({
            "id": int(offer.group(1)),
            "srv": int(srv_num.group(1)) if srv_num else None,
            "srv_name": unescape(server.group(1)).strip(),
            "price": num(price.group(1)),
            "lvl": int(attr(r'data-f-level="(\d+)"') or 0),
            "bal": attr(r'data-f-currency="([\d.]+)"'),      # тысяч виртов
            "prop": attr(r'data-f-property="([\d.]+)"'),     # тысяч виртов
            "auto": 'data-auto="1"' in block,
            "online": 'data-online="1"' in block,
            "user": int(user.group(1)) if user else None,
            "seller": clean(name.group(1)) if name else "",
            "reviews": int(reviews.group(1)) if reviews else 0,
            "stars": int(stars.group(1)) if stars else None,
            "desc": TAIL_RE.sub("", desc).strip(" ,"),
        })
    return lots


def virt_rate():
    """Рыночная цена вирта — из выгрузки по серверам, если она есть."""
    try:
        data = json.load(open(SERVERS, encoding="utf-8"))
        rates = [s["safe"] for s in data["servers"] if s.get("safe")]
        return statistics.median(rates) if rates else None
    except (OSError, ValueError, KeyError):
        return None


def annotate(lots, rate):
    """Признаки из описания и проверка заявленного добра на правдоподобие."""
    for lot in lots:
        low = lot["desc"].lower()
        lot["flags"] = [name for name, pat in FLAGS if re.search(pat, low)]
        if lot["auto"]:
            lot["flags"].insert(0, "автовыдача")

        assets_kk = (lot["bal"] + lot["prop"]) / 1000        # тысячи → миллионы
        lot["assets_kk"] = round(assets_kk, 1)
        lot["worth"] = round(assets_kk * rate) if rate else None

        if not rate or assets_kk <= 0:
            lot["claim"] = "нет данных"
        elif lot["price"] >= assets_kk * rate * PLAUSIBLE_RATIO:
            # Цена сопоставима с рыночной стоимостью заявленного — верим.
            lot["claim"] = "правдоподобно"
        else:
            lot["claim"] = "завышено"
    return lots


# Бот тянет эту выгрузку целиком при каждом холодном старте, поэтому имена
# полей короткие, а описание обрезано: полный текст всё равно не помещается
# в сообщение, а по ссылке на лот видно оригинал.
DESC_LIMIT = 150


def compact(lot):
    return {
        "i": lot["id"], "s": lot["srv"], "n": lot["srv_name"],
        "p": lot["price"], "l": lot["lvl"],
        "a": lot["assets_kk"], "w": lot["worth"],
        "u": lot["seller"], "r": lot["reviews"], "st": lot["stars"],
        "on": 1 if lot["online"] else 0,
        "f": lot["flags"],
        "c": {"правдоподобно": "ok", "завышено": "hi"}.get(lot["claim"], "na"),
        "d": lot["desc"][:DESC_LIMIT],
    }


def main():
    rate = virt_rate()
    print(f"рыночная цена вирта: {rate:.2f} ₽/кк" if rate else "цена вирта неизвестна",
          file=sys.stderr)

    lots = annotate(parse(fetch(URL)), rate)
    print(f"лотов разобрано: {len(lots)}", file=sys.stderr)
    if not lots:
        sys.exit("лоты не найдены — вёрстка страницы изменилась")

    counts = {}
    for lot in lots:
        counts[lot["claim"]] = counts.get(lot["claim"], 0) + 1
    print("проверка заявленного:", counts, file=sys.stderr)

    payload = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": URL,
        "rate": rate,
        "lots_shown": len(lots),
        # FunPay отдаёт только часть категории; пишем честно, чтобы бот мог
        # об этом сказать, а не делать вид, что видит весь рынок.
        "lots_total": total_in_category(),
        "claims": counts,
        "lots": [compact(l) for l in lots],
    }
    os.makedirs(DATA, exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"записано: {OUT}", file=sys.stderr)


def total_in_category():
    """Счётчик категории на странице игры — сколько лотов есть на самом деле."""
    try:
        text = TAG_RE.sub("\n", fetch("https://funpay.com/lots/1442/"))
        m = re.search(r"Аккаунты\s*\n\s*(\d+)", text)
        return int(m.group(1)) if m else None
    except Exception:
        return None


if __name__ == "__main__":
    main()
