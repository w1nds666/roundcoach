"""DeepSeek 外发 payload 白名单构建（Phase 5B 第 2 项）。

构造专门的 outbound payload——绝不可把数据库 snapshot 原样发送。
允许：schema_version、map、比分/回合/时长、匿名 player_index 统计、团队聚合、
武器/道具聚合、可用热图模式、明确 null 的未实现指标。
禁止：demo.id、file_name、file_path、DB id、input_hash、Steam ID、昵称、
原始 demo、tick 坐标/事件明细、日志、Cookie/token、任何个人信息。
"""
from __future__ import annotations

import json


def build_outbound_payload(snapshot: dict) -> dict:
    """从 metrics snapshot 提取白名单字段，构造外发 payload。"""
    players = []
    for p in snapshot.get("players", []):
        players.append({
            "player_index": p.get("player_index"),
            "team": p.get("team"),
            "kills": p.get("kills"),
            "deaths": p.get("deaths"),
            "assists": p.get("assists"),
            "hs_rate": p.get("hs_rate"),
            "damage": p.get("damage"),
            "adr": p.get("adr"),
            "first_kills": p.get("first_kills"),
        })

    return {
        "schema_version": snapshot.get("schema_version"),
        "match": {
            "map_name": snapshot.get("match", {}).get("map_name"),
            "score_ct": snapshot.get("match", {}).get("score_ct"),
            "score_t": snapshot.get("match", {}).get("score_t"),
            "rounds": snapshot.get("match", {}).get("rounds"),
            "duration_seconds": snapshot.get("match", {}).get("duration_seconds"),
        },
        "players": players,
        "teams": snapshot.get("teams", {}),
        "kills": snapshot.get("kills", {}),
        "grenades": snapshot.get("grenades", {}),
        "heatmaps": {"available": snapshot.get("heatmaps", {}).get("available", [])},
        "unimplemented": snapshot.get("unimplemented", {}),
    }


def build_chat_request_body(payload: dict, system_prompt: str, user_prompt: str,
                            thinking: bool = False) -> dict:
    """构造 OpenAI-compatible chat/completions 请求体（Phase 5B 第 1/3 项）。

    参数：thinking 默认 False（低延迟/低 token）；max_tokens 受配置硬上限约束。
    """
    from app.config import settings

    body = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": settings.deepseek_max_output_tokens,
        "thinking": {"type": "enabled"} if thinking else {"type": "disabled"},
    }
    return body


def redact_body_for_log(body: dict) -> dict:
    """日志用脱敏副本：仅保留结构信息，绝不含 key/完整 prompt/指标数据。"""
    return {
        "model": body.get("model"),
        "thinking": body.get("thinking", {}).get("type"),
        "max_tokens": body.get("max_tokens"),
        "messages": [{"role": m.get("role"), "content_len": len(m.get("content", ""))} for m in body.get("messages", [])],
    }


def payload_json(payload: dict) -> str:
    """payload 的 canonical JSON（白名单内容，供发送）。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
