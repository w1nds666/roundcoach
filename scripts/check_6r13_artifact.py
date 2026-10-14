"""6R13：校验回填的 v3.0 tactical artifact 新字段计数（只读）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.storage import storage  # noqa: E402
from app.services.routes import gunzip_json  # noqa: E402

for mid in [59, 60, 61, 62, 63]:
    data = storage.get_bytes(f"tactical/{mid}/3.0/tactical.json.gz")
    a = gunzip_json(data)
    k = sum(len(r.get("killfeed", [])) for r in a["rounds"])
    b = sum(len(r.get("blinds", [])) for r in a["rounds"])
    h = sum(len(r.get("he_damage", [])) for r in a["rounds"])
    props = a.get("prop_count")
    print(f"match {mid}: schema={a['schema_version']} rounds={len(a['rounds'])} "
          f"props={props} killfeed={k} blinds={b} he_damage={h}")
