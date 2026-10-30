// クリック計測つきリダイレクト
//   GET /go?src=xxx   … クリックを記録して TARGET_URL へ 302 転送
//   GET /stats?key=…  … 都道府県別・配布元別・日別の集計画面
//   GET /stats.csv?key=… … 全クリックの CSV

const PREFECTURES = {
  "01": "北海道", "02": "青森県", "03": "岩手県", "04": "宮城県", "05": "秋田県",
  "06": "山形県", "07": "福島県", "08": "茨城県", "09": "栃木県", "10": "群馬県",
  "11": "埼玉県", "12": "千葉県", "13": "東京都", "14": "神奈川県", "15": "新潟県",
  "16": "富山県", "17": "石川県", "18": "福井県", "19": "山梨県", "20": "長野県",
  "21": "岐阜県", "22": "静岡県", "23": "愛知県", "24": "三重県", "25": "滋賀県",
  "26": "京都府", "27": "大阪府", "28": "兵庫県", "29": "奈良県", "30": "和歌山県",
  "31": "鳥取県", "32": "島根県", "33": "岡山県", "34": "広島県", "35": "山口県",
  "36": "徳島県", "37": "香川県", "38": "愛媛県", "39": "高知県", "40": "福岡県",
  "41": "佐賀県", "42": "長崎県", "43": "熊本県", "44": "大分県", "45": "宮崎県",
  "46": "鹿児島県", "47": "沖縄県",
};

// SNS のリンクプレビュー取得やクローラーは人のクリックではないので数えない
// （"Line/" は LINE アプリ内ブラウザ＝人なので除外しない）
const BOT_UA = /bot|crawl|spider|slurp|facebookexternalhit|slack|discord|whatsapp|telegram|skype|preview|embedly|curl|wget|python|headless|lighthouse/i;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/stats" || url.pathname === "/stats.csv") {
      if (!env.STATS_KEY || url.searchParams.get("key") !== env.STATS_KEY) {
        return new Response("Not found", { status: 404 });
      }
      return url.pathname === "/stats" ? statsPage(env, url) : statsCsv(env);
    }

    if (url.pathname === "/" || url.pathname === "/go") {
      const ua = request.headers.get("user-agent") || "";
      if (request.method === "GET" && !BOT_UA.test(ua)) {
        ctx.waitUntil(recordClick(env, request, url, ua));
      }
      return Response.redirect(env.TARGET_URL, 302);
    }

    return new Response("Not found", { status: 404 });
  },
};

