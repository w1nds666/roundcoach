"""6R10B：Rating Pro v1 验收样本内标定（demo 19–23，约 50 player-match）。

从 player_stats.rating_raw 读取各分量原始值，计算样本均值作为归一化常数，
写入 backend/app/services/rating_pro_calibration.json，并输出分布/异常值/敏感性
（用于 docs/phase6r10-calibration.md 的诚实边界）。

在容器内运行：
    docker cp scripts/calibrate_rating_pro.py cs2demo-backend-1:/app/calibrate_rating_pro.py
    docker exec cs2demo-backend-1 python calibrate_rating_pro.py
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Match, PlayerStats
from app.services.rating_pro import RATING_PRO_VERSION

MATCH_IDS = [59, 60, 61, 62, 63]
OUT = Path("/app/app/services/rating_pro_calibration.json")
HOST_OUT = Path(__file__).resolve().parent.parent / "backend" / "app" / "services" / "rating_pro_calibration.json"


def _stats(values):
    if not values:
        return None
    import statistics
    n = len(values)
    mean = statistics.mean(values)
    vals_sorted = sorted(values)
    med = statistics.median(values)
    p10 = vals_sorted[max(0, int(n * 0.1))]
    p90 = vals_sorted[min(n - 1, int(n * 0.9))]
    stdev = statistics.pstdev(values)
    mn, mx = min(values), max(values)
    return {"n": n, "mean": round(mean, 5), "median": round(med, 5),
            "p10": round(p10, 5), "p90": round(p90, 5), "min": round(mn, 5),
            "max": round(mx, 5), "stdev": round(stdev, 5)}


def main() -> None:
    db = SessionLocal()
    try:
        comp_values = {"frag": [], "survival": [], "kast": [], "adr": [], "trade": []}
        raw_records = []
        for m in db.scalars(select(Match).where(Match.id.in_(MATCH_IDS))).all():
            for s in db.scalars(select(PlayerStats).where(PlayerStats.match_id == m.id)).all():
                if not s.rating_raw:
                    continue
                comps = s.rating_raw.get("components", {})
                rec = {"match_id": m.id, "player_id": s.player_id, "map": m.map_name,
                       "rounds": s.rating_raw.get("rounds"), "components": {}}
                for key in comp_values:
                    c = comps.get(key) or {}
                    v = c.get("value")
                    if v is None:
                        continue
                    if key == "trade" and c.get("status") != "ok":
                        continue
                    try:
                        fv = float(v)
                    except (TypeError, ValueError):
                        continue
                    comp_values[key].append(fv)
                    rec["components"][key] = fv
                raw_records.append(rec)
        db.close()
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass

    cal = {}
    summary = {}
    for key in comp_values:
        st = _stats(comp_values[key])
        summary[key] = st
        if st and st["mean"] > 0:
            cal[key] = round(st["mean"], 5)
        else:
            cal[key] = 0.0
    # trade 若样本量过小则回退 0（不得用虚构常数的占位——用 0 使该分量 unavailable）
    if not cal.get("trade") or summary["trade"]["n"] < 5:
        cal["trade"] = 0.0
        summary["trade"]["note"] = "trade 机会样本过少，标定置 0 → 该分量视为 calibration_missing（不计分）"

    payload = {"version": RATING_PRO_VERSION, "scope": "验收样本内 demo19-23", "cal": cal,
               "summary": summary,
               "note": "均值约 1.00 仅为验收样本内标定，非行业/全球基线"}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    HOST_OUT.parent.mkdir(parents=True, exist_ok=True)
    HOST_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"cal": cal, "summary": summary}, ensure_ascii=False, indent=2))
    print(f"wrote {OUT} and {HOST_OUT}")


if __name__ == "__main__":
    main()
