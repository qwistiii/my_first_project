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
      (s.min_order != null ? `, минимум ${s.min_order} кк` : ""),
  ];
  // Если верхняя строка списка недостижима, честнее сразу показать вторую цену.
  if (s.safe != null && s.safe !== s.min) {
    rows.push(
      "",
      `<b>Низшая у проверенного: ${rub(s.safe)} ₽</b>`,
      `   ${esc(s.safe_seller || "—")}, отзывов ${s.safe_reviews}` +
        (s.safe_min_order != null ? `, минимум ${s.safe_min_order} кк` : ""),
      `   <i>Дешевле есть, но у того продавца ` +
        `${(s.min_reviews ?? 0) < 1 ? "нет отзывов" : `минимум ${s.min_order} кк`}.</i>`
    );
  }
  rows.push("", `Медиана сервера: ${rub(s.median)} ₽`, "", freshness(updated));
  return rows.join("\n");
}

function findServer(servers, query) {
  const q = query.trim().toLowerCase().replace(/^№|^#/, "");
  const byNum = servers.find(s => String(s.num) === q || String(s.num).padStart(2, "0") === q);
  if (byNum) return byNum;
  return servers.find(s => s.name.toLowerCase() === q)
      || servers.find(s => s.name.toLowerCase().startsWith(q))
      || servers.find(s => s.name.toLowerCase().includes(q));
}

const HELP = [
  "<b>Вирты Black Russia</b>",
  "",
  "/best — где выгоднее фармить: онлайн × цена",
  "/cheap — где дешевле купить",
  "/top — самые населённые серверы",
  "/s &lt;сервер&gt; — карточка сервера, например <code>/s blue</code> или <code>/s 42</code>",
  "",
  "<i>Цены — за 1 кк (миллион виртов) на FunPay, онлайн — прямой запрос к игровым серверам.</i>",
].join("\n");

async function handle(text, env) {
  const [cmdRaw, ...rest] = text.trim().split(/\s+/);
  const cmd = cmdRaw.toLowerCase().split("@")[0];
  const arg = rest.join(" ");

  if (cmd === "/start" || cmd === "/help") return HELP;

  const data = await loadData(env);
  const all = data.servers;
  const priced = all.filter(s => s.min != null);

  if (cmd === "/best") {
    const list = all.filter(s => s.index).sort((a, b) => b.index - a.index).slice(0, 12);
    if (!list.length) return "Пока нет данных с онлайном — сборщик ещё не отработал.";
    return ["<b>Лучшие для фарма</b>",
      "<i>Индекс = игроков × цена ÷ 1000. Высокая цена на пустом сервере бесполезна.</i>", "",
      ...list.map((s, i) => `${i + 1}. ${line(s, true)}`), "", freshness(data.updated)].join("\n");
  }

  if (cmd === "/cheap") {
    const list = priced.filter(s => s.safe != null).sort((a, b) => a.safe - b.safe).slice(0, 12);
    return ["<b>Где дешевле купить</b>",
      "<i>Цена у продавца с отзывами и посильным минимумом заказа.</i>", "",
      ...list.map((s, i) => `${i + 1}. ${line(s, false)}`), "", freshness(data.updated)].join("\n");
  }

  if (cmd === "/top") {
    const list = all.filter(s => s.online != null).sort((a, b) => b.online - a.online).slice(0, 12);
    if (!list.length) return "Онлайн серверов сейчас недоступен.";
    return ["<b>Самые населённые</b>", "",
      ...list.map((s, i) => `${i + 1}. ${line(s, false)}`), "", freshness(data.updated)].join("\n");
  }

  if (cmd === "/s" || cmd === "/server") {
    if (!arg) return "Укажите сервер: <code>/s blue</code> или <code>/s 42</code>";
    const s = findServer(all, arg);
    if (!s) return `Сервер «${esc(arg)}» не найден. Попробуйте номер, например <code>/s 03</code>.`;
    return cardFor(s, data.updated);
  }

  return HELP;
}

async function send(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId, text, parse_mode: "HTML", disable_web_page_preview: true,
    }),
  });
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
    const msg = update.message || update.edited_message;
    if (!msg?.text) return new Response("ok");
    let reply;
    try {
      reply = await handle(msg.text, env);
    } catch (err) {
      reply = `Не получилось прочитать данные: ${esc(err.message)}. Попробуйте через пару минут.`;
    }
    await send(env, msg.chat.id, reply);
    return new Response("ok");
  },
};
