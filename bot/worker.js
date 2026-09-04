/**
 * Телеграм-бот «Вирты Black Russia» на Cloudflare Workers.
 *
 * Воркер только отвечает на сообщения: данные собирает GitHub Actions и кладёт
 * в data/servers.json репозитория, откуда воркер их читает и держит в памяти
 * несколько минут. Так бот отвечает мгновенно и не дёргает ни FunPay, ни
 * игровые серверы на каждое сообщение.
 *
 * Переменные окружения (Settings → Variables):
 *   BOT_TOKEN      — токен от @BotFather (секрет)
 *   DATA_URL       — ссылка на raw-версию data/servers.json
 *   WEBHOOK_SECRET — та же строка, что передана Telegram в secret_token (секрет)
 */

const CACHE_TTL_MS = 4 * 60 * 1000;
let cache = { at: 0, data: null };

async function loadData(env) {
  if (cache.data && Date.now() - cache.at < CACHE_TTL_MS) return cache.data;
  const r = await fetch(env.DATA_URL, { cf: { cacheTtl: 120 } });
  if (!r.ok) throw new Error(`данные недоступны: HTTP ${r.status}`);
  cache = { at: Date.now(), data: await r.json() };
  return cache.data;
}

const esc = s => String(s).replace(/[<>&]/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));
const rub = v => (v == null ? "—" : Number(v).toFixed(2));

