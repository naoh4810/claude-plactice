"""デモ用の架空の請求書PDFと一覧ページを生成する（Dockerビルド時に実行）。

わざと問題を2つ仕込んでいる：
- 2枚目：インボイス登録番号の記載がない
- 4枚目：税抜金額＋消費税が税込合計と一致しない
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent
FONT = "HeiseiKakuGo-W5"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))

INVOICES = [
    {
        "file": "invoice_01.pdf",
        "issuer": "株式会社さくらオフィス用品",
        "reg": "T1234567890123",
        "date": "2026年9月5日",
        "due": "2026年10月31日",
        "items": [("コピー用紙 A4（5000枚）", 4, 3200), ("トナーカートリッジ", 2, 8500)],
        "total_override": None,
    },
    {
        "file": "invoice_02.pdf",
        "issuer": "やまと清掃サービス",
        "reg": None,
        "date": "2026年9月10日",
        "due": "2026年10月10日",
        "items": [("事務所定期清掃（9月分）", 1, 33000)],
        "total_override": None,
    },
    {
        "file": "invoice_03.pdf",
        "issuer": "有限会社みどり印刷",
        "reg": "T9876543210987",
        "date": "2026年9月18日",
        "due": "2026年10月20日",
        "items": [("名刺印刷（100枚×6名）", 6, 2800), ("会社案内パンフレット", 300, 120)],
        "total_override": None,
    },
    {
        "file": "invoice_04.pdf",
        "issuer": "株式会社ネクストITサポート",
        "reg": "T5551234567890",
        "date": "2026年9月25日",
        "due": "2026年10月15日",
        "items": [("PC保守サポート（月額）", 1, 55000), ("出張設定作業", 2, 12000)],
        # 正しくは 79,000 + 7,900 = 86,900。わざとずらす
        "total_override": 89600,
    },
]


def yen(n: int) -> str:
    # CIDフォントでは「¥」がバックスラッシュで表示されるため「円」表記にする
    return f"{n:,}円"


def draw(inv: dict) -> dict:
    subtotal = sum(q * p for _, q, p in inv["items"])
    tax = subtotal // 10
    total = inv["total_override"] or subtotal + tax

    c = canvas.Canvas(str(OUT / inv["file"]), pagesize=A4)
    c.setTitle(f"請求書 {inv['issuer']}")
    w, h = A4
    c.setFont(FONT, 26)
    c.drawCentredString(w / 2, h - 70, "請 求 書")
    c.setFont(FONT, 11)
    c.drawRightString(w - 50, h - 100, f"請求日：{inv['date']}")
    c.setFont(FONT, 14)
    c.drawString(50, h - 140, "サンプル商事株式会社 御中")
    c.setFont(FONT, 11)
    y = h - 140
    for line in [inv["issuer"], "〒700-0000 岡山県岡山市北区（デモ）", "TEL 086-000-0000"]:
        c.drawRightString(w - 50, y, line)
        y -= 16
    if inv["reg"]:
        c.drawRightString(w - 50, y, f"登録番号：{inv['reg']}")

    c.setFont(FONT, 13)
    c.drawString(50, h - 210, f"ご請求金額（税込）　{yen(total)}")
    c.line(50, h - 216, 330, h - 216)
    c.setFont(FONT, 11)
    c.drawString(50, h - 240, f"お支払期限：{inv['due']}")

    y = h - 290
    c.setFillGray(0.92)
    c.rect(50, y - 6, w - 100, 22, fill=1, stroke=0)
    c.setFillGray(0)
    for x, label in [(56, "品目"), (330, "数量"), (400, "単価"), (480, "金額")]:
        c.drawString(x, y, label)
    for name, qty, price in inv["items"]:
        y -= 26
        c.drawString(56, y, name)
        c.drawRightString(360, y, str(qty))
        c.drawRightString(450, y, yen(price))
        c.drawRightString(w - 56, y, yen(qty * price))
    y -= 40
    for label, val in [("小計（税抜）", subtotal), ("消費税（10%）", tax), ("合計（税込）", total)]:
        c.drawString(380, y, label)
        c.drawRightString(w - 56, y, yen(val))
        y -= 20
    c.setFont(FONT, 9)
    c.drawString(50, 60, "※デモ用の架空の請求書です。")
    c.save()
    return {**inv, "total": total}


def main() -> None:
    rows = "\n".join(
        f'      <tr><td><a href="{inv["file"]}">{inv["file"]}</a></td>'
        f"<td>{inv['issuer']}</td><td>{inv['date']}</td></tr>"
        for inv in map(draw, INVOICES)
    )
    (OUT / "index.html").write_text(
        f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>受信トレイ - 請求書</title>
<style>
  body {{ font-family: "Noto Sans CJK JP", sans-serif; margin: 0; background: #f6f8fc; color: #202124; }}
  header {{ background: #1a73e8; color: #fff; padding: 14px 32px; font-size: 20px; }}
  main {{ max-width: 900px; margin: 24px auto; background: #fff; border-radius: 8px; padding: 20px 28px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 15px; }}
  th, td {{ border-bottom: 1px solid #e0e0e0; padding: 10px 8px; text-align: left; }}
  a {{ color: #1a73e8; }}
</style>
</head>
<body>
<header>受信トレイ ／ 請求書フォルダ</header>
<main>
  <h1>2026年9月 受信した請求書（{len(INVOICES)}件）</h1>
  <p>※デモ用の架空データです。ファイル名をクリックするとPDFが開きます。</p>
  <table>
    <thead><tr><th>ファイル</th><th>差出人</th><th>受信日</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
</main>
</body>
</html>
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
