"""热力图生成（Phase 2.1 转播级视觉升级版，计划书 7.2 算法 + 视觉专项）。

相对 v1 的升级（docs/backlog.md「Phase 2.1」）：
- 连续密度场：256 网格 + 高斯平滑 + 双线性上采样到 1024px（消灭方块拼贴与硬边）
- 透明 alpha 叠加：低密度透明、高密度实（幂次 alpha 曲线），不污染地图
- CT/T 语义色：CT 蓝 / T 橙 / 击杀通用热色（多级连续渐变）
- 雷达轮廓裁剪：以底图 alpha 为 mask（羽化边缘），热力不溢出地图外
- 高分辨率渲染（1024×1024，Retina 友好）
- 内置强度图例（右下角渐变条 + 低/高标注）
- PNG（透明可叠加）+ 密度网格 JSON 双输出（前端 Canvas 交互渲染）

已知限制（如实记录）：未实现严格 walkable mask（可行走区域）；
当前 mask 采用雷达图 alpha 轮廓近似，热力可能出现在不可行走的贴图区域。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = BACKEND_DIR / "config"
MAPS_JSON = CONFIG_DIR / "maps.json"
RADAR_DIR = CONFIG_DIR / "radars"

GRID = 512           # 密度网格分辨率（Phase 6 成品化：256→512，消除方块感）
HEAT_SIZE = 1024     # 输出渲染分辨率（高分辨率/Retina）
SIGMA = 5.0          # 高斯平滑 σ（网格单位；512 网格下等效平滑宽度更大）
ALPHA_POWER = 0.7    # alpha 幂次曲线：低密度更透明、边缘更柔和（成品化）
ALPHA_MAX = 240

# 色板：多级连续渐变（value 0→1）
PALETTES = {
    # 击杀/通用：深蓝紫 → 青 → 黄 → 白热（广播级热力）
    "heat": [
        (0.00, (20, 0, 90)),
        (0.25, (66, 0, 255)),
        (0.50, (0, 200, 255)),
        (0.75, (255, 220, 0)),
        (1.00, (255, 255, 255)),
    ],
    # CT：深蓝 → 亮青蓝
    "ct": [
        (0.00, (12, 30, 90)),
        (0.40, (20, 90, 200)),
        (0.75, (60, 170, 255)),
        (1.00, (190, 240, 255)),
    ],
    # T：深橙 → 亮橙黄
    "t": [
        (0.00, (110, 30, 0)),
        (0.40, (220, 90, 0)),
        (0.75, (255, 160, 40)),
        (1.00, (255, 235, 170)),
    ],
}

PALETTE_FOR_MODE = {
    "kills_all": "heat",
    "route_all": "heat",
    "route_ct": "ct",
    "route_t": "t",
}


def load_maps_config() -> dict:
    return json.loads(MAPS_JSON.read_text(encoding="utf-8"))["maps"]


def world_to_radar(x: float, y: float, meta: dict) -> tuple[float, float]:
    """世界坐标 → 雷达图归一化坐标 [0,1)。

    radar_x = (world_x - pos_x) / scale / image_width
    radar_y = (pos_y - world_y) / scale / image_width
    """
    rx = (x - meta["pos_x"]) / meta["scale"] / HEAT_SIZE
    ry = (meta["pos_y"] - y) / meta["scale"] / HEAT_SIZE
    return rx, ry


def _density_map(points: list[tuple[float, float]], map_name: str, meta: dict) -> np.ndarray:
    """坐标点集 → 高斯平滑连续密度场（[0,1] 归一化，GRID×GRID）。"""
    heat = np.zeros((GRID, GRID), dtype=np.float64)
    for x, y in points:
        if x == 0 and y == 0:
            continue  # 剔除无效点
        rx, ry = world_to_radar(x, y, meta)
        if 0.0 <= rx < 1.0 and 0.0 <= ry < 1.0:
            bx = min(int(rx * GRID), GRID - 1)
            by = min(int(ry * GRID), GRID - 1)
            heat[by, bx] += 1.0
    if heat.max() > 0:
        heat = gaussian_filter(heat, sigma=SIGMA)
        heat = heat / heat.max()
    return heat


def _apply_palette(heat: np.ndarray, palette_name: str, mask: np.ndarray | None = None) -> np.ndarray:
    """密度场 → RGBA（向量化多级渐变 + 幂次 alpha + 可选轮廓 mask）。"""
    stops = PALETTES[palette_name]
    v = np.clip(heat, 0.0, 1.0).ravel()

    r = np.interp(v, [s[0] for s in stops], [s[1][0] for s in stops])
    g = np.interp(v, [s[0] for s in stops], [s[1][1] for s in stops])
    b = np.interp(v, [s[0] for s in stops], [s[1][2] for s in stops])
    a = np.clip(v ** ALPHA_POWER * ALPHA_MAX, 0, ALPHA_MAX)

    h, w = heat.shape
    rgba = np.stack([r, g, b, a], axis=-1).reshape(h, w, 4).astype(np.uint8)

    if mask is not None:
        # 成品化：mask 为羽化灰度（0-255），渐变乘入 alpha（而非硬阈值裁剪）
        rgba[..., 3] = (rgba[..., 3].astype(np.float64) * (mask / 255.0)).astype(np.uint8)
    return rgba


def _radar_mask(radar: Image.Image, size: int) -> np.ndarray:
    """雷达图 alpha 轮廓 mask（羽化边缘），用于裁剪热力层。"""
    a = np.array(radar.convert("RGBA"))[..., 3].astype(np.float64) / 255.0
    a = gaussian_filter(a, sigma=2.0)          # 边缘羽化
    mask = np.clip(a * 255, 0, 255).astype(np.uint8)
    return mask


def _legend_strip(size: int = 140) -> Image.Image:
    """强度图例：横向渐变条。"""
    strip = np.zeros((10, size, 4), dtype=np.uint8)
    stops = PALETTES["heat"]
    xs = np.linspace(0, 1, size)
    r = np.interp(xs, [s[0] for s in stops], [s[1][0] for s in stops])
    g = np.interp(xs, [s[0] for s in stops], [s[1][1] for s in stops])
    b = np.interp(xs, [s[0] for s in stops], [s[1][2] for s in stops])
    strip[:, :, 0] = r.astype(np.uint8)
    strip[:, :, 1] = g.astype(np.uint8)
    strip[:, :, 2] = b.astype(np.uint8)
    strip[:, :, 3] = 220
    return Image.fromarray(strip, "RGBA")


def _compose_heatmap(
    points: list[tuple[float, float]],
    map_name: str,
    palette_name: str = "heat",
    radar_png: str | Path | None = None,
    with_legend: bool = True,
) -> Image.Image:
    """密度场 + 语义色阶 + 轮廓 mask + 高清底图合成（含图例）。"""
    maps = load_maps_config()
    if map_name not in maps:
        raise KeyError(f"maps.json 缺少地图 {map_name}")
    meta = maps[map_name]

    heat = _density_map(points, map_name, meta)
    radar = radar_png or (RADAR_DIR / f"{map_name}.png")
    base = Image.open(radar).convert("RGBA").resize((HEAT_SIZE, HEAT_SIZE), Image.LANCZOS)

    mask = _radar_mask(base, HEAT_SIZE)
    # 密度场上采样到输出分辨率（双线性，消灭方块感）
    heat_hi = np.array(Image.fromarray((heat * 255).astype(np.uint8)).resize(
        (HEAT_SIZE, HEAT_SIZE), Image.BILINEAR)) / 255.0
    mask_hi = np.array(Image.fromarray(mask).resize((HEAT_SIZE, HEAT_SIZE), Image.BILINEAR))
    overlay = _apply_palette(heat_hi, palette_name, mask_hi)

    heat_img = Image.fromarray(overlay, "RGBA")
    result = Image.alpha_composite(base, heat_img)

    if with_legend:
        legend = _legend_strip(150)
        result.alpha_composite(legend, (HEAT_SIZE - 190, HEAT_SIZE - 44))
        # 低/高标注（简单像素文本用两次合成避免依赖字体：用色块+纯文本 PIL 默认字体）
        from PIL import ImageDraw

        draw = ImageDraw.Draw(result)
        draw.text((HEAT_SIZE - 185, HEAT_SIZE - 40), "低", fill=(200, 210, 220, 220))
        draw.text((HEAT_SIZE - 44, HEAT_SIZE - 40), "高", fill=(255, 255, 255, 240))
    return result


def generate_heatmap_png(
    points: list[tuple[float, float]],
    map_name: str,
    out_path: str | Path,
    radar_png: str | Path | None = None,
    palette_name: str = "heat",
) -> Path:
    """生成热力图 PNG（叠加雷达图底图 + 图例）。"""
    result = _compose_heatmap(points, map_name, palette_name, radar_png)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.save(out)
    return out


def generate_heatmap_bytes(
    points: list[tuple[float, float]],
    map_name: str,
    radar_png: str | Path | None = None,
    palette_name: str = "heat",
) -> bytes:
    """生成热力图 PNG 字节（供 MinIO 上传）。"""
    import io

    buf = io.BytesIO()
    _compose_heatmap(points, map_name, palette_name, radar_png).save(buf, format="PNG")
    return buf.getvalue()


def generate_heatmap_grid(points: list[tuple[float, float]], map_name: str) -> dict:
    """ECharts/Canvas 密度网格数据（[[x, y, value], ...]，像素坐标系）。"""
    maps = load_maps_config()
    meta = maps[map_name]
    heat = _density_map(points, map_name, meta)
    data = []
    for by in range(GRID):
        for bx in range(GRID):
            v = round(float(heat[by, bx]), 4)
            if v > 0:
                data.append([bx, GRID - 1 - by, v])
    return {"map": map_name, "buckets": GRID, "size": HEAT_SIZE, "data": data}
