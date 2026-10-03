"""ターミナルからデモを1本実行する。

    python -m agent.cli --list
    python -m agent.cli form_survey
"""

from __future__ import annotations

import argparse

from . import loop, scenarios

ICONS = {"note": "💬", "action": "🖱 ", "slack": "📣", "status": "▶ ", "done": "✅", "error": "⚠️ "}


def main() -> None:
    parser = argparse.ArgumentParser(description="Computer Use 営業デモ")
    parser.add_argument("scenario", nargs="?", help="シナリオID（scenarios/*.yml のファイル名）")
    parser.add_argument("--list", action="store_true", help="シナリオ一覧を表示")
    args = parser.parse_args()

    if args.list or not args.scenario:
        for sid, s in scenarios.load_all().items():
            print(f"{sid:16} {s['title']}（{s['audience']}）")
        return

    scenario = scenarios.load(args.scenario)
    print(f"=== {scenario['title']} ===")
    loop.run(scenario["task"], on_event=lambda e: print(ICONS.get(e["type"], ""), e["text"], flush=True))


if __name__ == "__main__":
    main()
