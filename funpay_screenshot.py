"""Открывает биржу FunPay в реальном Chromium.

Браузер не ходит в сеть сам: его TLS не доверяет CA агент-прокси, а
отключать проверку сертификата нельзя. Поэтому каждый запрос страницы
перехватывается и выполняется через urllib, который CA доверяет.
"""
import sys, urllib.request, urllib.error
from playwright.sync_api import sync_playwright

SP = "/tmp/claude-0/-home-user-my-first-project/084f4f98-a17a-5cfa-916a-ba274181e384/scratchpad"
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
cache = {}

def handler(route, request):
    url = request.url
    if url.startswith("data:"):
        return route.continue_()
    if url in cache:
        body, ctype = cache[url]
        return route.fulfill(status=200, body=body, headers={"content-type": ctype})
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9",
            "Cookie": "cy=rub", "Referer": "https://funpay.com/"})
        if request.method == "POST" and request.post_data:
            req.data = request.post_data.encode()
        with urllib.request.urlopen(req, timeout=45) as r:
            body = r.read()
            ctype = r.headers.get("Content-Type", "application/octet-stream")
        cache[url] = (body, ctype)
        route.fulfill(status=200, body=body, headers={"content-type": ctype})
    except Exception as exc:
        print(f"  [пропущен] {url[:70]} — {type(exc).__name__}", file=sys.stderr)
        route.abort()

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME)
    ctx = b.new_context(viewport={"width": 1500, "height": 1150}, locale="ru-RU", user_agent=UA)
    ctx.route("**/*", handler)
    pg = ctx.new_page()
    pg.goto("https://funpay.com/chips/186/", wait_until="domcontentloaded", timeout=90000)
    pg.wait_for_selector("a.tc-item", timeout=30000)
    print("заголовок:", pg.title())
    print("лотов в DOM:", pg.locator("a.tc-item").count())
    print("серверов в фильтре:", pg.locator('select[name="server"] option').count())
    print("колонки:", [t.strip() for t in pg.locator(".tc-header").first.locator("div").all_inner_texts()][:6])
    pg.wait_for_timeout(2500)
    pg.screenshot(path=f"{SP}/01_page.png")
    print("→ 01_page.png")
    b.close()
