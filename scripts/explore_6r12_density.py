"""6R12 探针：对比 range-parse（tactical 当前做法）与 explicit-list parse 的 yaw 稠密度。

用法: python scripts/explore_6r12_density.py <demo_path> <sid> <round_windowタ>
打印：range 里该玩家在有坐标的行里 yaw 非空的比例；explicit 每 tick 解析的比例。
"""
from __future__ import annotations

import sys

import numpy as np
from demoparser2 import DemoParser

demo = sys.argv[1]
sid = sys.argv[2]
p = DemoParser(demo)
# 用 CDemoFileInfo 读取权威播放 tick 数
info = None
try:
    import struct
    with open(demo, "rb") as f:
        head = f.read(12)
        if head[:8] == b"PBDEMS2\x00":
            off = struct.unpack("<I", head[8:12])[0]
            f.seek(off); data = f.read(1 << 20)
            import sys as _s
            def _vv(b, i):
                r = 0; s = 0
                while i < len(b):
                    x = b[i]; i += 1; r |= (x & 0x7F) << s
                    if not (x & 0x80): return r, i
                    s += 7
                    if s > 63: break
                return r, i
            _c, i = _vv(data, 0); _t, i = _vv(data, i); size, i = _vv(data, i)
            payload = data[i:i + size] if i + size <= len(data) else b""
            j = 0
            while j < len(payload):
                key, j = _vv(payload, j); fid = key >> 3; wire = key & 7
                if wire == 0:
                    v, j = _vv(payload, j)
                    if fid == 2: info = int(v)
                elif wire == 5:
                    if j + 4 <= len(payload): j += 4
                elif wire == 2:
                    ln, j = _vv(payload, j); j += ln
                else:
                    break
except Exception as e:  # noqa: BLE001
    print("read info error", e)
if not info:
    info = int(p.parse_ticks(["tick"])["tick"].max() or 0)
playout = info
print(f"playout={playout} sid={sid}")

# range 16 采样（像 tactical 的 yaw_tick_step=16）
rr = p.parse_ticks(["CCSPlayerPawn.m_angEyeAngles", "X", "Y", "Z", "health", "steamid", "tick"],
                   ticks=range(0, playout + 1, 16))
rows = rr.to_dict(orient="records")
print("range rows total:", len(rows))
mine = [r for r in rows if str(r.get("steamid")) == sid]
print("range rows for this sid:", len(mine))
if mine:
    with_xy = [r for r in mine if r.get("X") is not None or r.get("Y") is not None]
    yaws = [r.get("CCSPlayerPawn.m_angEyeAngles") for r in mine]
    has_yaw = [y is not None and isinstance(y, (list, tuple)) and len(y) >= 2 for y in yaws]
    ticks = [int(r["tick"]) for r in mine]
    print(f"  rows_with_xy={len(with_xy)} rows_with_yaw={sum(has_yaw)}  yaw_ratio={sum(has_yaw)/len(mine):.3f}")
    print(f"  tick span {min(ticks)}..{max(ticks)}  steps={sorted(set(ticks))[:20]}")
    print("  yaw non-null sample:", [y for y, h in zip(yaws, has_yaw) if h][:8])

# explicit: 每 16 tick 单独 list 一次？不——explicit 用一个大 list。这里模拟 tactical: 收集全部 16 步 tick 用 list 一次。
e_ticks = list(range(0, playout + 1, 16))
ee = p.parse_ticks(["CCSPlayerPawn.m_angEyeAngles", "X", "Y", "steamid", "tick"], ticks=e_ticks)
erows = ee.to_dict(orient="records")
emine = [r for r in erows if str(r.get("steamid")) == sid]
print("\nexplicit-list rows for this sid:", len(emine))
if emine:
    yaws = [r.get("CCSPlayerPawn.m_angEyeAngles") for r in emine]
    has_yaw = [y is not None and isinstance(y, (list, tuple)) and len(y) >= 2 for y in yaws]
    ticks = [int(r["tick"]) for r in emine]
    print(f"  rows={len(emine)} has_yaw={sum(has_yaw)} ratio={sum(has_yaw)/len(emine):.3f}")
    print(f"  tick span {min(ticks)}..{max(ticks)}")
