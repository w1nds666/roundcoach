"""Phase 6A 热力图成品化回归：参数/掩码渐变/密度平滑（消除方块拼贴感）。"""
import numpy as np
import pytest

from app.services import heatmap as hm


def test_polish_parameters():
    """成品化参数：512 网格、更大 σ、柔和 alpha 曲线。"""
    assert hm.GRID == 512
    assert hm.SIGMA == 5.0
    assert hm.ALPHA_POWER == 0.7
    assert hm.ALPHA_MAX == 240


def test_mask_gradient_not_hard_threshold():
    """掩码为羽化灰度（半透明 mask 产生中间 alpha），而非硬阈值二值裁剪。"""
    heat = np.full((8, 8), 0.5)
    # 半透明 mask（128/255）
    mask = np.full((8, 8), 128, dtype=np.uint8)
    rgba = hm._apply_palette(heat, "heat", mask)
    alphas = np.unique(rgba[..., 3])
    # 唯一中间 alpha（半透明 ≈ 240*(0.5^0.7)*(128/255) ≈ 74），非 0/满值两极
    assert len(alphas) == 1
    assert 40 <= alphas[0] <= 140, f"渐变掩码 alpha 应为半透明中间值，实际 {alphas[0]}"


def test_density_smooth_no_blockiness():
    """邻近点密度场平滑：相邻像素差小（无方块硬边）。"""
    meta = {"pos_x": -3230.0, "pos_y": 1713.0, "scale": 5.0}
    points = [(x, y) for x in range(-3000, -2500, 40) for y in range(1400, 1900, 40)]
    heat = hm._density_map(points, "de_mirage", meta)
    # 取中部区域检查相邻差
    mid = heat[150:170, 150:170]
    diff = np.abs(np.diff(mid, axis=0))
    assert diff.max() < 0.35, f"密度场存在硬边（最大相邻差 {diff.max():.3f}）"


def test_density_json_buckets_aligned():
    """密度 JSON 网格与 GRID 一致（前端渲染对齐）。"""
    meta = {"pos_x": -3230.0, "pos_y": 1713.0, "scale": 5.0}
    grid = hm.generate_heatmap_grid([(100.0, 100.0)], "de_mirage")
    assert grid["buckets"] == hm.GRID
