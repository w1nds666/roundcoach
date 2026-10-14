"""6R21F-E6B-NAVMESH-PRODUCT-GATE：受控后端产品路由的版本化 NavMesh artifact 读取。

消除 MapControlReplay/Report 对 Vite dev-only `/__dev__/vrf-navmesh` 路由与 repo 外环境变量
（VRF_NAVMESH_JSON）的产品依赖：由真实 nginx:80 产品链路经后端稳定取得 Valve 官方提取、
版本化、可审计的 Mirage NavMesh。

安全边界（fail-closed）：
- 白名单 catalog：仅提供 manifest 登记的十张标准对战地图，其它地图一律 404。
- map_name 拒绝任何路径分隔符（/、\\\\）与 `..` 片段 → 无任意文件/目录遍历。
- artifact 路径解析后必须落在 NAVMESH_DIR 内（防御纵深，与 radar 路由一致）。
- 读取后校验 schema==cs2-navmesh-v1 且 source.map==请求 map（审计一致性），不匹配即 None。
- 校验失败（未白名单/越界/读取失败/非法 JSON/schema 不符）一律返回 None → 上层 404，绝不回显任意文件。

版本化/可审计记录：artifact 内嵌 source（file/sha256/navVersion）、tool（ValveResourceFormat
19.2.6339 / MIT）、stats（deterministicHash），本服务透出供响应头与审计。

6R21F-E6C-MULTIMAP-PREFLIGHT：新增 `de_inferno`（demo22 对应地图，与 de_inferno 相同按去重规则只保留单份），
并提供 `config/navmesh/manifest.json`（cs2-navmesh-manifest-v1）作为三地图官方真值 inventory 的权威核对凭据。
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
NAVMESH_DIR = BACKEND_DIR / "config" / "navmesh"

# 白名单：官方提取、版本化、可审计的十张标准对战地图；未知/社区地图仍 404。
NAVMESH_CATALOG = {
    "de_mirage": "de_mirage.json",
    "de_dust2": "de_dust2.json",
    "de_inferno": "de_inferno.json",
    "de_ancient": "de_ancient.json",
    "de_anubis": "de_anubis.json",
    "de_cache": "de_cache.json",
    "de_nuke": "de_nuke.json",
    "de_overpass": "de_overpass.json",
    "de_train": "de_train.json",
    "de_vertigo": "de_vertigo.json",
}

# 6R21F-E6C-MULTIMAP-PREFLIGHT：三地图官方真值 inventory（cs2-navmesh-manifest-v1）。
# 提供每张图的 artifact/nav/vpk 哈希、统计与 demo→map 对齐，供审计与测试核对，绝不含原 VPK 本机绝对路径/Steam 身份。
MANIFEST_FILE = "manifest.json"
_MANIFEST_SCHEMA = "cs2-navmesh-manifest-v1"

# 路径分隔符 / 穿越片段（token 级防御，与 radar 路由一致）。
_FORBIDDEN = ("/", "\\", "..")

_SCHEMA = "cs2-navmesh-v1"


def is_allowed_map(map_name: str) -> bool:
    """是否白名单内合法 map，且不含路径分隔符/穿越片段。"""
    return map_name in NAVMESH_CATALOG and not any(sep in map_name for sep in _FORBIDDEN)


def navmesh_path(map_name: str) -> Path:
    """解析白名单 map 对应的 artifact 绝对路径，并确保落在 NAVMESH_DIR 内。"""
    if not is_allowed_map(map_name):
        raise ValueError(map_name)
    root = NAVMESH_DIR.resolve()
    p = (root / NAVMESH_CATALOG[map_name]).resolve()
    prefix = str(root) + "\\"
    if not str(p).startswith(prefix) and not str(p).startswith(str(root) + "/"):
        raise ValueError(map_name)
    return p


@lru_cache(maxsize=None)
def _cached_bytes(map_name: str) -> bytes | None:
    try:
        p = navmesh_path(map_name)
    except ValueError:
        return None
    if not p.exists() or not p.is_file():
        return None
    try:
        return p.read_bytes()
    except OSError:
        return None


def load_navmesh_artifact(map_name: str) -> dict | None:
    """读取并校验版本化 artifact；返回字节 + 审计元数据，校验失败返回 None（fail-closed）。"""
    if not is_allowed_map(map_name):
        return None
    raw = _cached_bytes(map_name)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    # 审计一致性：必须是 cs2-navmesh-v1 且 source.map 与请求 map 一致。
    if parsed.get("schema") != _SCHEMA:
        return None
    if parsed.get("source", {}).get("map") != map_name:
        return None
    return {
        "bytes": raw,
        "size": len(raw),
        # 响应用强 ETag：内容 sha256（与 X-VRF-NavMesh-SHA256 一致）。
        "sha256": hashlib.sha256(raw).hexdigest(),
        "schema": _SCHEMA,
        "map": map_name,
        # 版本头：取 deterministicHash 前 16 位（稳定、可审计），非空才用。
        "version": (parsed.get("stats", {}).get("deterministicHash") or "")[:16] or "unknown",
        "nav_version": parsed.get("source", {}).get("navVersion"),
        "source_hash": parsed.get("source", {}).get("sha256"),
        "tool": parsed.get("tool"),
    }


@lru_cache(maxsize=1)
def _cached_manifest() -> tuple[bytes, dict] | None:
    """读取并缓存 manifest（cs2-navmesh-manifest-v1）；异常/非法返回 None（fail-closed）。"""
    p = (NAVMESH_DIR / MANIFEST_FILE).resolve()
    prefix = str(NAVMESH_DIR.resolve()) + "\\"
    if not (str(p).startswith(prefix) or str(p).startswith(str(NAVMESH_DIR.resolve()) + "/")):
        return None
    if not p.exists() or not p.is_file():
        return None
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if parsed.get("schema") != _MANIFEST_SCHEMA:
        return None
    return raw, parsed


def load_manifest() -> dict | None:
    """返回 manifest 元数据（含 bytes/sha256），校验失败返回 None；供审计与测试核对。"""
    cached = _cached_manifest()
    if cached is None:
        return None
    raw, parsed = cached
    return {
        "bytes": raw,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "schema": _MANIFEST_SCHEMA,
        "manifest": parsed,
        "maps": parsed.get("maps", {}),
    }
