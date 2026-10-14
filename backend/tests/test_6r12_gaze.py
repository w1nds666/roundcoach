"""6R12 测试：朝向/枪线真值门禁（gaze）。

- 正确变换/语义拆分：世界前向=(cos yaw,sin yaw)，雷达屏幕方向=(cos yaw,-sin yaw)；
  旧式 (-sin,-cos)（=世界 (-sin yaw,cos yaw)）为 90° 错误，被门禁拒绝。
- classify_death：枪械/direct、wallbang、thrusmoke、grenade、knife、suicide、missing 分类正确。
- compute_gate：median<=10 且 p95<=25 且无 90/180° 系统偏差 → passed；否则失败。
- golden set 回归：load 真实 5-demo 角误差（backend/tests/fixtures/phase6r12-golden.json），
  门禁通过且有足够样本（>=50）；90° 反例必失败（证明门禁确实识别系统偏差）。
"""
import json
import math
from pathlib import Path

import pytest

from app.services.gaze import (
    GATE_MEDIAN_MAX_DEG, GATE_P95_MAX_DEG, classify_death, compute_gate,
    deg_error, is_firearm, world_angle, yaw_forward, yaw_screen,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "phase6r12-golden.json"


# ---------- 变换/语义 ----------

def test_yaw_forward_math():
    """世界前向向量 = (cos yaw, sin yaw)：yaw=0 → +X（东），yaw=90 → +Y（北）。"""
    assert abs(yaw_forward(0)[0] - 1.0) < 1e-9 and abs(yaw_forward(0)[1]) < 1e-9
    assert abs(yaw_forward(90)[0]) < 1e-9 and abs(yaw_forward(90)[1] - 1.0) < 1e-9
    assert abs(yaw_forward(180)[0] + 1.0) < 1e-9 and abs(yaw_forward(180)[1]) < 1e-9


def test_yaw_screen_math():
    """雷达/屏幕方向（y 向下）= (cos yaw, -sin yaw)：yaw=0 → 屏幕右（+rx），yaw=90 → 屏幕上（-ry）。"""
    assert abs(yaw_screen(0)[0] - 1.0) < 1e-9 and abs(yaw_screen(0)[1]) < 1e-9
    assert abs(yaw_screen(90)[0]) < 1e-9 and abs(yaw_screen(90)[1] + 1.0) < 1e-9
    assert abs(yaw_screen(180)[0] + 1.0) < 1e-9 and abs(yaw_screen(180)[1]) < 1e-9


def test_old_wrong_transform_is_90_deg_off():
    """旧式 (-sin,-cos)（=世界 (-sin yaw,cos yaw)）相对正确 (cos,sin) 恒偏 90°。"""
    for yaw in (0, 30, 90, 150, 250):
        r = math.radians(yaw)
        new_world = (math.cos(r), math.sin(r))
        old_world = (-math.sin(r), math.cos(r))
        new_ang = math.degrees(math.atan2(new_world[1], new_world[0]))
        old_ang = math.degrees(math.atan2(old_world[1], old_world[0]))
        assert abs(deg_error(old_ang, new_ang) - 90.0) < 1e-6


def test_world_angle_and_deg_error():
    assert abs(world_angle(0, 10) - 90.0) < 1e-9   # +Y → 90°
    assert abs(world_angle(10, 0) - 0.0) < 1e-9    # +X → 0°
    assert abs(world_angle(-10, 0) - 180.0) < 1e-9
    assert deg_error(350, 10) == 20.0
    assert deg_error(0, 180) == 180.0
    assert deg_error(90, 90) == 0.0


# ---------- 分类 ----------

def test_classify_direct_firearm():
    row = {"weapon": "ak47", "attacker_steamid": "a", "user_steamid": "b",
           "user_X": 1.0, "user_Y": 1.0, "attacker_X": 0.0, "attacker_Y": 0.0,
           "penetrated": False, "thrusmoke": False}
    assert classify_death(row) == "direct"
    assert is_firearm("ak47") and is_firearm("m4a1_silencer_vip") and is_firearm("weapon_awp")


def test_classify_wallbang_thrusmoke():
    base = {"weapon": "ak47", "attacker_steamid": "a", "user_steamid": "b",
            "user_X": 1.0, "user_Y": 1.0, "attacker_X": 0.0, "attacker_Y": 0.0}
    assert classify_death(dict(base, penetrated=True)) == "wallbang"
    assert classify_death(dict(base, thrusmoke=True)) == "thrusmoke"


def test_classify_non_firearm():
    assert classify_death({"weapon": "hegrenade", "attacker_steamid": "a", "user_steamid": "b",
                           "user_X": 1.0, "user_Y": 1.0, "attacker_X": 0.0, "attacker_Y": 0.0}) == "grenade"
    assert classify_death({"weapon": "knife", "attacker_steamid": "a", "user_steamid": "b",
                           "user_X": 1.0, "user_Y": 1.0, "attacker_X": 0.0, "attacker_Y": 0.0}) == "knife"


def test_classify_suicide_missing():
    assert classify_death({"weapon": "ak47", "attacker_steamid": "a", "user_steamid": "a",
                           "user_X": 1.0, "user_Y": 1.0, "attacker_X": 0.0, "attacker_Y": 0.0}) == "suicide"
    assert classify_death({"weapon": "ak47", "attacker_steamid": "", "user_steamid": "b",
                           "user_X": 1.0, "user_Y": 1.0, "attacker_X": 0.0, "attacker_Y": 0.0}) == "suicide"
    assert classify_death({"weapon": "ak47", "attacker_steamid": "a", "user_steamid": "b",
                           "attacker_X": 0.0, "attacker_Y": 0.0}) == "missing"


# ---------- 门禁 ----------

def test_compute_gate_passes_on_accurate():
    """准确方向（误差<10°）→ 门禁通过。"""
    errors = [0.5, 1.2, 2.0, 3.1, 4.2, 5.0, 6.5, 8.0] * 10
    g = compute_gate(errors)
    assert g["median"] <= GATE_MEDIAN_MAX_DEG
    assert g["p95"] <= GATE_P95_MAX_DEG
    assert g["bias_90"] <= 0.02 and g["bias_180"] <= 0.02
    assert g["passed"] is True


def test_compute_gate_rejects_90deg_bias():
    """90° 系统偏差 → 门禁必须失败（不能伪造通过）。"""
    errors = [90.0] * 100
    g = compute_gate(errors)
    assert g["passed"] is False
    assert g["bias_90"] > 0.02


def test_compute_gate_rejects_180deg_bias():
    errors = [179.5] * 100
    g = compute_gate(errors)
    assert g["passed"] is False


def test_compute_gate_empty_not_passed():
    g = compute_gate([])
    assert g["passed"] is False


# ---------- golden set 回归（真实 5-demo，785 直接枪械击杀） ----------

def _load_errors():
    if not FIXTURE.exists():
        pytest.skip("golden fixture 缺失")
    with open(FIXTURE, encoding="utf-8") as f:
        data = json.load(f)
    errs = []
    for d in data["demos"]:
        errs.extend(d["errors"])
    return errs


def test_golden_set_gate_passes():
    errors = _load_errors()
    assert len(errors) >= 50, "golden set 样本应 >= 50"
    g = compute_gate(errors)
    assert g["median"] <= GATE_MEDIAN_MAX_DEG, f"median {g['median']} > 10"
    assert g["p95"] <= GATE_P95_MAX_DEG, f"p95 {g['p95']} > 25"
    assert g["bias_90"] <= 0.02 and g["bias_180"] <= 0.02
    assert g["passed"] is True


def test_golden_set_90deg_rotation_rejected():
    """把每条真相方向旋转 90° → 门禁必须失败（证明门禁真在检测偏差，而不是恒真）。"""
    errors = _load_errors()
    rotated = [deg_error(e + 90.0, 0.0) for e in errors]   # 每条再偏 90°
    g = compute_gate(rotated)
    assert g["passed"] is False
    assert g["median"] > GATE_MEDIAN_MAX_DEG
