/**
 * Телеграм-бот «Аналитика Black Russia» на Cloudflare Workers.
 *
 * Воркер только отвечает на сообщения: данные собирает GitHub Actions и кладёт
 * в data/ репозитория, откуда воркер их читает и держит в памяти несколько
 * минут. Так бот отвечает мгновенно и не дёргает ни FunPay, ни игровые
 * серверы на каждое сообщение.
 *
 * Переменные окружения:
 *   BOT_TOKEN      — токен от @BotFather (секрет)
 *   WEBHOOK_SECRET — то же, что передано Telegram в secret_token (секрет)
 *   DATA_URL       — ссылка на raw-версию data/servers.json
 *   ACCOUNTS_URL   — ссылка на raw-версию data/accounts.json
 */

const CACHE_TTL_MS = 4 * 60 * 1000;
const caches = { servers: { at: 0, data: null }, accounts: { at: 0, data: null } };

async function load(url, slot) {
  const c = caches[slot];
  if (c.data && Date.now() - c.at < CACHE_TTL_MS) return c.data;
  const r = await fetch(url, { cf: { cacheTtl: 120 } });
  if (!r.ok) throw new Error(`данные недоступны: HTTP ${r.status}`);
  c.at = Date.now();
  c.data = await r.json();
  return c.data;
}

const loadData = env => load(env.DATA_URL, "servers");
const loadAccounts = env =>
  load(env.ACCOUNTS_URL || env.DATA_URL.replace(/servers\.json$/, "accounts.json"), "accounts");

/* ------------------------------------------------------------------ формат */

const esc = s => String(s).replace(/[<>&]/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));

/* Моноширинных блоков здесь нет намеренно: Телеграм рисует их как код, с
   кнопкой «копировать», а в шрифте кода нет символа рубля — вместо ₽ выходит
   квадратик. Поэтому строки просто короткие, чтобы не переносились. */
const rub = v => (v == null ? "—" : Number(v) >= 100 ? String(Math.round(v)) : Number(v).toFixed(2));
const num = v => Number(v).toLocaleString("ru-RU").replace(/\u00a0/g, " ");
const kk = v => (v >= 1000 ? `${(v / 1000).toFixed(1)} млрд` : `${num(v)} кк`);
const srvName = s => `№${String(s.num).padStart(2, "0")} ${s.name}`;

function fillWord(s) {
  if (!s.online || !s.cap) return null;
  const p = s.online / s.cap;
  return p >= 1 ? "переполнен" : p >= 0.7 ? "людно"
       : p >= 0.45 ? "средне" : p >= 0.25 ? "малолюдно" : "пусто";
}

function fillBar(s) {
  if (!s.online || !s.cap) return "";
  const n = Math.min(5, Math.max(1, Math.round((s.online / s.cap) * 5)));
  return "▰".repeat(n) + "▱".repeat(5 - n);
}

function freshness(iso) {
  const d = new Date(iso);
  const min = Math.round((Date.now() - d.getTime()) / 60000);
  const ago = min < 1 ? "только что" : min < 60 ? `${min} мин назад`
    : min < 1440 ? `${Math.round(min / 60)} ч назад` : `${Math.round(min / 1440)} дн назад`;
  const stamp = d.toLocaleString("ru-RU", {
    timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit",
  });
  return `<i>${stamp} МСК · ${ago}${min > 45 ? " ⚠️ устарело" : ""}</i>`;
}

/* ------------------------------------------------------------------- меню */

/* Два раздела вместо свалки команд: у виртов и аккаунтов общего только
   сервер, а вопросы к ним разные. */
const MENU = {
  keyboard: [
    [{ text: "💰 Вирты" }, { text: "🎮 Аккаунты" }],
    [{ text: "🔍 Поиск" }],
  ],
  resize_keyboard: true,
  is_persistent: true,
};

const rows = (...lines) => ({ inline_keyboard: lines.filter(l => l && l.length) });
const btn = (text, data) => ({ text, callback_data: data });
const backTo = where => [btn("‹ Назад", `c:${where}`)];

const VIRT_MENU = rows(
  [btn("📈 Где фармить", "c:/best"), btn("💰 Где дешевле", "c:/cheap")],
  [btn("👥 Онлайн", "c:/top"), btn("📋 Все серверы", "c:/all")],
);

const ACC_MENU = rows(
  [btn("💸 Дешёвые", "c:/acc"), btn("🎯 По уровню", "c:/lvl")],
  [btn("🖥 По серверу", "c:/accsrv"), btn("⚠️ Сомнительные", "c:/acc_bad")],
);

const chunk = (arr, n) => arr.reduce(
  (acc, item, i) => (i % n ? acc[acc.length - 1].push(item) : acc.push([item]), acc), []);

