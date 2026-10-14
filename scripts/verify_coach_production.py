"""6R15：生产画像验收 —— demo19-23 真实数据生成 profile snapshot，两次 hash 一致。

诚实边界：只用「真实解析数据 + 绑定在 roster 的 demo」；不为凑 10/30 满样本伪造比赛。
用法（在 backend 容器内运行）：
    docker cp scripts/verify_coach_production.py cs2demo-backend-1:/app/verify_coach_production.py
    docker exec cs2demo-backend-1 python verify_coach_production.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Demo, Match, Player, PlayerStats, SteamIdentity, User
from app.services.security import hash_password

# 真实验收样本（demo19-23）对应 match_id；以及我们要绑定的 SteamID64。
MATCH_IDS = [59, 60, 61, 62, 63]
# demo19-23 中真实在 roster 最多的实体身份（实测 4/5 场有完整 player_stats）。
# 产品侧的「我」（6R14 绑定的 Steam) 仅出现在 demo19/20（2/5），为诚实呈现多场真实画像，
# 这里绑定 demo19-23 覆盖最多的真实身份（陆言），并如实报告覆盖（4/10、4/30），不伪造 5/10。
# 6R19 P0：个人 SteamID64 不写死在仓库，改由环境变量注入（未提供则 fail-fast）。
BIND_STEAM = os.environ.get("COACH_VERIFY_STEAM", "")
if not BIND_STEAM:
    raise SystemExit("请设置环境变量 COACH_VERIFY_STEAM=<steam_id64> 再运行（避免把个人标识写死进仓库）")
EMAIL = "coach.prod@test.com"


def _bind_user(db):
    u = db.scalar(select(User).where(User.email == EMAIL))
    if u is None:
        u = User(email=EMAIL, password_hash=hash_password("password123"), nickname="教练验收",
                 subscription_tier="max",
                 subscription_expire=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=365))
        db.add(u)
        db.flush()
    # 幂等绑定（先解除该用户已有 active；再对该 steam 做 upsert，避免唯一约束冲突）
    for st in db.scalars(select(SteamIdentity).where(SteamIdentity.user_id == u.id)).all():
        st.status = "unbound"
    db.flush()
    row = db.scalar(select(SteamIdentity).where(SteamIdentity.steam_id64 == BIND_STEAM))
    if row is None:
        db.add(SteamIdentity(provider="steam", subject=BIND_STEAM, steam_id64=BIND_STEAM,
                             user_id=u.id, status="active"))
    else:
        row.status = "active"
        row.user_id = u.id
        row.version = (row.version or 0) + 1
    db.commit()
    return u


def _real_demo_matches(db, steam: str):
    """返回 steam 真实在 roster 且已解析的 demos（按时间升序）。"""
    rows = db.execute(
        select(Demo, Match)
        .join(Match, Match.id == Demo.match_id)
        .join(Player, Player.match_id == Match.id)
        .where(Player.steam_id == steam, Demo.status == "parsed", Demo.match_id.isnot(None))
        .distinct()
        .order_by(Demo.created_at.asc())
    ).all()
    return [(d, m) for d, m in rows]


def main() -> None:
    db = SessionLocal()
    try:
        u = _bind_user(db)
        from app.services.coach import compute_match_snapshot, profile_snapshot  # noqa: PLC0415

        eligible = _real_demo_matches(db, BIND_STEAM)
        print(f"绑定 Steam={BIND_STEAM}；真实可纳入的解析完成 demo 数量 = {len(eligible)}")
        for d, m in eligible:
            snap = compute_match_snapshot(db, u, d)
            print(f"  demo {d.id} ({m.map_name}) match_id={m.id} rounds={m.rounds} "
                  f"hash={snap.content_hash[:12]} facts_ok="
                  f"{sum(1 for f in snap.facts.values() if f.get('status') == 'ok')}")
        prof1 = profile_snapshot(db, u, window=30)
        prof2 = profile_snapshot(db, u, window=30)
        h1, h2 = prof1.content_hash, prof2.content_hash
        print(f"profile content_hash 第一次 = {h1}")
        print(f"profile content_hash 第二次 = {h2}")
        print(f"deterministic hash 两次一致 = {h1 == h2}")
        print(f"coverage(window_10) = {prof1.coverage.get('window_10')}")
        print(f"coverage(window_30) = {prof1.coverage.get('window_30')}")
        print(f"repeated_problems = {len(prof1.repeated_problems)}; top3 = {len(prof1.top3)}")
        for t in prof1.top3:
            print(f"  top3[{t['rank']}] {t['human_name']} score={t['score']:.3f} "
                  f"repeat={t['repeat']} confidence={t['confidence']} evidence={t['evidence_count']}")
        if h1 != h2:
            print("FAIL: deterministic hash 不一致")
            sys.exit(1)
        print("PASS: 生产 demo19-23 真实画像生成成功且 deterministic hash 两次一致")
    finally:
        db.close()


if __name__ == "__main__":
    main()
