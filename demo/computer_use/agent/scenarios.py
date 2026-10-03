"""scenarios/*.yml（業種別デモシナリオ）の読み込み。"""

from __future__ import annotations

from pathlib import Path

import yaml

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios"


def load_all() -> dict[str, dict]:
    scenarios = {}
    for path in sorted(SCENARIO_DIR.glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["id"] = path.stem
        scenarios[path.stem] = data
    return scenarios


def load(scenario_id: str) -> dict:
    scenarios = load_all()
    if scenario_id not in scenarios:
        raise SystemExit(f"シナリオが見つかりません: {scenario_id}（候補: {', '.join(scenarios)}）")
    return scenarios[scenario_id]
