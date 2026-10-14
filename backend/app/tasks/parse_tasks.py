"""Celery 解析任务（计划书 7.1 流程、4.3 tasks/parse_tasks.py）。

流程：
1. 接收 demo_id → 状态置 parsing
2. demoparser2 解析（header/players/rounds/deaths/hurts/grenades）
3. 数据入库：matches/players/player_stats/events（含 tick→round 映射）
4. 状态置 parsed；异常 → failed + error_msg
（击杀热力图生成与 MinIO 存储属于 Phase 2，届时扩展本任务）

grenade 聚合：parse_grenades 为 tick 级（51万行/场），按实体聚合为事件级
（决策点 3 方案 B：thrown 时刻 + 类型 + 位置）。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Demo, Event, HeatmapCache, Match, Player, PlayerStats, RouteArtifact, TacticalArtifact
from app.services.heatmap import generate_heatmap_bytes, generate_heatmap_grid
from app.services.parser import DemoAnalyzer
from app.services.routes import artifact_sha256, build_route_artifact, gzip_json, route_object_key
from app.services.stats import compute_basic_stats, compute_match_score
from app.services.storage import storage
from app.services.rating_pro import compute_rating_raw
from app.services.tactical import TACTICAL_SCHEMA_VERSION, build_tactical_artifact, tactical_object_key
from app.tasks import celery_app

# 6R19：playout_ticks 来自不可信 demo 二进制（CDemoFileInfo），攻击者可控且可被构造成天文数字，
# 若直接用于 `range(0, playout_ticks, 8)` 会导致 worker 采样数亿次 → CPU/内存耗尽（解析服务 DoS）。
# 合法 CS2 回放最长约数小时、64/128 tick，远低于此上限；这里对采样上界做防御性钳制。
MAX_ROUTE_SAMPLE_TICKS = 5_000_000

# 投掷物类型归一化：demoparser2 Projectile 实体 → 计划书 events.grenade_type
# 注意：parse_grenades 还包含玩家手持武器实体（CKnife/CC4/CWeaponGlock 等），
# 聚合时必须仅保留 *Projectile 结尾的投掷物实体（Phase 4B 实测修复）
GRENADE_TYPE_MAP = {
    "CSmokeGrenadeProjectile": "smoke",
    "CFlashbangProjectile": "flash",   # 注意：无 S（实测 demoparser2 类型名）
    "CHEGrenadeProjectile": "he",
    "CMolotovProjectile": "molotov",
    "CIncendiaryGrenadeProjectile": "incendiary",
    "CDecoyGrenadeProjectile": "decoy",
}

TEAM_MAP = {2: "CT", 3: "T"}


def _clean_coord(v) -> float | None:
    """坐标清洗：is not None + finite；NaN/Inf/None → None（6R1，禁止 0 值回退）。"""
    import math

    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _save_heatmap(db, match: Match, map_name: str, mode: str, points: list) -> None:
    """生成热力图 PNG + 密度网格 JSON → MinIO → heatmap_cache 记录（Phase 4B 双输出）。

    视觉升级（Phase 2.1）：kills/route_all 通用热色，route_ct 蓝 / route_t 橙（语义色）。
    """
    from app.services.heatmap import PALETTE_FOR_MODE

    palette = PALETTE_FOR_MODE.get(mode, "heat")
    png = generate_heatmap_bytes(points, map_name, palette_name=palette)
    key = f"heatmaps/{match.id}/{mode}.png"
    storage.upload_bytes(key, png)

    grid = generate_heatmap_grid(points, map_name)
    import json

    grid_key = f"heatmaps/{match.id}/{mode}.json"
    storage.upload_bytes(grid_key, json.dumps(grid, ensure_ascii=False).encode("utf-8"), content_type="application/json")

    db.add(HeatmapCache(
        match_id=match.id,
        map_name=map_name,
        mode=mode,
        heatmap_key=key,
    ))


def _save_route_artifact(db, analyzer: DemoAnalyzer, match: Match, map_name: str, playout_ticks: int, rounds: list,
                         players: list, identity_of: dict, player_id_of: dict, deaths: list,
                         tick_rate: float | None = None) -> dict:
    """生成版本化压缩路线 artifact → MinIO → 落 manifest（6R4）。

    幂等：内容由 demo 确定性推导，同一输入生成相同字节/sha256；manifest 按 match 唯一（upsert）。
    tick_rate 为权威时基（来自解析 header）；None 表示无法证明 → 不显示秒（API 层处理）。
    """
    from app.services.routes import ROUTE_SCHEMA_VERSION

    artifact = build_route_artifact(
        analyzer, match.id, map_name, playout_ticks, rounds, players, identity_of, player_id_of, deaths,
        tick_rate=tick_rate,
    )
    data = gzip_json(artifact)
    key = route_object_key(match.id, ROUTE_SCHEMA_VERSION)
    storage.upload_bytes(key, data, content_type="application/gzip")
    sha = artifact_sha256(data)

    existing = db.scalar(select(RouteArtifact).where(RouteArtifact.match_id == match.id))
    if existing is None:
        db.add(RouteArtifact(match_id=match.id, schema_version=ROUTE_SCHEMA_VERSION, object_key=key,
                             size_bytes=len(data), sha256=sha,
                             player_count=len(player_id_of), round_count=len(rounds)))
    else:
        existing.schema_version = ROUTE_SCHEMA_VERSION
        existing.object_key = key
        existing.size_bytes = len(data)
        existing.sha256 = sha
        existing.player_count = len(player_id_of)
        existing.round_count = len(rounds)
    return {"key": key, "size_bytes": len(data), "sha256": sha, "rounds": len(rounds)}


def _save_tactical_artifact(db, analyzer: DemoAnalyzer, match: Match, map_name: str, playout_ticks: int,
                            rounds: list, players: list, identity_of: dict, player_id_of: dict,
                            deaths: list, grenades: list, tick_rate: float | None = None) -> dict:
    """生成版本化压缩战术 artifact（6R8 v2.0）→ MinIO → 落 manifest。

    幂等：内容由 demo 确定性推导；manifest 按 match 唯一（upsert）。
    tick_rate 为权威时基（来自解析 header）；None 表示无法证明 → 不显示秒（API 层处理）。
    """
    from app.services.tactical import TACTICAL_SCHEMA_VERSION, artifact_sha256

    artifact = build_tactical_artifact(
        analyzer, match.id, map_name, playout_ticks, rounds, players,
        identity_of, player_id_of, deaths, grenades, tick_rate=tick_rate,
    )
    data = gzip_json(artifact)
    key = tactical_object_key(match.id, TACTICAL_SCHEMA_VERSION)
    storage.upload_bytes(key, data, content_type="application/gzip")
    sha = artifact_sha256(data)

    existing = db.scalar(select(TacticalArtifact).where(TacticalArtifact.match_id == match.id))
    if existing is None:
        db.add(TacticalArtifact(match_id=match.id, schema_version=TACTICAL_SCHEMA_VERSION, object_key=key,
                                size_bytes=len(data), sha256=sha,
                                player_count=len(player_id_of), round_count=len(rounds),
                                prop_count=artifact.get("prop_count", 0)))
    else:
        existing.schema_version = TACTICAL_SCHEMA_VERSION
        existing.object_key = key
        existing.size_bytes = len(data)
        existing.sha256 = sha
        existing.player_count = len(player_id_of)
        existing.round_count = len(rounds)
        existing.prop_count = artifact.get("prop_count", 0)
    return {"key": key, "size_bytes": len(data), "sha256": sha, "rounds": len(rounds),
            "props": artifact.get("prop_count", 0)}


@celery_app.task(
    name="app.tasks.parse_tasks.process_demo",
    soft_time_limit=540,   # 6R19：解析硬超时保护（秒）——防不可信 demo 长时间高 CPU/OOM 卡死 worker
    time_limit=600,
)
def process_demo(demo_id: int) -> dict:
    """解析单个 demo 并入库。demo.file_path 指向本地文件（容器内 /demos 挂载）。

    注意：不启用 Celery 自动重试（非幂等，重复执行会撞唯一约束）；
    失败即置 failed，由人工/管理端重新投递。
    """
    db = SessionLocal()
    try:
        demo = db.get(Demo, demo_id)
        if demo is None:
            raise ValueError(f"demo {demo_id} not found")

        # 幂等保护：已解析且已有 match → 直接返回
        if demo.status == "parsed" and demo.match_id is not None:
            return {"demo_id": demo.id, "status": "already_parsed", "match_id": demo.match_id}

        demo.status = "parsing"
        db.commit()

        path = demo.file_path
        # 上传的 demo 存 MinIO（key 以 demos/ 开头）→ 拉取到临时文件解析；
        # 开发期本地路径（/demos/... 挂载）直接使用
        tmp_file = None
        if path.startswith("demos/"):
            import tempfile

            data = storage.get_bytes(path)
            with tempfile.NamedTemporaryFile(suffix=".dem", delete=False) as f:
                f.write(data)
                tmp_file = f.name
            path = tmp_file
        if not Path(path).exists():
            raise FileNotFoundError(f"demo file not found: {path}")

        analyzer = DemoAnalyzer(path)
        header = analyzer.parse_header()
        rounds = analyzer.parse_rounds()
        players = analyzer.parse_player_info()
        deaths = analyzer.parse_deaths()
        hurts = analyzer.parse_hurts()
        grenades = analyzer.parse_grenades()

        # ---------- matches ----------
        # 6R0：队伍比分（roster 贯穿换边）；score_ct/score_t 保留为阵营胜局（兼容）
        team_a, team_b = compute_match_score(rounds)
        side_ct = sum(1 for r in rounds if r.get("winner_side") == 2)
        side_t = sum(1 for r in rounds if r.get("winner_side") == 3)
        match = Match(
            demo_id=demo.id,
            map_name=header.map_name,
            score_ct=side_ct,
            score_t=side_t,
            team_a_score=team_a,
            team_b_score=team_b,
            team_a_name="队伍 A",
            team_b_name="队伍 B",
            duration_seconds=int(header.duration_seconds),
            rounds=len(rounds),
        )
        db.add(match)
        db.flush()  # 拿 match.id

        # ---------- players ----------
        mapper = analyzer.round_mapper(rounds)
        name_of: dict[str, str] = {}
        player_rows: list[Player] = []
        # 6R1：队伍身份（team_identity）由首回合 roster 绑定（A=开局 CT 侧）
        identity_of: dict[str, str] = {}
        if rounds:
            first_side = rounds[0]["team_side_map"]          # {"A": 2, "B": 3}
            identity_by_side = {v: k for k, v in first_side.items()}
            for sid, team in analyzer._build_roster(rounds[0]["start_tick"]).items():
                identity_of[sid] = team
        for p in players:
            sid = str(p["steamid"])
            name_of[sid] = p["name"]
            player_rows.append(Player(
                match_id=match.id,
                steam_id=sid,
                name=p["name"],
                team=TEAM_MAP.get(p.get("team_number"), ""),
                team_identity=identity_of.get(sid),
            ))
        db.add_all(player_rows)
        db.flush()

        # 6R1：round_sides（每回合队伍 A/B 阵营，个人交战图 side 数据地基）
        from app.models import RoundSide

        db.add_all([
            RoundSide(match_id=match.id, round=r["round"],
                      side_a=r["team_side_map"]["A"], side_b=r["team_side_map"]["B"])
            for r in rounds
        ])

        # ---------- player_stats ----------
        steamids = list(name_of.keys())
        for d in deaths:
            d["round"] = mapper(d["tick"])
        for h in hurts:
            h["round"] = mapper(h["tick"])
        stats = compute_basic_stats(deaths, hurts, steamids, rounds)
        player_id_of = {pl.steam_id: pl.id for pl in player_rows}
        # 6R10B：Rating Pro v1 原始分量（解析期确定性计算，供读取时按标定归一化）
        raw_ratings = compute_rating_raw(rounds, deaths, stats, steamids, identity_of, header.tick_rate)
        for sid, s in stats.items():
            db.add(PlayerStats(
                match_id=match.id,
                player_id=player_id_of[sid],
                kills=s["kills"],
                deaths=s["deaths"],
                assists=s["assists"],
                hs_kills=s["hs_kills"],
                hs_rate=s["hs_rate"],
                damage=s["damage"],
                adr=s["adr"],
                first_kills=s["first_kills"],
                weapon_stats=s["weapon_stats"],
                rating_raw=raw_ratings.get(sid),
            ))

        # ---------- events（kill 事件，6R1 结构化坐标：actor=攻击者、victim=受害者） ----------
        # 坐标分别来自 attacker_X/Y 与 user_X/Y；is not None + finite 清洗；
        # 禁止 A or B 坐标回退；环境击杀/攻击者缺失 → actor 坐标 NULL（显式表示）。
        kill_events = []
        for d in deaths:
            actor = str(d.get("attacker_steamid") or "")
            victim = str(d.get("user_steamid") or "")
            ax, ay = _clean_coord(d.get("attacker_X")), _clean_coord(d.get("attacker_Y"))
            vx, vy = _clean_coord(d.get("user_X")), _clean_coord(d.get("user_Y"))
            # 兼容列（deprecated）：保留原行为——优先攻击者坐标（旧消费方），无则受害者
            legacy_x = ax if ax is not None else (vx if vx is not None else 0.0)
            legacy_y = ay if ay is not None else (vy if vy is not None else 0.0)
            kill_events.append(Event(
                match_id=match.id,
                tick=int(d.get("tick") or 0),
                round=int(d.get("round") or 0),
                event_type="kill",
                actor_steam=actor,
                victim_steam=victim or None,
                weapon=str(d.get("weapon") or ""),
                x=legacy_x,
                y=legacy_y,
                actor_x=ax,
                actor_y=ay,
                victim_x=vx,
                victim_y=vy,
            ))
        db.add_all(kill_events)

        # ---------- events（grenade 聚合，决策点 3 方案 B） ----------
        # 仅聚合投掷物实体（*Projectile）；玩家手持武器实体（CKnife/CC4 等）不是投掷事件。
        # 注意：同一 entity_id 在生命周期内可能出现多种类型（CS2 动态类型），
        # 取主导类型（众数）作为该投掷物的类型（Phase 4B 实测修正）。
        from collections import Counter

        grenade_events = []
        by_entity: dict[int, list[dict]] = {}
        for g in grenades:
            raw = g.get("grenade_type", "")
            if not raw.endswith("Projectile"):
                continue
            by_entity.setdefault(g["grenade_entity_id"], []).append(g)
        for eid, rows in by_entity.items():
            main_type = Counter(r.get("grenade_type", "") for r in rows).most_common(1)[0][0]
            first = min(rows, key=lambda r: r["tick"])
            gtype = GRENADE_TYPE_MAP.get(main_type, "other")
            grenade_events.append(Event(
                match_id=match.id,
                tick=int(first.get("tick") or 0),
                # 6R10A：备战/场外（round=None）→ 存 0（非空约束需 int）
                round=int(mapper(int(first.get("tick") or 0)) or 0),
                event_type="grenade",
                actor_steam=str(first.get("steamid") or ""),
                weapon=None,
                x=float(first.get("x") or 0.0),
                y=float(first.get("y") or 0.0),
                grenade_type=gtype,
            ))
        db.add_all(grenade_events)

        # ---------- 热力图（Phase 2：击杀 + 路线，存 MinIO + heatmap_cache） ----------
        # 健壮性（Phase 5B P1）：热力图失败（缺底图/maps.json 缺地图/图片损坏等）不得
        # 让已成功的核心比赛解析整体失败；异常隔离为警告并继续，解析仍置 parsed。
        # 仅隔离热力图相关异常；数据库/核心解析异常仍由外层 except 处理（不吞）。
        heatmap_warnings: list[str] = []

        def _heatmap_safe(mode: str, points: list) -> None:
            try:
                _save_heatmap(db, match, header.map_name, mode, points)
            except (FileNotFoundError, OSError, KeyError, ValueError) as exc:
                msg = f"{mode}: {type(exc).__name__}: {str(exc)[:120]}"
                heatmap_warnings.append(msg)
                import logging

                logging.getLogger("parse_tasks").warning(
                    "heatmap skipped demo_id=%s mode=%s warn=%s", demo.id, mode, msg
                )

        # 击杀热力图：全队凶手位置（计划书 7.2）；6R10A：仅正式回合（round>=1），备战击杀不计。
        kill_points = [
            (float(d["attacker_X"]), float(d["attacker_Y"]))
            for d in deaths
            if d.get("round") is not None and d["round"] >= 1
            and d.get("attacker_X") is not None and d.get("attacker_Y") is not None
        ]
        _heatmap_safe("kills_all", kill_points)
        # 路线热力图：tick 位置降采样（每 8 tick 一行 ≈3 万点），按队伍分 CT/T
        # 6R19：对不可信 playout_ticks 做上界钳制（防构造超长 range 触发 DoS），并保证为正整数。
        try:
            playout_ticks = int(header.playout_ticks or 0)
        except (TypeError, ValueError):
            playout_ticks = 0
        sample_ticks = max(0, min(playout_ticks, MAX_ROUTE_SAMPLE_TICKS))
        tick_range = range(0, sample_ticks, 8) if sample_ticks else range(0)
        pos_df = analyzer._parser.parse_ticks(
            ["X", "Y", "CCSPlayerPawn.m_iTeamNum"], ticks=tick_range
        )
        route_all, route_ct, route_t = [], [], []
        for row in pos_df.to_dict(orient="records"):
            x, y, team = row.get("X"), row.get("Y"), row.get("CCSPlayerPawn.m_iTeamNum")
            if x is None or y is None:
                continue
            pt = (float(x), float(y))
            route_all.append(pt)
            if team == 2:
                route_ct.append(pt)
            elif team == 3:
                route_t.append(pt)
        _heatmap_safe("route_all", route_all)
        _heatmap_safe("route_ct", route_ct)
        _heatmap_safe("route_t", route_t)

        # ---------- 动态路线 artifact（6R4） ----------
        # 异常隔离：路线数据生成失败不阻断解析（demo 仍 parsed），仅 manifest 缺失，
        # 前端 /personal/route 返回稳定 ROUTE_UNAVAILABLE（可读提示）。与热力图隔离策略一致。
        route_info: dict | None = None
        try:
            route_info = _save_route_artifact(
                db, analyzer, match, header.map_name, int(header.playout_ticks or 0),
                rounds, players, identity_of, player_id_of, deaths,
                header.tick_rate,   # 权威时基（CDemoFileInfo）；None=不显示秒
            )
        except Exception as exc:  # noqa: BLE001  路线生成失败不影响主体解析
            import logging

            logging.getLogger("parse_tasks").warning(
                "route artifact skipped demo_id=%s warn=%s", demo.id, f"{type(exc).__name__}: {str(exc)[:120]}"
            )

        # ---------- 战术回放 artifact（6R8 v2.0） ----------
        # 异常隔离：战术数据（双向 yaw + 道具生命周期）生成失败不阻断解析（demo 仍 parsed），
        # 前端 /team/replay 与 /tactical/primitives 返回稳定 TACTICAL_UNAVAILABLE。
        tactical_info: dict | None = None
        try:
            tactical_info = _save_tactical_artifact(
                db, analyzer, match, header.map_name, int(header.playout_ticks or 0),
                rounds, players, identity_of, player_id_of, deaths, grenades,
                header.tick_rate,
            )
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger("parse_tasks").warning(
                "tactical artifact skipped demo_id=%s warn=%s", demo.id, f"{type(exc).__name__}: {str(exc)[:120]}"
            )

        # ---------- 收尾 ----------
        # 6R0.1：比分重建与引擎比分交叉核验（不一致产生 parse warning，不静默声称正确）
        parse_warnings: list[str] = []
        try:
            score_ok, score_warn = analyzer.reconcile_score(rounds)
            if not score_ok and score_warn:
                parse_warnings.append(score_warn)
        except Exception as exc:  # noqa: BLE001  核验失败不阻断解析（记录即可）
            parse_warnings.append(f"比分核验异常: {type(exc).__name__}: {str(exc)[:120]}")

        demo.status = "parsed"
        demo.map_name = header.map_name
        demo.match_id = match.id
        demo.heatmap_warnings = "\n".join(heatmap_warnings) if heatmap_warnings else None
        demo.parse_warnings = "\n".join(parse_warnings) if parse_warnings else None
        db.commit()

        return {
            "demo_id": demo.id,
            "match_id": match.id,
            "map": header.map_name,
            "rounds": len(rounds),
            "score": f"{team_a}:{team_b}",   # 6R0：队伍比分
            "players": len(player_rows),
            "kills": len(kill_events),
            "grenades": len(grenade_events),
            "heatmap_warnings": heatmap_warnings,
            "route_artifact": route_info,
            "tactical_artifact": tactical_info,
        }
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        demo = db.get(Demo, demo_id) if "demo_id" in locals() else None
        if demo is not None:
            demo.status = "failed"
            demo.error_msg = str(exc)[:1000]
            db.commit()
        raise
    finally:
        if "tmp_file" in locals() and tmp_file:
            try:
                Path(tmp_file).unlink(missing_ok=True)
            except OSError:
                pass
        db.close()