/* --------------------------------------------------------------- вирты */

const cheapLine = s => `<b>${srvName(s)}</b> — ${rub(s.safe ?? s.min)} ₽`;
const farmLine = s => `<b>${srvName(s)}</b> — ${rub(s.safe ?? s.min)} ₽ · ${num(s.online)}`;
const onlineLine = s => `<b>${srvName(s)}</b> — ${num(s.online)} ${fillBar(s)}`;

const serverButtons = list => rows(
  ...chunk(list.map(s => btn(srvName(s), `s:${s.num}`)), 2),
  backTo("/virt"),
);

function serverCard(s, updated) {
  const out = [`<b>${esc(s.name)}</b>  №${String(s.num).padStart(2, "0")}`, ""];
  if (s.online != null) {
    out.push(`${fillBar(s)}  <b>${num(s.online)}</b> из ${num(s.cap)} — ${fillWord(s)}`);
  }
  if (s.sellers_online != null) out.push(`${s.sellers_online} продавцов виртов в сети`);

  const who = (name, reviews, min) => {
    const bits = [esc(name || "—"), reviews ? `${num(reviews)} отз.` : "без отзывов"];
    if (min) bits.push(`от ${min} кк`);
    else if (min === 0) bits.push("без минимума");
    return bits.join(" · ");
  };

  out.push("", `💰 <b>${rub(s.min)} ₽</b> — дешевле всех`,
    `<i>${who(s.min_seller, s.min_reviews, s.min_order)}</i>`);

  // Если верхняя строка списка недостижима, честнее сразу показать вторую
  // цену — и назвать настоящую причину, а не любую подвернувшуюся.
  if (s.safe != null && s.safe !== s.min) {
    const why = (s.min_reviews ?? 0) < 1 ? "у того нет отзывов"
      : s.min_order == null ? "условия того лота не прочитались"
      : s.min_order > 0 ? `там минимум ${s.min_order} кк`
      : "тот лот не прошёл проверку";
    out.push("", `✅ <b>${rub(s.safe)} ₽</b> — можно брать`,
      `<i>${who(s.safe_seller, s.safe_reviews, s.safe_min_order)}</i>`,
      `<i>дешевле есть, но ${why}</i>`);
  }
  out.push("", freshness(updated));
  return out.join("\n");
}

/* ------------------------------------------------------------ аккаунты */

const LEVELS = [[1, 4], [5, 9], [10, 14], [15, 99]];
const levelName = ([a, b]) => (b === 99 ? `${a}+ лвл` : `${a}–${b} лвл`);

const accLine = l => `<b>${Math.round(l.p)} ₽</b> · ${l.l} лвл · ${esc(l.n)}`;

const accButtons = (list, back) => rows(
  ...chunk(list.map(l => btn(`${Math.round(l.p)}₽ · ${l.l}лвл`, `a:${l.i}`)), 3),
  backTo(back || "/acc_menu"),
);

function accCard(l, meta) {
  const out = [
    `<b>${Math.round(l.p)} ₽</b> · ${l.l} уровень · ${esc(l.n)}`,
    "",
    `<i>${esc(l.d)}</i>`,
  ];
  if (l.a > 0) {
    out.push("", l.c === "hi"
      ? `⚠️ <b>Заявлено ${kk(l.a)}</b> — по бирже это ${num(l.w)} ₽, а лот отдают за ${Math.round(l.p)} ₽. Скорее всего продавец ошибся в единицах: поле считается тысячами.`
      : `💰 добра на <b>${kk(l.a)}</b> — по бирже это ${num(l.w)} ₽`);
  }
  if (l.f.length) out.push("", l.f.join(" · "));
  out.push("",
    `продавец ${esc(l.u)} · ${l.r ? `${num(l.r)} отз.` : "без отзывов"}${l.on ? " · в сети" : ""}`,
    `<a href="https://funpay.com/lots/offer?id=${l.i}">открыть на FunPay</a>`,
    "", freshness(meta.updated));
  return out.join("\n");
}

/** Честный охват: показывать часть категории как всё — обман. */
const accFooter = acc =>
  `<i>видно ${num(acc.lots_shown)} из ${num(acc.lots_total)} лотов — больше FunPay не отдаёт</i>`;

const goodLots = acc => acc.lots.filter(l => l.c === "ok" && l.r > 0);

const accList = (acc, list, title, note, back) => ({
  text: [title, note ? `<i>${note}</i>` : null, "",
    ...list.map(accLine), "", accFooter(acc), freshness(acc.updated)]
    .filter(x => x !== null).join("\n"),
  buttons: accButtons(list, back),
});

