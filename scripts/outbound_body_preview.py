"""输出『将要发送的最终 HTTP body』样例（key 遮蔽）供 Codex/人类复核。

用法：DEBUG=true python scripts/outbound_body_preview.py
说明：仅构造展示，不发送任何请求；Authorization 用占位符。
"""
import json
import os
import sys

os.environ.setdefault("DEBUG", "true")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.services.ai_metrics import build_metrics_snapshot
from app.services.ai_outbound import build_chat_request_body, build_outbound_payload
from app.services.ai_report_prompt import SYSTEM_PROMPT, build_user_prompt


def main():
    # 用真实 demo 1 的指标快照构造 outbound payload（白名单）
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(os.environ.get("DEMO_DB_URL", "sqlite://"))
    # 无真实 DB 时无法跑；直接改用样例文件
    payload = None
    sample_path = os.path.join(os.path.dirname(__file__), "..", "docs", "phase5-metrics-sample.json")
    if os.path.exists(sample_path):
        payload = json.load(open(sample_path, encoding="utf-8"))
    if payload is None:
        print("需要 docs/phase5-metrics-sample.json 或真实数据库")
        return

    body = build_chat_request_body(payload, SYSTEM_PROMPT, build_user_prompt(payload), thinking=False)

    print("=" * 70)
    print("最终 HTTP POST {base_url}/chat/completions")
    print("Headers: Authorization: Bearer <DEEPSEEK_API_KEY 已遮蔽> | Content-Type: application/json")
    print("=" * 70)
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print("=" * 70)
    print(f"payload 大小: {len(json.dumps(payload, ensure_ascii=False))} 字节")
    print("提示：body 不含 demo.id/file_name/steam/昵称；玩家仅 player_index。")


if __name__ == "__main__":
    main()
