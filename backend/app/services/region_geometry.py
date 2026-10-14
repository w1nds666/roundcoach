"""6R16：区域几何版本化 artifact —— 从真实 last_place_name 样本点生成确定性区域多边形。

诚实来源原则（合同 C2）：
- 区域多边形**只能**由真实样本点（last_place_name + 世界坐标 x/y，来自战术 artifact 玩家采样）经
  统一雷达变换生成；样本不足 → 只给中心点 + 虚线 + 低置信，**绝不凭空画多边形**。
- 凡是人工地图配置（region_meta）也必须版本化、可审计（source/version/checksum）。
- 全部确定性：同输入 → 同 hull/coverage/centroid/confidence → 同 content_hash。
- 输出坐标为雷达归一化 [0,1]（rx/ry），与 maps.json 换算一致（与 heatmap/personal 同源）。
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
REGION_DIR = BACKEND_DIR / "config" / "regions"


def load_region_geometry(map_name: str) -> dict | None:
    """加载版本化区域几何 artifact（backend/config/regions/{map}.json）；缺失返回 None。

    前端区域图层与区域控制状态机共用；无来源则不绘制（诚实）。
    返回已归一化的 {map, regions:[...], coverage, content_hash, geometry_trust, meta}。
    """
    p = REGION_DIR / f"{map_name}.json"
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    regions = raw.get("regions")
    if isinstance(regions, dict):
        # artifact 内层再包一层 {regions:[...], ...}（build_regions 输出被整体存入）
        return {
            "map": raw.get("map") or map_name,
            "regions": regions.get("regions", []),
            "coverage": regions.get("coverage"),
            "region_count": regions.get("region_count"),
            "geometric_region_count": regions.get("geometric_region_count"),
            "content_hash": regions.get("content_hash"),
            "geometry_trust": regions.get("geometry_trust"),
            "meta": regions.get("meta"),
        }
    return {"map": raw.get("map") or map_name, "regions": regions or [],
            "coverage": raw.get("coverage"), "content_hash": raw.get("content_hash"),
            "geometry_trust": raw.get("geometry_trust"), "meta": raw.get("meta")}

REGION_SCHEMA_VERSION = "1.0"

# 一个区域成为「可信多边形」的最小样本点数（不足 → 仅中心/虚线 + low confidence）
REGION_MIN_SAMPLES = 24
# 参与 hull 的最大采样点（抽样，避免大 demo 膨胀；抽样用确定性间隔）
REGION_MAX_SAMPLES = 1600
# 判定「样本足够画出轮廓」的点数门槛
REGION_CONFIDENT_SAMPLES = 60
# 虚线/中心低置信的样本上限
REGION_UNCERTAIN_SAMPLES = 12


def world_to_radar(x: float, y: float, pos_x: float, pos_y: float, scale: float, image_size: float) -> tuple[float, float]:
    """世界坐标 → 雷达归一化 [0,1)（与 heatmap.world_to_radar 同公式）。"""
    rx = (x - pos_x) / scale / image_size
    ry = (pos_y - y) / scale / image_size
    return round(rx, 5), round(ry, 5)


def _clean(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """单调链凸包（确定性）。返回逆时针多边形顶点；<3 点返回原有序（无面积）。"""
    pts = sorted(set((round(x, 5), round(y, 5)) for x, y in points))
    if len(pts) <= 1:
        return pts
    # 极端退化（全共线）也返回两端
    if len(pts) == 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _sample(points: list[tuple[float, float]], cap: int) -> list[tuple[float, float]]:
    if len(points) <= cap:
        return points
    step = math.ceil(len(points) / cap)
    return points[::step][:cap]


def _polygon_area(poly: list[tuple[float, float]]) -> float:
    if len(poly) < 3:
        return 0.0
    s = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _point_in_poly(pt: tuple[float, float], poly: list[tuple[float, float]]) -> bool:
    """射线法；空/退化多边形返回 False（保守，不误判覆盖）。"""
    if len(poly) < 3:
        return False
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xin:
                inside = not inside
    return inside


def build_region(
    place_key: str,
    place_cn: str,
    world_pts: list[tuple[float, float]],
    pos_x: float,
    pos_y: float,
    scale: float,
    image_size: float,
    *,
    manual: dict | None = None,
) -> dict:
    """从真实样本点构建单个区域几何（确定性）。

    world_pts：该区域的真实采样点 [(x, y), ...]（世界坐标）。
    manual：人工地图配置（可选，可审计）。若提供，优先使用其 polygons/centroid，但必须标注
            source="manual" 且版本化；否则 source="samples"（从真实样本生成）。
    """
    rp = [world_to_radar(x, y, pos_x, pos_y, scale, image_size) for x, y in world_pts]
    rp = [p for p in rp if 0.0 <= p[0] <= 1.0 and 0.0 <= p[1] <= 1.0]

    if manual:
        poly = [(round(p["rx"], 5), round(p["ry"], 5)) for p in manual.get("polygon", [])]
        centroid = tuple(round(v, 5) for v in manual.get("centroid", _centroid(poly)))
        return {
            "place_key": place_key, "place_cn": place_cn,
            "source": "manual", "manual_version": manual.get("version", "1"),
            "polygon": [{"rx": x, "ry": y} for x, y in poly],
            "centroid": {"rx": centroid[0], "ry": centroid[1]},
            "coverage": round(_coverage(rp, poly), 3),
            "area": round(_polygon_area(poly), 5),
            "sample_count": len(world_pts),
            "confidence": "medium" if len(rp) >= REGION_CONFIDENT_SAMPLES else "low",
            "geometric": bool(len(poly) >= 3),
        }

    if len(rp) < REGION_UNCERTAIN_SAMPLES:
        # 样本过少：只给中心（虚线），无多边形
        c = _centroid(rp) if rp else (0.5, 0.5)
        return {
            "place_key": place_key, "place_cn": place_cn, "source": "samples",
            "polygon": [], "centroid": {"rx": round(c[0], 5), "ry": round(c[1], 5)},
            "coverage": 0.0, "area": 0.0, "sample_count": len(world_pts),
            "confidence": "low", "geometric": False, "reason": "insufficient_samples",
        }

    pts = _sample(rp, REGION_MAX_SAMPLES)
    poly = convex_hull(pts)
    c = _centroid(poly) if poly else _centroid(rp)
    geometric = len(poly) >= 3
    conf = "high" if (len(rp) >= REGION_CONFIDENT_SAMPLES and geometric) else (
        "medium" if geometric else "low")
    return {
        "place_key": place_key, "place_cn": place_cn, "source": "samples",
        "polygon": [{"rx": x, "ry": y} for x, y in poly],
        "centroid": {"rx": round(c[0], 5), "ry": round(c[1], 5)},
        "coverage": round(_coverage(rp, poly), 3),
        "area": round(_polygon_area(poly), 5),
        "sample_count": len(world_pts),
        "confidence": conf, "geometric": geometric,
    }


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    if not points:
        return (0.5, 0.5)
    if len(points) == 1:
        return points[0]
    if len(points) == 2:
        return ((points[0][0] + points[1][0]) / 2, (points[0][1] + points[1][1]) / 2)
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def _coverage(points: list[tuple[float, float]], poly: list[tuple[float, float]]) -> float:
    """落在 hull 内的样本占比（回归真实性指标；空多边形=0）。"""
    if not points or len(poly) < 3:
        return 0.0
    inside = sum(1 for p in points if _point_in_poly(p, poly))
    return inside / len(points)


def build_regions(
    samples_by_place: dict[str, dict],
    map_name: str,
    map_meta: dict,
    manual_regions: dict[str, dict] | None = None,
) -> dict:
    """按 place 分组样本 → 版本化区域几何清单。

    samples_by_place：{place_key: {"place_cn": str, "points": [(x,y), ...]}}。
    manual_regions：{place_key: manual_region}（可选，可审计人工配置）。
    返回 {schema_version, map, version_hash, regions, coverage, meta}。（纯函数，不含配置读取）
    """
    pos_x = map_meta["pos_x"]; pos_y = map_meta["pos_y"]
    scale = map_meta["scale"]; image_size = map_meta.get("image_size", 1024)
    regions = []
    for pk, info in samples_by_place.items():
        manual = (manual_regions or {}).get(pk)
        regions.append(build_region(
            pk, info.get("place_cn", pk), info.get("points", []),
            pos_x, pos_y, scale, image_size, manual=manual))
    # 稳定排序（place_key）保证确定性
    regions.sort(key=lambda r: r["place_key"])
    geo_count = sum(1 for r in regions if r["geometric"])
    total_samples = sum(r["sample_count"] for r in regions)
    return {
        "schema_version": REGION_SCHEMA_VERSION,
        "map": map_name,
        "regions": regions,
        "coverage": round(geo_count / len(regions), 3) if regions else 0.0,
        "region_count": len(regions),
        "geometric_region_count": geo_count,
        "total_samples": total_samples,
        "meta": {
            "source": "last_place_name samples (tactical artifact) via radar transform",
            "pos_x": pos_x, "pos_y": pos_y, "scale": scale, "image_size": image_size,
            "manual_regions": sorted((manual_regions or {}).keys()),
            "note": "多边形由真实 last_place_name 样本点确定性凸包生成；样本不足仅中心/虚线；不用热力图表达路线",
        },
        "content_hash": _regions_hash(map_name, regions, geo_count),
    }


def _regions_hash(map_name: str, regions: list[dict], geo_count: int) -> str:
    h = hashlib.sha256()
    h.update((map_name or "").encode("utf-8"))
    for r in regions:
        h.update(json.dumps({
            "k": r["place_key"], "src": r["source"],
            "poly": [(p["rx"], p["ry"]) for p in r.get("polygon", [])],
            "c": [r["centroid"]["rx"], r["centroid"]["ry"]],
            "cov": r.get("coverage"), "n": r.get("sample_count"),
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


def evaluate_region_geometry(regions: list[dict]) -> dict:
    """评估区域几何可信度（供 area 状态机把 '置信' 纳入 controlled 门槛）。"""
    geometric = [r for r in regions if r.get("geometric")]
    if not geometric:
        return {"available": False, "geometric_count": 0, "region_count": len(regions),
                "min_coverage": 0.0, "sample_total": 0, "trust": "hidden"}
    min_cov = min(r.get("coverage", 0.0) for r in geometric)
    sample_total = sum(r.get("sample_count", 0) for r in geometric)
    trust = "validated" if (min_cov >= 0.85 and sample_total >= REGION_CONFIDENT_SAMPLES * len(geometric)) else (
        "experimental")
    return {"available": True, "geometric_count": len(geometric), "region_count": len(regions),
            "min_coverage": round(min_cov, 3), "sample_total": sample_total, "trust": trust}
