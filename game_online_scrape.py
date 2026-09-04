import sys, re, json
sys.path.insert(0,"/tmp/claude-0/-home-user-my-first-project/084f4f98-a17a-5cfa-916a-ba274181e384/scratchpad")
exec(open("/tmp/claude-0/-home-user-my-first-project/084f4f98-a17a-5cfa-916a-ba274181e384/scratchpad/br_online.py",encoding="utf-8").read().split("with sync_playwright")[0])
from playwright.sync_api import sync_playwright
SP="/tmp/claude-0/-home-user-my-first-project/084f4f98-a17a-5cfa-916a-ba274181e384/scratchpad"
SRV_RE = re.compile("\u2116\\s?\\d{2}")

def attempt(p, n):
    b=p.chromium.launch(executable_path=CHROME)
    ctx=b.new_context(locale="ru-RU", user_agent=UA); ctx.route("**/*", handler)
    pg=ctx.new_page()
    hydrated=[]
    pg.on("request", lambda r: hydrated.append(1) if "static-prod2" in r.url else None)
    try:
        pg.goto("https://blackrussia.online/", wait_until="commit", timeout=60000)
        for _ in range(12):
            pg.wait_for_timeout(3000)
            try:
                t=pg.title()
                if t.strip() and "Check your browser" not in t: break
            except Exception: pass
        else:
            print(f"заход {n}: челлендж не пройден"); b.close(); return None
        # ждём, пока в DOM появятся имена серверов
        try:
            pg.wait_for_function(
                "() => /№\\s?\\d{2}\\s|Общий онлайн\\s*[1-9]/.test(document.body.innerText)",
                timeout=60000)
        except Exception:
            pass
        txt=pg.inner_text("body"); html=pg.content()
        found = len(re.findall(SRV_RE, txt))
        print(f"заход {n}: прошли, скриптов приложения {len(hydrated)}, имён серверов в DOM {found}")
        if found:
            open(f"{SP}/br_ok.html","w",encoding="utf-8").write(html)
            return txt
    except Exception as e:
        print(f"заход {n}: {type(e).__name__}")
    finally:
        try: b.close()
        except Exception: pass
    return None

with sync_playwright() as p:
    for n in range(1, 9):
        got = attempt(p, n)
        if got:
            i = got.find("ВЫБЕРИ СВОЙ СЕРВЕР")
            print("\n=== СЕКЦИЯ СЕРВЕРОВ ===")
            print(got[i:i+900] if i > 0 else got[:900])
            break
    else:
        print("\nне удалось: страница ни разу не отдала список серверов")