/** Возраст данных словами: «5 минут назад». Дату показываем всегда. */
function freshness(iso) {
  const d = new Date(iso);
  const min = Math.round((Date.now() - d.getTime()) / 60000);
  const ago = min < 1 ? "только что" : min < 60 ? `${min} мин назад`
    : min < 1440 ? `${Math.round(min / 60)} ч назад` : `${Math.round(min / 1440)} дн назад`;
  const stamp = d.toLocaleString("ru-RU", {
    timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
  const stale = min > 45 ? " ⚠️ данные устарели" : "";
  return `<i>Обновлено ${stamp} МСК · ${ago}${stale}</i>`;
}

const fill = s => (s.online && s.cap ? Math.round((s.online / s.cap) * 100) : null);

/** Одна строка списка: сервер, цена, онлайн. */
function line(s, showIndex) {
  const f = fill(s);
  const bits = [`<b>№${String(s.num).padStart(2, "0")} ${esc(s.name)}</b>`];
  bits.push(`${rub(s.safe ?? s.min)} ₽`);
  if (s.online != null) bits.push(`${s.online} чел.${f ? ` (${f}%)` : ""}`);
  if (showIndex && s.index) bits.push(`индекс ${s.index}`);
  return bits.join(" · ");
}

function cardFor(s, updated) {
  const f = fill(s);
  const rows = [
    `<b>№${String(s.num).padStart(2, "0")} ${esc(s.name)}</b>`,
    "",
    `Игроков онлайн: <b>${s.online ?? "нет данных"}</b>${f ? ` из ${s.cap} (${f}%)` : ""}`,
    `Продавцов в сети: ${s.sellers_online ?? "—"} · лотов ${s.lots ?? "—"}`,
    "",
    `<b>Самая низкая цена: ${rub(s.min)} ₽</b> за 1 кк`,
    `   ${esc(s.min_seller || "—")}, отзывов ${s.min_reviews ?? 0}` +
      (s.min_order ? `, минимум ${s.min_order} кк`
       : s.min_order === 0 ? ", без минимума" : ""),
  ];
  // Если верхняя строка списка недостижима, честнее сразу показать вторую
  // цену — и назвать настоящую причину, а не любую подвернувшуюся.
  if (s.safe != null && s.safe !== s.min) {
    const why = (s.min_reviews ?? 0) < 1 ? "у того продавца нет отзывов"
      : s.min_order == null ? "условия того лота прочитать не удалось"
      : s.min_order > 0 ? `там минимум ${s.min_order} кк`
      : "тот лот не прошёл проверку";
    rows.push(
      "",
      `<b>Низшая у проверенного: ${rub(s.safe)} ₽</b>`,
      `   ${esc(s.safe_seller || "—")}, отзывов ${s.safe_reviews}` +
        (s.safe_min_order ? `, минимум ${s.safe_min_order} кк`
         : s.safe_min_order === 0 ? ", без минимума" : ""),
      `   <i>Дешевле есть, но ${why}.</i>`
    );
  }
  rows.push("", `Медиана сервера: ${rub(s.median)} ₽`, "", freshness(updated));
  return rows.join("\n");
}

const cardButtons = () => ({
  inline_keyboard: [[
    { text: "📈 Фарм", callback_data: "c:/best" },
    { text: "💰 Дешевле", callback_data: "c:/cheap" },
    { text: "👥 Онлайн", callback_data: "c:/top" },
  ]],
});

function findServer(servers, query) {
  const q = query.trim().toLowerCase().replace(/^№|^#/, "");
  const byNum = servers.find(s => String(s.num) === q || String(s.num).padStart(2, "0") === q);
  if (byNum) return byNum;
  return servers.find(s => s.name.toLowerCase() === q)
      || servers.find(s => s.name.toLowerCase().startsWith(q))
      || servers.find(s => s.name.toLowerCase().includes(q));
}

/* Постоянные кнопки под полем ввода: на телефоне печатать команды неудобно,
   а половину из них ещё и набирают с ошибкой. */
const MENU = {
  keyboard: [
    [{ text: "📈 Где фармить" }, { text: "💰 Где дешевле" }],
    [{ text: "👥 Онлайн" }, { text: "🔍 Найти сервер" }],
  ],
  resize_keyboard: true,
  is_persistent: true,
};

/** Кнопки под списком: открыть карточку сервера одним касанием. */
const serverButtons = (list, back) => ({
  inline_keyboard: [
    ...chunk(list.map(s => ({
      text: `№${String(s.num).padStart(2, "0")} ${s.name}`,
      callback_data: `s:${s.num}`,
    })), 2),
    ...(back ? [[{ text: "‹ Назад", callback_data: back }]] : []),
  ],
});

const chunk = (arr, n) => arr.reduce(
  (rows, item, i) => (i % n ? rows[rows.length - 1].push(item) : rows.push([item]), rows), []);

const HELP = [
  "<b>Вирты Black Russia</b>",
  "",
  "Нажимайте кнопки внизу — или напишите название сервера,",
  "например <code>blue</code> или <code>42</code>.",
  "",
  "<b>📈 Где фармить</b> — где больше народу и цена не худшая",
  "<b>💰 Где дешевле</b> — где выгоднее купить вирты",
  "<b>👥 Онлайн</b> — самые населённые серверы",
  "",
  "<i>Цены — за 1 кк (миллион виртов) на FunPay, онлайн — с сайта игры.</i>",
].join("\n");

const BUTTON_COMMANDS = {
  "📈 где фармить": "/best",
  "💰 где дешевле": "/cheap",
  "👥 онлайн": "/top",
  "🔍 найти сервер": "/find",
};

async function handle(text, env) {
  const raw = text.trim();
  const mapped = BUTTON_COMMANDS[raw.toLowerCase()];
  const [cmdRaw, ...rest] = (mapped || raw).split(/\s+/);
  const cmd = cmdRaw.toLowerCase().split("@")[0];
  const arg = rest.join(" ");

  if (cmd === "/start" || cmd === "/help") return { text: HELP, keyboard: MENU };
  if (cmd === "/find") {
    return { text: "Напишите название или номер сервера — например <code>blue</code> или <code>42</code>.",
             keyboard: MENU };
  }

  const data = await loadData(env);
  const all = data.servers;
  const priced = all.filter(s => s.min != null);

  if (cmd === "/best") {
    const list = all.filter(s => s.index).sort((a, b) => b.index - a.index).slice(0, 10);
    if (!list.length) return { text: "Пока нет данных с онлайном — сборщик ещё не отработал." };
    return {
      text: ["<b>📈 Где выгоднее фармить</b>",
        "<i>Считаю игроков × цену: высокая цена на пустом сервере бесполезна.</i>", "",
        ...list.map((s, i) => `${i + 1}. ${line(s, true)}`), "", freshness(data.updated)].join("\n"),
      buttons: serverButtons(list),
    };
  }

  if (cmd === "/cheap") {
    const list = priced.filter(s => s.safe != null).sort((a, b) => a.safe - b.safe).slice(0, 10);
    return {
      text: ["<b>💰 Где дешевле купить</b>",
        "<i>Цена у продавца с отзывами, у которого реально можно взять.</i>", "",
        ...list.map((s, i) => `${i + 1}. ${line(s, false)}`), "", freshness(data.updated)].join("\n"),
      buttons: serverButtons(list),
    };
  }

  if (cmd === "/top") {
    const list = all.filter(s => s.online != null).sort((a, b) => b.online - a.online).slice(0, 10);
    if (!list.length) return { text: "Онлайн серверов сейчас недоступен." };
    return {
      text: ["<b>👥 Самые населённые</b>", "",
        ...list.map((s, i) => `${i + 1}. ${line(s, false)}`), "", freshness(data.updated)].join("\n"),
      buttons: serverButtons(list),
    };
  }

  // Название сервера можно писать без команды — так проще всего.
  const query = cmd === "/s" || cmd === "/server" ? arg : raw;
  if (query && !query.startsWith("/")) {
    const s = findServer(all, query);
    if (s) return { text: cardFor(s, data.updated), buttons: cardButtons(s) };
    return { text: `Сервер «${esc(query)}» не найден. Попробуйте номер, например <code>42</code>.`,
             keyboard: MENU };
  }

  return { text: HELP, keyboard: MENU };
}

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
  // Постоянное меню и кнопки под сообщением — разные вещи, и Telegram
  // принимает только одну разметку за раз.
  if (reply.buttons) body.reply_markup = reply.buttons;
  else if (reply.keyboard) body.reply_markup = reply.keyboard;
  await api(env, "sendMessage", body);
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok");
    // Telegram шлёт секрет заголовком — без него запрос пришёл не от него.
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
    // Нажатие на кнопку под сообщением приходит отдельным типом события.
    const cb = update.callback_query;
    if (cb) {
      // Телеграм ждёт подтверждения, иначе на кнопке крутится часики.
      await api(env, "answerCallbackQuery", { callback_query_id: cb.id });
      const data = cb.data || "";
      const text = data.startsWith("s:") ? data.slice(2)
                 : data.startsWith("c:") ? data.slice(2)
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