async function recordClick(env, request, url, ua) {
  const cf = request.cf || {};
  const src = (url.searchParams.get("src") || "").match(/^[\w-]{1,40}$/)?.[0] || null;
  await env.DB.prepare(
    `INSERT INTO clicks (ts, src, country, region_code, region, city, device, referer)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
  ).bind(
    new Date().toISOString(),
    src,
    cf.country || null,
    cf.regionCode || null,
    cf.region || null,
    cf.city || null,
    deviceOf(ua),
    (request.headers.get("referer") || "").slice(0, 300) || null,
  ).run();
}

function deviceOf(ua) {
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) return "Android";
  if (/Macintosh|Windows|Linux/.test(ua)) return "PC";
  return "other";
}

function placeName(country, regionCode, region) {
  if (country === "JP") return PREFECTURES[regionCode] || region || "日本（県不明）";
  if (!country) return "不明";
  return `海外（${country}）`;
}

async function statsPage(env, url) {
  const days = Math.min(Math.max(parseInt(url.searchParams.get("days") || "30", 10) || 30, 1), 3650);
  const since = new Date(Date.now() - days * 86400_000).toISOString();
  const q = (sql) => env.DB.prepare(sql).bind(since).all().then((r) => r.results);

  const [byPlace, bySrc, byDay, byDevice] = await Promise.all([
    q(`SELECT country, region_code, region, COUNT(*) n FROM clicks WHERE ts >= ?
       GROUP BY country, region_code, region`),
    q(`SELECT COALESCE(src, '（指定なし）') k, COUNT(*) n FROM clicks WHERE ts >= ? GROUP BY k ORDER BY n DESC`),
    // 日付は日本時間で区切る
    q(`SELECT date(ts, '+9 hours') k, COUNT(*) n FROM clicks WHERE ts >= ? GROUP BY k ORDER BY k DESC`),
    q(`SELECT device k, COUNT(*) n FROM clicks WHERE ts >= ? GROUP BY k ORDER BY n DESC`),
  ]);

  // regionCode が取れない行も同じ県にまとめる
  const places = new Map();
  for (const r of byPlace) {
    const name = placeName(r.country, r.region_code, r.region);
    places.set(name, (places.get(name) || 0) + r.n);
  }
  const placeRows = [...places].map(([k, n]) => ({ k, n })).sort((a, b) => b.n - a.n);
  const total = placeRows.reduce((s, r) => s + r.n, 0);

  const key = encodeURIComponent(url.searchParams.get("key"));
  const html = `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>クリック集計</title>
<style>
body{font-family:system-ui,sans-serif;margin:0 auto;max-width:760px;padding:16px;color:#222;background:#fff}
h1{font-size:1.3rem}h2{font-size:1.05rem;margin-top:2rem}
table{border-collapse:collapse;width:100%}td,th{padding:6px 8px;border-bottom:1px solid #eee;text-align:left}
td.n{text-align:right;font-variant-numeric:tabular-nums;width:4em}
.bar{background:#4f7cff;height:10px;border-radius:3px}
nav a{margin-right:12px}
</style></head><body>
<h1>クリック集計（直近${days}日・合計 ${total} 件）</h1>
<nav>期間: ${[7, 30, 90, 365].map((d) => `<a href="?key=${key}&days=${d}">${d}日</a>`).join("")}
 | <a href="/stats.csv?key=${key}">CSVダウンロード</a></nav>
${table("都道府県別", placeRows, total)}
${table("配布元（src）別", bySrc, total)}
${table("端末別", byDevice, total)}
${table("日別（日本時間）", byDay, total, false)}
<p style="color:#888;font-size:.85rem">地域は IP アドレスからの推定です。モバイル回線では県がずれることがあります。</p>
</body></html>`;
  return new Response(html, { headers: { "content-type": "text/html; charset=utf-8" } });
}

function table(title, rows, total, withBar = true) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  const body = rows.length
    ? rows.map((r) => `<tr><td>${esc(r.k)}</td><td class="n">${r.n}</td>` +
        (withBar ? `<td><div class="bar" style="width:${(r.n / max) * 100}%"></div></td>` : "") +
        `<td class="n">${total ? Math.round((r.n / total) * 100) : 0}%</td></tr>`).join("")
    : `<tr><td>まだデータがありません</td></tr>`;
  return `<h2>${title}</h2><table>${body}</table>`;
}

async function statsCsv(env) {
  const { results } = await env.DB.prepare(
    `SELECT ts, src, country, region_code, region, city, device, referer FROM clicks ORDER BY ts`
  ).all();
  const header = "日時(日本時間),配布元,国,都道府県,地域(英),市区町村,端末,参照元";
  const lines = results.map((r) => [
    new Date(new Date(r.ts).getTime() + 9 * 3600_000).toISOString().slice(0, 19).replace("T", " "),
    r.src, r.country, r.country === "JP" ? PREFECTURES[r.region_code] || "" : "",
    r.region, r.city, r.device, r.referer,
  ].map(csvCell).join(","));
  // BOM 付きにして Excel で文字化けしないようにする
  return new Response("﻿" + [header, ...lines].join("\r\n"), {
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition": 'attachment; filename="clicks.csv"',
    },
  });
}

function csvCell(v) {
  const s = v == null ? "" : String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
