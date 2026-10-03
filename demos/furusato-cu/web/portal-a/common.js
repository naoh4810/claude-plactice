// ポータルA共通：レイアウト版（v1 / v2）の判定と API 呼び出し
const LAYOUT = new URLSearchParams(location.search).get("layout") === "v2" ? "v2" : "v1";
document.documentElement.dataset.layout = LAYOUT;

async function api(method, url, body) {
  const res = await fetch(url, {
    method, headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) throw Object.assign(new Error(data.error || "error"), { data });
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
