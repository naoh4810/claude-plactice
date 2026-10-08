---
name: job-apply
description: >-
  Generate a proposal (応募文) for a crowdsourcing job from the user's job ledger by its 6-digit ID.
  Use when the user says things like "000313に応募したい", "000313の応募文を作って",
  "313に応募", "この案件に応募したい ID 000313", or marks a job as sent/skipped
  ("000313 送信した", "000313 やめる"). Never submits the application — the user sends it themselves.
---

# 案件への応募文を作る（job-apply）

案件チェック自動化（`scripts/job_hunter.py`）が台帳 `data/jobs.csv` に記録した案件を、
ID（6桁、例 `000313`）で指定して応募文を生成する。**送信は絶対にしない**（最後は本人がサイトで確認して送信ボタンを押す）。

## 手順

1. ユーザーの発言から ID を取り出し、6桁にゼロ埋めする（`313` → `000313`）。
2. 台帳の該当行を確認して、案件名・判定・URL を一言で伝える:
   `grep '^000313,' data/jobs.csv`
   - 見つからなければ、その旨と直近の 〇△ 案件のIDをいくつか示して終わる。
3. 応募文を生成する:
   `python scripts/job_apply.py 000313`
   - 生成物は `data/proposals/000313.md` に保存され、台帳の状態は「応募文作成済」になる。
4. 生成された「応募文」と「送信前チェック」をそのまま見せる。特に `【要確認】` の箇所を目立たせ、
   金額・納期など本人が決めるべき点を質問する。ユーザーの回答で応募文を直す場合は
   `data/proposals/000313.md` を編集する。
5. 最後に案件URLを示し、「内容を確認してサイト上で送信してください」と伝える。

## 状態の更新

- 「送信した」「応募した」→ `python scripts/job_apply.py 000313 --sent`（応募済）
- 「やめる」「見送り」→ `python scripts/job_apply.py 000313 --skip`（見送り）

## 注意

- プロフィール（`config/profile.yml`）に無い実績を応募文に足さない。足りなければ本人に聞いてプロフィールに追記してもらう。
- `ANTHROPIC_API_KEY` が必要。`data/` はローカルの個人データなのでコミットしない。
