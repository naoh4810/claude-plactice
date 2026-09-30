// 短縮リンクの転送役（Cloudflare Workers + D1）。
//
//   GET  /{code}      … 登録先へ 302 転送し、クリックを記録する
//   GET  /api/links   … リンク一覧（要 Authorization: Bearer <ADMIN_TOKEN>）
//   POST /api/links   … リンク作成 {target, code?, label?, source?}
//   GET  /api/clicks  … クリック記録 ?code=&from=&to=（UTC の ISO 日時）
//
// 個人を特定できる情報は保存しない。IP アドレスと User-Agent の全文は捨て、
// Cloudflare が付ける地域（国・都道府県・市区町村）と、端末の種類・アプリ名だけを残す。

const CODE_RE = /^[A-Za-z0-9_-]{3,32}$/;
const RESERVED = new Set(["api", "favicon.ico", "robots.txt"]);
const BOT_RE = /bot|crawler|spider|facebookexternalhit|whatsapp|preview|headless/i;
const MAX_CLICKS = 20000;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function device(ua) {
  if (/iPhone|iPad|iPod/.test(ua)) return "ios";
  if (/Android/.test(ua)) return "android";
  if (/Windows|Macintosh|Linux|CrOS/.test(ua)) return "desktop";
  return "other";
}

// SNS アプリ内のブラウザは User-Agent にアプリ名が入る（リファラが空でも流入元が分かる）
function inApp(ua) {
  if (/Instagram/.test(ua)) return "instagram";
  if (/FBAN|FBAV|FB_IAB/.test(ua)) return "facebook";
  if (/\bLine\//.test(ua)) return "line";
  if (/musical_ly|TikTok|BytedanceWebview/.test(ua)) return "tiktok";
  return null;
}

function refererHost(value) {
  try {
    return value ? new URL(value).host : null;
  } catch {
    return null;
  }
}

function randomCode(length = 6) {
  const chars = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // 見間違えやすい文字を除く
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return Array.from(bytes, (b) => chars[b % chars.length]).join("");
}

function authorized(request, env) {
  const header = request.headers.get("authorization") || "";
  return Boolean(env.ADMIN_TOKEN) && header === `Bearer ${env.ADMIN_TOKEN}`;
}

async function createLink(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "JSON を送ってください" }, 400);
  }
  let target;
  try {
    target = new URL(body.target);
  } catch {
    return json({ error: "target に転送先の URL を入れてください" }, 400);
  }
  if (!["http:", "https:"].includes(target.protocol)) {
    return json({ error: "転送先は http / https だけです" }, 400);
  }
  if (body.code !== undefined && body.code !== null && body.code !== "") {
    if (!CODE_RE.test(body.code) || RESERVED.has(body.code)) {
      return json({ error: "code は英数字・-・_ の3〜32文字で、api などの予約語以外にしてください" }, 400);
    }
  }
  const code = body.code || randomCode();
  const exists = await env.DB.prepare("SELECT code FROM links WHERE code = ?").bind(code).first();
  if (exists) return json({ error: `code「${code}」は使われています` }, 409);

  const link = {
    code,
    target: target.toString(),
    label: body.label || "",
    source: body.source || "",
    created_at: new Date().toISOString(),
  };
  await env.DB.prepare("INSERT INTO links (code, target, label, source, created_at) VALUES (?, ?, ?, ?, ?)")
    .bind(link.code, link.target, link.label, link.source, link.created_at)
    .run();
  return json(link, 201);
}

async function listLinks(env) {
  const { results } = await env.DB.prepare(
    "SELECT code, target, label, source, created_at FROM links ORDER BY created_at",
  ).all();
  return json({ links: results });
}

async function listClicks(url, env) {
  const where = [];
  const params = [];
  for (const [key, sql] of [["code", "code = ?"], ["from", "ts >= ?"], ["to", "ts < ?"]]) {
    let value = url.searchParams.get(key);
    if (!value) continue;
    if (key !== "code") {
      // ts は文字列で比べるので、保存時と同じ形式（…:00.000Z）にそろえる
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return json({ error: `${key} を日時として読めません` }, 400);
      value = date.toISOString();
    }
    where.push(sql);
    params.push(value);
  }
  const sql =
    "SELECT code, ts, country, region_code, region, city, referer_host, device, app, is_bot FROM clicks" +
    (where.length ? ` WHERE ${where.join(" AND ")}` : "") +
    ` ORDER BY ts LIMIT ${MAX_CLICKS + 1}`;
  const { results } = await env.DB.prepare(sql).bind(...params).all();
  return json({ clicks: results.slice(0, MAX_CLICKS), truncated: results.length > MAX_CLICKS });
}

async function redirect(request, env, ctx, code) {
  const link = await env.DB.prepare("SELECT target FROM links WHERE code = ?").bind(code).first();
  if (!link) {
    return new Response("リンクが見つかりません", { status: 404, headers: { "content-type": "text/plain; charset=utf-8" } });
  }
  const ua = request.headers.get("user-agent") || "";
  const cf = request.cf || {};
  const record = env.DB.prepare(
    "INSERT INTO clicks (code, ts, country, region_code, region, city, referer_host, device, app, is_bot) " +
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
  )
    .bind(
      code,
      new Date().toISOString(),
      cf.country || null,
      cf.regionCode || null,
      cf.region || null,
      cf.city || null,
      refererHost(request.headers.get("referer")),
      device(ua),
      inApp(ua),
      BOT_RE.test(ua) ? 1 : 0,
    )
    .run();
  // 記録を待たずに転送する（記録が失敗しても利用者は目的のページに行ける）
  ctx.waitUntil(record.catch((e) => console.error("click log failed", e)));
  return Response.redirect(link.target, 302);
}

export async function handle(request, env, ctx) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/^\/+|\/+$/g, "");

  if (path === "api/links" || path === "api/clicks") {
    if (!authorized(request, env)) return json({ error: "認証が必要です" }, 401);
    if (path === "api/links" && request.method === "POST") return createLink(request, env);
    if (path === "api/links" && request.method === "GET") return listLinks(env);
    if (path === "api/clicks" && request.method === "GET") return listClicks(url, env);
    return json({ error: "対応していないメソッドです" }, 405);
  }
  if (request.method === "GET" && CODE_RE.test(path) && !RESERVED.has(path)) {
    return redirect(request, env, ctx, path);
  }
  return new Response("Not Found", { status: 404 });
}

export default { fetch: handle };
