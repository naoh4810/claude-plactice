// Cloudflare D1 の最小限の代わり（Node 22 の node:sqlite を使う）。テストと手元での動作確認用。
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

const SCHEMA = new URL("../schema.sql", import.meta.url);

function statement(db, sql, params) {
  return {
    bind: (...values) => statement(db, sql, values),
    first: async () => {
      const row = db.prepare(sql).get(...params);
      return row ? { ...row } : null;
    },
    all: async () => ({ results: db.prepare(sql).all(...params).map((row) => ({ ...row })) }),
    run: async () => {
      db.prepare(sql).run(...params);
      return { success: true };
    },
  };
}

export function fakeD1(path = ":memory:") {
  const db = new DatabaseSync(path);
  db.exec(readFileSync(SCHEMA, "utf8"));
  return { prepare: (sql) => statement(db, sql, []), raw: db };
}
