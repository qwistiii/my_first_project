/** Прогон логики бота на реальном data/servers.json без сети и Telegram. */
import { readFileSync } from "node:fs";
import worker from "../worker.js";

const data = readFileSync(new URL("../../data/servers.json", import.meta.url), "utf8");
const sent = [];

globalThis.fetch = async (url, init) => {
  if (String(url).includes("api.telegram.org")) {
    sent.push(JSON.parse(init.body));
    return new Response("{}", { status: 200 });
  }
  return new Response(data, { status: 200 });          // подменяет DATA_URL
};

const env = { BOT_TOKEN: "x", DATA_URL: "https://example/data.json", WEBHOOK_SECRET: "s" };
const ask = async text => {
  sent.length = 0;
  const req = new Request("https://w/", {
    method: "POST",
    headers: { "x-telegram-bot-api-secret-token": "s", "content-type": "application/json" },
    body: JSON.stringify({ message: { chat: { id: 1 }, text } }),
  });
  await worker.fetch(req, env);
  return sent[0]?.text ?? "(нет ответа)";
};

for (const cmd of ["/start", "/best", "/cheap", "/top", "/s blue", "/s 87", "/s зззз"]) {
  const out = await ask(cmd);
  console.log(`\n===== ${cmd} =====\n${out}`);
}

// Чужой запрос без секрета не должен обслуживаться.
const bad = await worker.fetch(new Request("https://w/", {
  method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ message: { chat: { id: 1 }, text: "/best" } }),
}), env);
console.log("\n===== без секрета =====\nHTTP", bad.status);