/* -------------------------------------------------------------- разбор */

const HELP = [
  "<b>Аналитика Black Russia</b>",
  "",
  "💰 <b>Вирты</b> — где дешевле купить и где выгоднее фармить",
  "🎮 <b>Аккаунты</b> — что продают и не врут ли в описании",
  "🔍 <b>Поиск</b> — сервер по названию или номеру",
  "",
  "<i>Цены с FunPay, онлайн — с сайта игры. Обновляется каждые 15 минут.</i>",
].join("\n");

const BUTTONS = {
  "💰 вирты": "/virt",
  "🎮 аккаунты": "/acc_menu",
  "🔍 поиск": "/find",
};

async function handle(text, env) {
  const raw = text.trim();
  const [cmdRaw, ...rest] = (BUTTONS[raw.toLowerCase()] || raw).split(/\s+/);
  const cmd = cmdRaw.toLowerCase().split("@")[0];
  const arg = rest.join(" ");

  if (cmd === "/start" || cmd === "/help") return { text: HELP, keyboard: MENU };
  if (cmd === "/virt") return { text: "💰 <b>Вирты</b>\n<i>Что показать?</i>", buttons: VIRT_MENU };
  if (cmd === "/acc_menu") return { text: "🎮 <b>Аккаунты</b>\n<i>Что показать?</i>", buttons: ACC_MENU };
  if (cmd === "/find") {
    return { text: "Напишите название или номер сервера — например <code>blue</code> или <code>42</code>.",
             keyboard: MENU };
  }
  if (cmd === "/accsrv") {
    return { text: "Напишите сервер, и в его карточке будет кнопка «Аккаунты здесь» — например <code>blue</code>.",
             keyboard: MENU };
  }

  if (["/best", "/cheap", "/top", "/all"].includes(cmd)) {
    const data = await loadData(env);
    const all = data.servers;

    if (cmd === "/all") {
      // Страницами по 16: 91 сервер не помещается ни в сообщение, ни в кнопки.
      const page = Math.max(0, Number(arg) || 0);
      const sorted = all.slice().sort((a, b) => a.num - b.num);
      const pages = Math.ceil(sorted.length / 16);
      const slice = sorted.slice(page * 16, page * 16 + 16);
      const nav = [];
      if (page > 0) nav.push(btn("‹ Раньше", `c:/all ${page - 1}`));
      if (page + 1 < pages) nav.push(btn("Дальше ›", `c:/all ${page + 1}`));
      return {
        text: [`📋 <b>Все серверы</b> — страница ${page + 1} из ${pages}`, "",
          ...slice.map(cheapLine), "", freshness(data.updated)].join("\n"),
        buttons: rows(...chunk(slice.map(s => btn(srvName(s), `s:${s.num}`)), 2),
          nav, backTo("/virt")),
      };
    }

    const [pool, title, note, line] = {
      "/best": [all.filter(s => s.index).sort((a, b) => b.index - a.index),
        "📈 <b>Где выгоднее фармить</b>", "цена × игроки: дорого на пустом сервере бесполезно", farmLine],
      "/cheap": [all.filter(s => s.safe != null).sort((a, b) => a.safe - b.safe),
        "💰 <b>Где дешевле купить</b>", "у продавца с отзывами, у которого реально можно взять", cheapLine],
      "/top": [all.filter(s => s.online != null).sort((a, b) => b.online - a.online),
        "👥 <b>Самые населённые</b>", null, onlineLine],
    }[cmd];
    if (!pool.length) return { text: "Данные ещё не собрались, попробуйте через несколько минут." };
    const list = pool.slice(0, 10);
    return {
      text: [title, note ? `<i>${note}</i>` : null, "", ...list.map(line), "", freshness(data.updated)]
        .filter(x => x !== null).join("\n"),
      buttons: serverButtons(list),
    };
  }

  if (cmd === "/acc" || cmd === "/acc_bad") {
    const acc = await loadAccounts(env);
    if (cmd === "/acc_bad") {
      const all = acc.lots.filter(l => l.c === "hi");
      return accList(acc, all.slice().sort((a, b) => b.a - a.a).slice(0, 10),
        "⚠️ <b>Сомнительные лоты</b>",
        `таких ${num(all.length)}: заявленное добро стоит дороже самого лота в разы`);
    }
    return accList(acc, goodLots(acc).sort((a, b) => a.p - b.p).slice(0, 10),
      "💸 <b>Аккаунты — самые дешёвые</b>", "только лоты, где заявленное сходится с ценой");
  }

  if (cmd === "/lvl") {
    const acc = await loadAccounts(env);
    if (!arg) {
      return {
        text: "🎯 <b>Аккаунты по уровню</b>\n<i>Выберите диапазон</i>",
        buttons: rows(...chunk(LEVELS.map((r, i) => btn(levelName(r), `c:/lvl ${i}`)), 2),
          backTo("/acc_menu")),
      };
    }
    const range = LEVELS[Number(arg)] || LEVELS[0];
    const list = goodLots(acc).filter(l => l.l >= range[0] && l.l <= range[1])
      .sort((a, b) => a.p - b.p).slice(0, 10);
    if (!list.length) return { text: "На этот диапазон лотов не нашлось.", buttons: ACC_MENU };
    return accList(acc, list, `🎯 <b>Аккаунты ${levelName(range)}</b>`,
      "самые дешёвые из правдоподобных", "/lvl");
  }

  if (/^\/a\d+$/.test(cmd)) {
    const acc = await loadAccounts(env);
    const l = acc.lots.find(x => x.i === Number(cmd.slice(2)));
    return l ? { text: accCard(l, acc), buttons: rows(backTo("/acc_menu")) }
             : { text: "Этот лот уже не в выдаче — возможно, его продали.", keyboard: MENU };
  }

  if (/^\/n\d+$/.test(cmd)) {
    const acc = await loadAccounts(env);
    const srv = Number(cmd.slice(2));
    const list = goodLots(acc).filter(l => l.s === srv).sort((a, b) => a.p - b.p).slice(0, 10);
    if (!list.length) return { text: "На этом сервере правдоподобных лотов не нашлось.", buttons: ACC_MENU };
    return accList(acc, list, `🎮 <b>Аккаунты — ${esc(list[0].n)}</b>`, "самые дешёвые из правдоподобных");
  }

  // Название сервера можно писать без команды — так проще всего.
  const query = cmd === "/s" || cmd === "/server" ? arg : raw;
  if (query && !query.startsWith("/")) {
    const data = await loadData(env);
    const s = findServer(data.servers, query);
    if (s) {
      return {
        text: serverCard(s, data.updated),
        buttons: rows([btn("🎮 Аккаунты здесь", `n:${s.num}`)],
          [btn("💰 Вирты", "c:/virt"), btn("🎮 Аккаунты", "c:/acc_menu")]),
      };
    }
    return { text: `Сервер «${esc(query)}» не найден. Попробуйте номер, например <code>42</code>.`,
             keyboard: MENU };
  }

  return { text: HELP, keyboard: MENU };
}

