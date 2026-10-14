"""6R10B：验证 Rating Pro 分布（均值约 1.00、异常值、无 NaN/Infinity）。"""
from __future__ import annotations

import json
import statistics

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Match, PlayerStats
from app.services.rating_pro import build_rating

MATCH_IDS = [59, 60, 61, 62, 63]


def main() -> None:
    db = SessionLocal()
    ratings = []
    details = []
    try:
        for m in db.scalars(select(Match).where(Match.id.in_(MATCH_IDS))).all():
            for s in db.scalars(select(PlayerStats).where(PlayerStats.match_id == m.id)).all():
                raw = s.rating_raw
                if not raw:
                    continue
                r = build_rating(raw)
                if r["rating"] is None:
                    details.append({"match_id": m.id, "player_id": s.player_id, "status": r["status"],
                                    "reason": r.get("reason")})
                    continue
                ratings.append({
                    "match_id": m.id, "player_id": s.player_id, "rating": r["rating"],
                    "confidence": r["confidence"], "status": r["status"]})
                details.append({"match_id": m.id, "player_id": s.player_id, "rating": r["rating"],
                                "confidence": r["confidence"]})
    finally:
        db.close()

    vals = [x["rating"] for x in ratings]
    print(f"player-match 可算 Rating: {len(ratings)} / {len(details)}")
    print(f"unavailable: {len(details) - len(ratings)}")
    if vals:
        print("mean:", round(statistics.mean(vals), 4))
        print("median:", round(statistics.median(vals), 4))
        print("min:", min(vals), "max:", max(vals))
        print("stdev:", round(statistics.pstdev(vals), 4))
        print("p10:", round(sorted(vals)[max(0, int(len(vals)*0.1)-1)], 4))
        print("p90:", round(sorted(vals)[min(len(vals)-1, int(len(vals)*0.9))], 4))
        print("rating>1.5 count:", sum(1 for v in vals if v > 1.5))
        print("rating<0.6 count:", sum(1 for v in vals if v < 0.6))
        print("any nan/inf:", any(not (v == v) or v in (float('inf'), float('-inf')) for v in vals))
        print("\nsample:", json.dumps(ratings[:6], ensure_ascii=False))
    if ratings:
        print("\nunavailable details:", json.dumps(details[:len(details)-len(ratings)], ensure_ascii=False))


if __name__ == "__main__":
    main()
