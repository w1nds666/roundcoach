"""SteamID 规范化 / 校验 / 掩码（6R14）。

关键事实：demoparser2 返回的 player.steam_id 是 SteamID64（17 位，如 7656119XXXXXXXXXX），
与 Steam OpenID 2.0 的 identity `https://steamcommunity.com/openid/id/<steamid64>` 路径段直接可比。
本模块把任意合法 SteamID 输入（SteamID64 / STEAM_0:X:Y / [U:1:account] / 纯 account）规范化为
**规范化 17 位字符串**，避免 JS 整数精度丢失；并做服务端唯一的匹配与掩码展示。
"""
from __future__ import annotations

import re

# SteamID64 恒定偏移（account → SteamID64）：76561197960265728 + account_id
STEAMID64_OFFSET = 76561197960265728
# account_id 为 32 位；合法范围 [0, 2^32 - 1]
_ACCOUNT_MAX = (1 << 32) - 1
STEAMID64_MIN = STEAMID64_OFFSET
STEAMID64_MAX = STEAMID64_OFFSET + _ACCOUNT_MAX

# Steam OpenID 2.0 官方 op_endpoint 与其 claimed_id/identity host + path 前缀
STEAM_OPENID_ENDPOINT = "https://steamcommunity.com/openid/"
STEAM_OPENID_HOST = "steamcommunity.com"
STEAM_OPENID_ID_PATH = "/openid/id/"  # identity = https://steamcommunity.com/openid/id/<steamid64>

_DIGITS_17_RE = re.compile(r"\A\d{17}\Z")
_STEAM3_UI_RE = re.compile(r"\A\[U:(\d+):(\d+)\]\Z")
_STEAM0_RE = re.compile(r"\ASTEAM_\d+:(\d+):(\d+)\Z")
_RAW_ACCOUNT_RE = re.compile(r"\A\d{1,10}\Z")


def _sanitize(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int)):
        return ""
    return str(value).strip()


def to_account_id(steamid64: str) -> int | None:
    """SteamID64 → account_id（0..2^32-1）。非法返回 None。"""
    s = _sanitize(steamid64)
    if not _DIGITS_17_RE.fullmatch(s):
        return None
    v = int(s)
    account = v - STEAMID64_OFFSET
    if 0 <= account <= _ACCOUNT_MAX:
        return account
    return None


def is_valid_steamid64(value: object) -> bool:
    """严格 17 位且在合法范围。"""
    s = _sanitize(value)
    if not _DIGITS_17_RE.fullmatch(s):
        return False
    v = int(s)
    return STEAMID64_MIN <= v <= STEAMID64_MAX


def normalize_to_steamid64(value: object) -> str | None:
    """任意合法 SteamID 输入 → 规范化 17 位字符串；无法识别返回 None。

    支持：SteamID64 字符串；STEAM_0:X:Y（account=Y*2+X）；[U:1:account]；纯 account id。
    """
    s = _sanitize(value)
    if not s:
        return None
    # 1) 已是合法 SteamID64
    if is_valid_steamid64(s):
        return str(int(s))
    # 2) [U:universe:account]
    m = _STEAM3_UI_RE.fullmatch(s)
    if m:
        account = int(m.group(2))
        if 0 <= account <= _ACCOUNT_MAX:
            return str(STEAMID64_OFFSET + account)
        return None
    # 3) STEAM_X:auth:account
    m = _STEAM0_RE.fullmatch(s)
    if m:
        auth = int(m.group(1))
        account = int(m.group(2))
        token_id = account * 2 + auth
        if 0 <= token_id <= _ACCOUNT_MAX:
            return str(STEAMID64_OFFSET + token_id)
        return None
    # 4) 纯 account id（1..10 位，<= 2^32-1）
    if _RAW_ACCOUNT_RE.fullmatch(s):
        account = int(s)
        if 0 <= account <= _ACCOUNT_MAX:
            return str(STEAMID64_OFFSET + account)
    return None


def parse_claimed_id(claimed_id: object) -> str | None:
    """从 OpenID claimed_id/identity URL 解析 SteamID64；host/path 不匹配返回 None。

    合法形式：https://steamcommunity.com/openid/id/<steamid64>（可带尾斜杠）。
    """
    s = _sanitize(claimed_id)
    if not s:
        return None
    scheme, _, rest = s.partition("://")
    if not rest:
        return None
    host, _, path = rest.partition("/")
    if host.lower() != STEAM_OPENID_HOST:
        return None
    if not path.startswith(STEAM_OPENID_ID_PATH.lstrip("/")):
        return None
    sid = path[len(STEAM_OPENID_ID_PATH.lstrip("/")):].rstrip("/")
    return sid if is_valid_steamid64(sid) else None


def mask_steamid64(value: object) -> str | None:
    """掩码展示：保留首 6 位与末 5 位，中间以 **** 代替；非法输入保守掩码为 ****。"""
    s = _sanitize(value)
    if not s:
        return None
    if is_valid_steamid64(s):
        return f"{s[:6]}****{s[-5:]}"
    # 非法输入：不原样回显，占位掩码
    return "****"
