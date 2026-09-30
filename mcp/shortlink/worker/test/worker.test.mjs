// 実行: node --no-warnings --test mcp/shortlink/worker/test/
import assert from "node:assert/strict";
import { test } from "node:test";
import { handle } from "../src/index.js";
import { fakeD1 } from "../dev/fake-d1.mjs";

const TOKEN = "test-token";

function setup() {
  const env = { DB: fakeD1(), ADMIN_TOKEN: TOKEN };
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p) };
  const call = async (path, { method = "GET", body, headers = {}, cf, auth = false } = {}) => {
    const request = new Request(`https://s.example.jp${path}`, {
      method,
      headers: { ...(auth ? { authorization: `Bearer ${TOKEN}` } : {}), ...headers },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (cf) request.cf = cf;
    const res = await handle(request, env, ctx);
    await Promise.all(pending.splice(0));
    return res;
  };
  return { env, call };
}

test("API は管理トークンが無いと 401", async () => {
  const { call } = setup();
  assert.equal((await call("/api/links")).status, 401);
  assert.equal((await call("/api/clicks", { headers: { authorization: "Bearer wrong" } })).status, 401);
});

test("リンクを作って転送し、地域・端末・アプリだけを記録する", async () => {
  const { env, call } = setup();
  const created = await call("/api/links", {
    method: "POST", auth: true,
    body: { target: "https://apps.apple.com/jp/app/id1", code: "ff-minato", label: "フリーペーパー港区", source: "print" },
  });
  assert.equal(created.status, 201);

  const iphoneInstagram = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Instagram 300.0";
  const res = await call("/ff-minato", {
    headers: { "user-agent": iphoneInstagram, referer: "https://l.instagram.com/?u=x" },
    cf: { country: "JP", regionCode: "13", region: "Tokyo", city: "Minato" },
  });
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("location"), "https://apps.apple.com/jp/app/id1");

  const row = env.DB.raw.prepare("SELECT * FROM clicks").get();
  assert.equal(row.region_code, "13");
  assert.equal(row.device, "ios");
  assert.equal(row.app, "instagram");
  assert.equal(row.referer_host, "l.instagram.com");
  assert.equal(row.is_bot, 0);
  assert.ok(!("ip" in row) && !("user_agent" in row));   // 個人を特定できる情報は持たない
});

test("リンクプレビューのボットは is_bot=1 で記録する", async () => {
  const { env, call } = setup();
  await call("/api/links", { method: "POST", auth: true, body: { target: "https://example.com", code: "abc" } });
  await call("/abc", { headers: { "user-agent": "facebookexternalhit/1.1" } });
  assert.equal(env.DB.raw.prepare("SELECT is_bot FROM clicks").get().is_bot, 1);
});

test("入力のチェック: URL・予約語・重複", async () => {
  const { call } = setup();
  const post = (body) => call("/api/links", { method: "POST", auth: true, body });
  assert.equal((await post({ target: "javascript:alert(1)" })).status, 400);
  assert.equal((await post({ target: "not a url" })).status, 400);
  assert.equal((await post({ target: "https://example.com", code: "api" })).status, 400);
  assert.equal((await post({ target: "https://example.com", code: "a b" })).status, 400);
  assert.equal((await post({ target: "https://example.com", code: "dup" })).status, 201);
  assert.equal((await post({ target: "https://example.com", code: "dup" })).status, 409);
  const auto = await (await post({ target: "https://example.com" })).json();
  assert.match(auto.code, /^[A-Za-z0-9]{6}$/);
});

test("クリック一覧は code と期間で絞れる（to は含まない）", async () => {
  const { env, call } = setup();
  await call("/api/links", { method: "POST", auth: true, body: { target: "https://example.com", code: "aaa" } });
  const insert = env.DB.raw.prepare("INSERT INTO clicks (code, ts, is_bot) VALUES (?, ?, 0)");
  insert.run("aaa", "2026-09-01T00:00:00.000Z");
  insert.run("aaa", "2026-09-02T00:00:00.000Z");
  insert.run("bbb", "2026-09-01T12:00:00.000Z");
  const body = await (await call("/api/clicks?code=aaa&from=2026-09-01T00:00:00Z&to=2026-09-02T00:00:00Z", { auth: true })).json();
  assert.deepEqual(body.clicks.map((c) => c.ts), ["2026-09-01T00:00:00.000Z"]);
  assert.equal(body.truncated, false);
  // タイムゾーン付きの指定も UTC にそろえて比べる（JST 9/1 9:00 = UTC 9/1 0:00）
  const jst = await (await call("/api/clicks?code=aaa&from=2026-09-01T09:00:00%2B09:00&to=2026-09-01T09:00:01%2B09:00", { auth: true })).json();
  assert.equal(jst.clicks.length, 1);
  assert.equal((await call("/api/clicks?from=yesterday", { auth: true })).status, 400);
});

test("存在しないコードは 404", async () => {
  const { call } = setup();
  assert.equal((await call("/nothing")).status, 404);
  assert.equal((await call("/")).status, 404);
});
