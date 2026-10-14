"""6R10A：重新解析 demo 19–23（权威回合边界修复后回填全部数据）。

在容器内运行（Postgres 仅容器网络内可达）：
    docker cp scripts/reparse_6r10_demos.py cs2demo-backend-1:/app/reparse_6r10_demos.py
    docker exec cs2demo-backend-1 python reparse_6r10_demos.py [19 20 21 22 23]

对每个 demo：
1. 删除旧 match 及其子行（players/player_stats/events/round_sides/artifacts/heatmaps）。
2. 重置 demo.match_id=None、status='uploaded'。
3. 调用 process_demo(demo_id) 重新解析（从 MinIO 下载原文件，重建全部表与 artifact）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models import (
    Demo, DemoFocusPlayer, Event, HeatmapCache, Match, Player, PlayerStats,
    RouteArtifact, RoundSide, TacticalArtifact,
)
from app.tasks.parse_tasks import process_demo

DEFAULT_TARGETS = [19, 20, 21, 22, 23]


def reset_match(db, match_id: int) -> None:
    """删除该 match 及其全部子行（依赖序），并清掉 artifact manifest。"""
    if match_id is None:
        return
    for model in (Event, PlayerStats, HeatmapCache):
        db.execute(delete(model).where(model.match_id == match_id))
    for model in (RouteArtifact, TacticalArtifact):
        db.execute(delete(model).where(model.match_id == match_id))
    db.execute(delete(RoundSide).where(RoundSide.match_id == match_id))
    # demo_focus_players 引用 players，须先删
    player_ids = db.scalars(
        select(Player.id).where(Player.match_id == match_id)
    ).all()
    if player_ids:
        db.execute(delete(DemoFocusPlayer).where(DemoFocusPlayer.player_id.in_(player_ids)))
    db.execute(delete(Player).where(Player.match_id == match_id))
    db.execute(delete(Match).where(Match.id == match_id))


def main() -> None:
    targets = [int(x) for x in sys.argv[1:]] or DEFAULT_TARGETS
    db = SessionLocal()
    try:
        for demo_id in targets:
            demo = db.get(Demo, demo_id)
            if demo is None:
                print(f"demo {demo_id}: 不存在，跳过")
                continue
            old_match = demo.match_id
            if old_match is not None:
                # 先解除 demos.match_id 引用，再删 match 及其子行（依赖序）
                demo.match_id = None
                db.flush()
                reset_match(db, old_match)
                db.flush()
            demo.status = "uploaded"
            demo.parse_warnings = None
            demo.heatmap_warnings = None
            demo.error_msg = None
            db.commit()
            print(f"demo {demo_id}: 已重置旧 match={old_match}，开始重新解析…", flush=True)
            try:
                res = process_demo(demo_id)
                print(f"  -> {res}", flush=True)
            except Exception as exc:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                print(f"  !! demo {demo_id} 重解析失败: {exc}", flush=True)
                db.rollback()
    finally:
        db.close()
    print("done")


if __name__ == "__main__":
    main()
