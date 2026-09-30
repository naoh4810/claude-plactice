// Cloudflare にデプロイせずに、手元で短縮リンクを動かすための開発用サーバー。
//
//   ADMIN_TOKEN=dev-token node --no-warnings mcp/shortlink/worker/dev/server.mjs [port] [dbファイル]
//
// 本番の Cloudflare は地域情報（request.cf）を自動で付けるが、手元では付かないので、
// X-Dev-Country / X-Dev-Region-Code / X-Dev-Region ヘッダーで代わりに渡せるようにしている。
import http from "node:http";
import { handle } from "../src/index.js";
import { fakeD1 } from "./fake-d1.mjs";

const port = Number(process.argv[2] || 8787);
const env = { DB: fakeD1(process.argv[3] || ":memory:"), ADMIN_TOKEN: process.env.ADMIN_TOKEN || "dev-token" };

http
  .createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = chunks.length ? Buffer.concat(chunks) : undefined;
    const request = new Request(`http://localhost:${port}${req.url}`, {
      method: req.method,
      headers: req.headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : body,
    });
    request.cf = {
      country: req.headers["x-dev-country"],
      regionCode: req.headers["x-dev-region-code"],
      region: req.headers["x-dev-region"],
    };
    const pending = [];
    const response = await handle(request, env, { waitUntil: (p) => pending.push(p) });
    await Promise.all(pending);
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  })
  .listen(port, () => console.log(`shortlink dev server: http://localhost:${port}`));
