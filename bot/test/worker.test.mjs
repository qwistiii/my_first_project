/** Прогон логики бота на реальном data/servers.json без сети и Telegram. */
import { readFileSync } from "node:fs";
import worker from "../worker.js";

const data = readFileSync(new URL("../../data/servers.json", import.meta.url), "utf8");
const accData = readFileSync(new URL("../../data/accounts.json", import.meta.url), "utf8");
const sent = [];

globalThis.fetch = async (url, init) => {
  if (String(url).includes("api.telegram.org")) {
    // answerCallbackQuery ответом не является — в проверку берём только сообщения.
    if (String(url).includes("/sendMessage")) sent.push(JSON.parse(init.body));
    return new Response("{}", { status: 200 });
  }
  if (String(url).includes("accounts.json")) return new Response(accData, { status: 200 });
  return new Response(data, { status: 200 });          // подменяет DATA_URL
};

const env = { BOT_TOKEN: "x", DATA_URL: "https://example/servers.json",
              ACCOUNTS_URL: "https://example/accounts.json", WEBHOOK_SECRET: "s" };
const post = async payload => {
  sent.length = 0;
  await worker.fetch(new Request("https://w/", {
    method: "POST",
    headers: { "x-telegram-bot-api-secret-token": "s", "content-type": "application/json" },
    body: JSON.stringify(payload),
  }), env);
  return sent[0] ?? null;
};
const ask = async text => {
  const m = await post({ message: { chat: { id: 1 }, text } });
  if (!m) return "(нет ответа)";
  const kb = m.reply_markup?.keyboard
    ? "\n[меню: " + m.reply_markup.keyboard.flat().map(b => b.text).join(" | ") + "]"
    : m.reply_markup?.inline_keyboard
    ? "\n[кнопки: " + m.reply_markup.inline_keyboard.flat().map(b => b.text).join(" | ") + "]"
    : "";
  return m.text + kb;
};
const tap = async data => {
  const m = await post({ callback_query: { id: "1", data, message: { chat: { id: 1 } } } });
  if (!m) return "(нет ответа)";
  const kb = m.reply_markup?.inline_keyboard
    ? "\n[кнопки: " + m.reply_markup.inline_keyboard.flat().map(b => b.text).join(" | ") + "]"
    : "";
  return m.text + kb;
};

for (const cmd of ["/start", "💰 Вирты", "🎮 Аккаунты", "blue"]) {
  const out = await ask(cmd);
  console.log(`\n===== ${cmd} =====\n${out}`);
}

for (const d of ["c:/cheap", "c:/all 0", "c:/all 5", "c:/lvl", "c:/lvl 3", "c:/acc", "c:/acc_bad", "n:3"]) {
  console.log(`\n===== кнопка ${d} =====\n` + await tap(d));
}
// Карточка лота: берём id первого лота из выгрузки.
const firstLot = JSON.parse(accData).lots.filter(l => l.c === "ok")[0];
console.log("\n===== карточка лота =====\n" + (await post({
  callback_query: { id: "1", data: `a:${firstLot.i}`, message: { chat: { id: 1 } } },
})).text);
console.log("\n===== нажатие кнопки c:/cheap =====\n" + await tap("c:/cheap"));

// Чужой запрос без секрета не должен обслуживаться.
const bad = await worker.fetch(new Request("https://w/", {
  method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ message: { chat: { id: 1 }, text: "/best" } }),
}), env);
console.log("\n===== без секрета =====\nHTTP", bad.status);