function findServer(servers, query) {
  const q = query.trim().toLowerCase().replace(/^№|^#/, "");
  const byNum = servers.find(s => String(s.num) === q || String(s.num).padStart(2, "0") === q);
  if (byNum) return byNum;
  return servers.find(s => s.name.toLowerCase() === q)
      || servers.find(s => s.name.toLowerCase().startsWith(q))
      || servers.find(s => s.name.toLowerCase().includes(q));
}

/* ------------------------------------------------------------- отправка */

async function api(env, method, body) {
  await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function send(env, chatId, reply) {
  const body = {
    chat_id: chatId, text: reply.text, parse_mode: "HTML",
    disable_web_page_preview: true,
  };
  // Постоянное меню и кнопки под сообщением — разные разметки, Телеграм
  // принимает только одну за раз.
  if (reply.buttons) body.reply_markup = reply.buttons;
  else if (reply.keyboard) body.reply_markup = reply.keyboard;
  await api(env, "sendMessage", body);
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok");
    if (env.WEBHOOK_SECRET &&
        request.headers.get("x-telegram-bot-api-secret-token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("ok");
    }

    const cb = update.callback_query;
    if (cb) {
      // Без подтверждения на кнопке крутятся часики.
      await api(env, "answerCallbackQuery", { callback_query_id: cb.id });
      const d = cb.data || "";
      const text = d.startsWith("s:") ? d.slice(2)
                 : d.startsWith("a:") ? `/a${d.slice(2)}`
                 : d.startsWith("n:") ? `/n${d.slice(2)}`
                 : d.startsWith("c:") ? d.slice(2)
                 : "/start";
      let reply;
      try {
        reply = await handle(text, env);
      } catch (err) {
        reply = { text: `Не получилось прочитать данные: ${esc(err.message)}.` };
      }
      await send(env, cb.message.chat.id, reply);
      return new Response("ok");
    }

    const msg = update.message || update.edited_message;
    if (!msg?.text) return new Response("ok");
    let reply;
    try {
      reply = await handle(msg.text, env);
    } catch (err) {
      reply = { text: `Не получилось прочитать данные: ${esc(err.message)}. Попробуйте через пару минут.` };
    }
    await send(env, msg.chat.id, reply);
    return new Response("ok");
  },
};
