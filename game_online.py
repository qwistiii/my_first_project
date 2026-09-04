"""Снимает игровой онлайн серверов Black Russia через настоящий браузер.

Сайт закрыт JS-челленджем антиддоса: он вычисляет cookie средствами AES и
перезагружает страницу, поэтому curl видит только заглушку. Chromium
челлендж проходит, но его TLS не доверяет CA агент-прокси, так что запросы
по-прежнему исполняет urllib — с пробросом заголовков браузера, иначе
выданная челленджем cookie не доедет обратно.
"""
import sys, urllib.request, urllib.error
from playwright.sync_api import sync_playwright

SP = "/tmp/claude-0/-home-user-my-first-project/084f4f98-a17a-5cfa-916a-ba274181e384/scratchpad"
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
PASS = {"cookie", "referer", "accept", "accept-language", "user-agent", "content-type"}


def handler(route, request):
    if request.url.startswith("data:"):
        return route.continue_()
    hdrs = {k: v for k, v in request.headers.items() if k.lower() in PASS}
    hdrs.setdefault("User-Agent", UA)
    try:
        req = urllib.request.Request(request.url, headers=hdrs, method=request.method)
        if request.post_data:
            req.data = request.post_data.encode()
        with urllib.request.urlopen(req, timeout=45) as r:
            body, hs = r.read(), r.headers
        out = {"content-type": hs.get("Content-Type", "text/html; charset=utf-8")}
        # Set-Cookie должен дойти до браузера, иначе челлендж не закрепится.
        for sc in hs.get_all("Set-Cookie") or []:
            out["set-cookie"] = sc
        route.fulfill(status=200, body=body, headers=out)
    except urllib.error.HTTPError as e:
        route.fulfill(status=e.code, body=e.read(), headers={"content-type": "text/html"})
    except Exception as exc:
        print(f"  [пропущен] {request.url[:60]} — {type(exc).__name__}", file=sys.stderr)
        route.abort()


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME)
    ctx = b.new_context(locale="ru-RU", user_agent=UA, viewport={"width": 1440, "height": 1000})
    ctx.route("**/*", handler)
    pg = ctx.new_page()
    pg.goto("https://blackrussia.online/", wait_until="domcontentloaded", timeout=90000)
    print("первый ответ:", len(pg.content()), "байт")
    pg.wait_for_timeout(9000)          # челлендж перезагружает страницу через 5 с
    html = pg.content()
    print("после челленджа:", len(html), "байт | заголовок:", pg.title())
    open(f"{SP}/br_real.html", "w", encoding="utf-8").write(html)
    txt = pg.inner_text("body")[:1200]
    print("--- текст страницы ---")
    print(txt)
    b.close()
