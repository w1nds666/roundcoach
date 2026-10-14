"""FastAPI 入口（计划书 4.3 backend/app/main.py）。"""
from pathlib import Path

from fastapi import FastAPI, Request, Response

from app.api import ai_report, auth, coach, demo, personal, share, steam_auth, team
from app.config import settings
from app.errors import DEMO_NOT_FOUND, NAVMESH_NOT_FOUND, ApiError, register_error_handlers

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="CS2 Demo 分析平台后端 API",
    # 6R19B 生产硬化：ENV=production 时禁用 /docs、/redoc、/openapi.json（本地 dev 仍开启）
    docs_url=None if settings.env == "production" else "/docs",
    redoc_url=None if settings.env == "production" else "/redoc",
    openapi_url=None if settings.env == "production" else "/openapi.json",
)


# 6R19：全局安全响应头（防点击劫持 / MIME 嗅探 / Referrer 泄露）。
# 仅追加不破坏应用的帧约束头；`frame-ancestors 'none'` + X-Frame-Options DENY 均只影响被 iframe 嵌入，
# 不影响本站运行。逐个响应追加（`always` 语义），与业务特定头（如 share 的 no-store）可共存。
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy", "frame-ancestors 'none'"
    )
    return response


register_error_handlers(app)
app.include_router(auth.router)
app.include_router(steam_auth.router)
app.include_router(steam_auth.recent_router)
app.include_router(demo.router)
app.include_router(ai_report.router)
app.include_router(ai_report.consent_router)
app.include_router(personal.router)
app.include_router(coach.router)
app.include_router(share.router)
app.include_router(team.router)


@app.get("/health", tags=["system"])
def health():
    """健康检查：供 Docker Compose healthcheck 与 Nginx 探活使用。"""
    return {"status": "ok", "app": settings.app_name}


@app.get("/api/maps/{map_name}/radar", tags=["maps"])
def radar_image(map_name: str, layer: str = "upper"):
    """雷达底图 PNG（公开静态资源，供前端 Canvas 叠加渲染）。

    6R19：map_name 必须以地图配置合法键白名单通过，且拒绝任何路径分隔符/`..`，
    防止路径穿越读取容器内任意 `.png` 文件（防御纵深——已在部署边界归一化，但代码不应留此缺陷）。
    """
    from app.services.heatmap import load_maps_config

    maps = load_maps_config()
    if map_name not in maps or any(sep in map_name for sep in ("/", "\\", "..")) or layer not in ("upper", "lower"):
        raise ApiError(404, DEMO_NOT_FOUND, f"地图 {map_name} 雷达图不存在")
    if layer == "lower" and "lower_altitude_below" not in maps[map_name]:
        raise ApiError(404, DEMO_NOT_FOUND, f"地图 {map_name} 没有下层雷达图")
    radar_dir = (Path(__file__).resolve().parent.parent / "config" / "radars").resolve()
    suffix = "_lower" if layer == "lower" else ""
    radar = (radar_dir / f"{map_name}{suffix}.png").resolve()
    if not str(radar).startswith(str(radar_dir) + "\\") and not str(radar).startswith(str(radar_dir) + "/"):
        raise ApiError(404, DEMO_NOT_FOUND, f"地图 {map_name} 雷达图不存在")
    if not radar.exists():
        raise ApiError(404, DEMO_NOT_FOUND, f"地图 {map_name} 雷达图不存在")
    return Response(content=radar.read_bytes(), media_type="image/png")


@app.get("/api/maps/{map_name}", tags=["maps"])
def map_metadata(map_name: str):
    """地图坐标换算元数据（pos_x/pos_y/scale，来自 maps.json；供前端校验/诊断）。"""
    from app.services.heatmap import load_maps_config

    maps = load_maps_config()
    if map_name not in maps:
        raise ApiError(404, DEMO_NOT_FOUND, f"地图 {map_name} 配置不存在")
    meta = maps[map_name]
    return {
        "name": map_name,
        "pos_x": meta["pos_x"],
        "pos_y": meta["pos_y"],
        "scale": meta["scale"],
        "image_size": 1024,
        "formula": "radar_x=(world_x-pos_x)/scale/1024; radar_y=(pos_y-world_y)/scale/1024",
        "lower_altitude_below": meta.get("lower_altitude_below"),
    }


@app.get("/api/maps/{map_name}/regions", tags=["maps"])
def map_regions(map_name: str):
    """区域几何（版本化 artifact）：真实 last_place_name 样本点确定性凸包。

    - 供前端"区域控制"图层：透明轮廓 + 中文叫位 + 覆盖/置信 + content_hash。
    - 样本不足 → 仅中心/虚线 + low confidence；无来源 → 不绘制（诚实）。
    - 不暴露裸 tick/世界坐标/SteamID；坐标已为雷达归一化 [0,1]。
    """
    from app.services.region_geometry import load_region_geometry

    geo = load_region_geometry(map_name)
    if geo is None:
        raise ApiError(404, DEMO_NOT_FOUND, f"地图 {map_name} 尚无区域几何（未由真实样本生成）")
    return geo


@app.get("/api/maps/{map_name}/navmesh", tags=["maps"])
def navmesh_artifact(map_name: str):
    """受控产品路由：Valve 官方提取、版本化、可审计的 CS2 NavMesh（静态几何）。

    6R21F-E6B-NAVMESH-PRODUCT-GATE：取代 Vite dev-only `/__dev__/vrf-navmesh` 路由，
    让真实 nginx:80 + headed 浏览器稳定取得 Mirage NavMesh（不复用/不复制仓库外环境变量路径）。

    安全边界（fail-closed）：
    - map_name 白名单（de_mirage、de_inferno）+ 拒绝任何路径分隔符（/、\\\\）与 `..` → 无任意文件/目录遍历。
    - artifact 只从后端仓库内 `config/navmesh/` 读取；绝不读任意本机路径/VPK/用户输入路径；无 Steam/demo 玩家数据。
    - 未白名单/越界/缺失/非法 JSON/schema 不符 → 404，绝不回显任意文件。
    - 公开静态几何（无需登录）；响应只含几何与 provenance，不含任何演示玩家/Steam 标识。

    响应：application/json + Content-Length + 强 ETag/sha256 + 缓存头 + 版本头（可审计）。
    """
    from app.services.navmesh import is_allowed_map, load_navmesh_artifact

    # 白名单 + 分隔符/穿越片段拒绝（防御纵深，与 radar 路由一致）。
    if not is_allowed_map(map_name):
        raise ApiError(404, NAVMESH_NOT_FOUND, f"地图 {map_name} 的 NavMesh 不存在或未接入")
    art = load_navmesh_artifact(map_name)
    if art is None:
        raise ApiError(404, NAVMESH_NOT_FOUND, f"地图 {map_name} 尚无版本化 NavMesh artifact")
    return Response(
        content=art["bytes"],
        media_type="application/json",
        headers={
            "ETag": f'"{art["sha256"]}"',
            "X-VRF-NavMesh-SHA256": art["sha256"],
            "X-VRF-NavMesh-Version": art["version"],
            "X-VRF-NavMesh-Schema": art["schema"],
            "X-VRF-NavMesh-NavVersion": str(art["nav_version"]) if art["nav_version"] is not None else "",
            # 版本化静态几何：可缓存 1 天 + ETag 再验证（避免 URL 固定时的部署后陈旧）。
            "Cache-Control": "public, max-age=86400",
        },
    )
