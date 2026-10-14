"""6R8 字段勘探：列出 demoparser2 可读的精确字段/事件名（不猜字段）。

用法: python scripts/explore_6r8_fields.py <demo_path> <map> <label>
只读勘探，不做任何写入。
"""
from __future__ import annotations

import sys
from pathlib import Path

from demoparser2 import DemoParser

demo = sys.argv[1]
label = sys.argv[2]

print(f"\n##### {label}: {Path(demo).name} #####")
p = DemoParser(demo)

# --- 事件名（game events） ---
ge = p.list_game_events()
print("\n=== list_game_events() ===")
print(type(ge).__name__)
if isinstance(ge, list):
    for g in ge:
        print(" ", g)
else:
    print(" ", ge)

# --- 玩家实体可更新字段（含眼球角度候选） ---
uf = p.list_updated_fields()
print("\n=== list_updated_fields() ===")
if isinstance(uf, list):
    eye = [f for f in uf if any(k in str(f).lower() for k in ("eye", "angle", "yaw", "pitch", "tang", "view", "looking", "cam"))]
    print(" eye/angle candidates:", eye)
else:
    print(" ", uf)

# --- parse_grenades 列 ---
print("\n=== parse_grenades() columns ===")
g = p.parse_grenades()
print(" columns:", list(g.columns))
print(" shape:", g.shape)
print(" head:")
print(g.head(3).to_string())
