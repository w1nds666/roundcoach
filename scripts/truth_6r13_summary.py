"""6R13 truth/golden 摘要统计（只读，从回填的 v3.0 artifact + player_blind 化验）。"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.storage import storage  # noqa: E402
from app.services.routes import gunzip_json  # noqa: E402

MATCHES = {19: 59, 20: 60, 21: 61, 22: 62, 23: 63}
NAMES = {19: "de_mirage", 20: "de_anubis", 21: "de_dust2", 22: "de_inferno", 23: "de_ancient"}

grand = {"props": 0, "known_thrower": 0, "killfeed": 0, "kf_known_atk": 0, "kf_known_vic": 0,
         "blinds": 0, "blind_cls": Counter(), "he": 0}
for demo, mid in MATCHES.items():
    a = gunzip_json(storage.get_bytes(f"tactical/{mid}/3.0/tactical.json.gz"))
    props = sum(len(r["props"]) for r in a["rounds"])
    known = sum(1 for r in a["rounds"] for p in r["props"] if p.get("thrower_player_id"))
    kf = sum(len(r["killfeed"]) for r in a["rounds"])
    kf_atk = sum(1 for r in a["rounds"] for k in r["killfeed"] if k.get("attacker_player_id"))
    kf_vic = sum(1 for r in a["rounds"] for k in r["killfeed"] if k.get("victim_player_id"))
    bl = sum(len(r["blinds"]) for r in a["rounds"])
    cls = Counter(c for r in a["rounds"] for b in r["blinds"] for c in [b["flash_class"]])
    he = sum(len(r["he_damage"]) for r in a["rounds"])
    print(f"demo{demo} {NAMES[demo]}: props={props} known_thrower={known} "
          f"killfeed={kf} atk_known={kf_atk} vic_known={kf_vic} blinds={bl} "
          f"cls={dict(cls)} he={he}")
    grand["props"] += props; grand["known_thrower"] += known
    grand["killfeed"] += kf; grand["kf_known_atk"] += kf_atk; grand["kf_known_vic"] += kf_vic
    grand["blinds"] += bl; grand["blind_cls"].update(cls); grand["he"] += he

print("\n== GRAND ==")
print(f"props={grand['props']} known_thrower={grand['known_thrower']} "
      f"({100*grand['known_thrower']/grand['props']:.1f}%)")
print(f"killfeed={grand['killfeed']} atk_known={grand['kf_known_atk']} "
      f"({100*grand['kf_known_atk']/grand['killfeed']:.1f}%) vic_known={grand['kf_known_vic']}")
print(f"blinds={grand['blinds']} class={dict(grand['blind_cls'])} he={grand['he']}")
