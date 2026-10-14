"""Steam OpenID 2.0 服务端认证核心（6R14）。

单一路径：
- start 用 `build_auth_url` 生成对 Steam 的认证跳转 URL（含我们的一次性 state，绑定用户/return target）。
- callback 用 `validate_callback` 严格校验参数，并把返回参数 POST 给 Steam
  `check_authentication`（`is_valid:true` 才接受）。任何一步失败都返回明确 reason_code，
  绝不信任前端提交的 steamid。

verifier 抽象：`check_authentication(params)` 返回 `{"is_valid": bool}`。
真实实现走 httpx POST（Steam 官方端点）；测试/门禁用 FakeVerifier 注入（fake provider）。
所有 callback 参数 / token / cookie 不落日志；只输出 reason_code（不含完整 SteamID）。
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode

from app.config import settings
from app.services.steamid import STEAM_OPENID_ENDPOINT, STEAM_OPENID_HOST, STEAM_OPENID_ID_PATH, is_valid_steamid64, parse_claimed_id

NS_2_0 = "http://specs.openid.net/auth/2.0"
MODE_IDRES = "id_res"
MODE_CANCEL = "cancel"
MODE_CHECKID = "checkid_setup"
MODE_CHECK_AUTH = "check_authentication"

# 稳定 reason_code（供前端/测试断言）
R_CANCEL = "OPENID_CANCEL"
R_BAD_MODE = "OPENID_BAD_MODE"
R_BAD_NS = "OPENID_BAD_NS"
R_BAD_OP_ENDPOINT = "OPENID_BAD_OP_ENDPOINT"
R_CLAIMED_MISMATCH = "OPENID_CLAIMED_MISMATCH"
R_BAD_CLAIMED_ID = "OPENID_BAD_CLAIMED_ID"
R_BAD_RETURN_TO = "OPENID_BAD_RETURN_TO"
R_BAD_REALM = "OPENID_BAD_REALM"
R_STATE_INVALID = "OPENID_STATE_INVALID"
R_STATE_EXPIRED = "OPENID_STATE_EXPIRED"
R_STATE_USED = "OPENID_STATE_USED"
R_NONCE_REPLAY = "OPENID_NONCE_REPLAY"
R_CHECK_FAILED = "OPENID_CHECK_FAILED"
R_MISSING_PARAMS = "OPENID_MISSING_PARAMS"
R_OK = "OPENID_OK"


def new_state() -> str:
    """高熵 state（一次性）。"""
    return secrets.token_urlsafe(32)


def build_realm(scheme: str, host: str) -> str:
    return f"{scheme}://{host}"


def build_callback_url(scheme: str, host: str, path: str | None = None) -> str:
    path = path or settings.steam_openid_callback_path
    return f"{scheme}://{host}{path}"


def is_host_allowed(host: str) -> bool:
    allowed = {h.strip() for h in settings.steam_openid_allowed_hosts.split(",") if h.strip()}
    return host in allowed


def assert_host_allowed(scheme: str, host: str) -> None:
    """host 必须在白名单；非 debug 强制 HTTPS（HTTP 降级防御）。"""
    if not is_host_allowed(host):
        raise ValueError("steam openid return_to host 不在白名单")
    if settings.steam_openid_require_https and scheme != "https":
        raise ValueError("steam openid 生产环境必须使用 HTTPS 回调")


class SteamOpenIDError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


class RealHttpVerifier:
    """真实 check_authentication：把 callback 的 openid.* 参数 POST 回 Steam 官方端点。"""

    def __init__(self, post: Callable | None = None, provider: str | None = None):
        # post 注入点：测试可替换成同步调用；默认 httpx。provider 默认为官方端点。
        self._post = post
        self.provider = provider or STEAM_OPENID_ENDPOINT

    def _https_post(self, url: str, data: dict) -> str:
        import httpx

        resp = httpx.post(url, data=data, timeout=settings.deepseek_connect_timeout, follow_redirects=False)
        return resp.text

    def check_authentication(self, params: dict[str, str]) -> dict:
        post = self._post or self._https_post
        body = dict(params)
        body["openid.mode"] = MODE_CHECK_AUTH
        text = post(self.provider, body)
        # 返回形如 "ns:...\nis_valid:true\n"
        result: dict[str, str] = {}
        for line in text.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                result[k.strip()] = v.strip()
        is_valid = result.get("is_valid", "").lower() == "true"
        return {"is_valid": is_valid, "raw": result}


class FakeVerifier:
    """fake provider verifier：返回预设结果，供单元/集成/门禁自动测试（不依赖真实 Steam）。"""

    def __init__(self, is_valid: bool = True, provider: str | None = None):
        self.is_valid = is_valid
        self.provider = provider or STEAM_OPENID_ENDPOINT
        self.calls: list[dict] = []

    def check_authentication(self, params: dict[str, str]) -> dict:
        self.calls.append(params)
        return {"is_valid": self.is_valid}


@dataclass
class CallbackResult:
    ok: bool
    steam_id64: str | None
    reason_code: str
    reason: str
    response_nonce: str | None = None
    persona_name: str | None = None   # 6R21：Steam sreg.nickname（只读徽标，绝不写 users.nickname）


def build_auth_url(callback_url: str, realm: str, state_value: str) -> str:
    """生成对 Steam 的认证跳转 URL（openid.return_to 内嵌 state，Steam 会原样回带其中自定义 query）。

    Steam 会填充 identity/claimed_id 为实际用户 id。realm=站点 origin，return_to=我们的 callback。
    """
    params = {
        "openid.ns": NS_2_0,
        "openid.mode": MODE_CHECKID,
        "openid.identity": f"https://{STEAM_OPENID_HOST}/openid/id/",
        "openid.claimed_id": f"https://{STEAM_OPENID_HOST}/openid/id/",
        "openid.return_to": f"{callback_url}?state={state_value}",
        "openid.realm": realm,
        # 6R21：请求 sreg.nickname（Steam persona 名；仅作为只读徽标，不写 users.nickname）
        "openid.ns.sreg": "http://openid.net/extensions/sreg/1.1",
        "openid.sreg.required": "nickname",
    }
    return f"{settings.steam_openid_provider}login?{urlencode(params)}"


def validate_callback(params: dict, state_row: object | None, verifier: Callable) -> CallbackResult:
    """严格校验 OpenID 2.0 回调。params 为 callback 的查询参数；state_row 为一次性 state 行；verifier 为 check_authentication 可调用。

    verifier(params) 返回 {"is_valid": bool}（仿真 Steam check_authentication）。
    返回 CallbackResult（不包含 callback 原始参数/完整标识，避免泄漏）。
    """
    mode = params.get("openid.mode")
    # cancel：用户取消，不视为攻击
    if mode is None:
        return CallbackResult(False, None, R_MISSING_PARAMS, "缺少 openid.mode")
    if mode == MODE_CANCEL:
        return CallbackResult(False, None, R_CANCEL, "用户取消了 Steam 登录")
    if mode != MODE_IDRES:
        return CallbackResult(False, None, R_BAD_MODE, "非预期的 openid.mode")

    ns = params.get("openid.ns")
    if ns != NS_2_0:
        return CallbackResult(False, None, R_BAD_NS, "openid.ns 非 OpenID 2.0")

    op_endpoint = params.get("openid.op_endpoint", "")
    if op_endpoint != settings.steam_openid_provider.rstrip("/") and op_endpoint != settings.steam_openid_provider:
        return CallbackResult(False, None, R_BAD_OP_ENDPOINT, "op_endpoint 非 Steam 官方端点")

    claimed = params.get("openid.claimed_id", "")
    identity = params.get("openid.identity", "")
    if claimed != identity:
        return CallbackResult(False, None, R_CLAIMED_MISMATCH, "claimed_id != identity")
    steam_id64 = parse_claimed_id(claimed)
    if steam_id64 is None:
        return CallbackResult(False, None, R_BAD_CLAIMED_ID, "claimed_id 不是合法的 SteamID64")

    # state 校验
    state_value = params.get("state", "")
    if state_row is None:
        return CallbackResult(False, None, R_STATE_INVALID, "state 不存在")
    if str(state_row.state) != state_value:
        return CallbackResult(False, None, R_STATE_INVALID, "state 不匹配")
    if state_row.used:
        return CallbackResult(False, None, R_STATE_USED, "state 已被使用（疑似重放）")
    from datetime import datetime, timezone

    expires = state_row.expires_at
    if expires and expires.replace(tzinfo=None) < datetime.now(timezone.utc).replace(tzinfo=None):
        return CallbackResult(False, None, R_STATE_EXPIRED, "state 已过期")

    # return_to 精确匹配我们发出的 callback_url
    expected_return_to = f"{state_row.callback_url}?state={state_value}"
    actual_return_to = params.get("openid.return_to", "")
    if actual_return_to != expected_return_to:
        return CallbackResult(False, None, R_BAD_RETURN_TO, "return_to 与本站配置不一致")

    # realm 若回带则须与我们存储的一致（defense in depth；部分 provider 不回带 realm）
    realm = params.get("openid.realm")
    if realm and realm != state_row.realm:
        return CallbackResult(False, None, R_BAD_REALM, "realm 与本站配置不一致")

    # 防重放：response_nonce 唯一
    nonce = params.get("openid.response_nonce")
    if state_row.response_nonce and state_row.response_nonce == nonce:
        return CallbackResult(False, None, R_NONCE_REPLAY, "response_nonce 重复（重放）")

    # check_authentication：必须 is_valid:true
    # 兼容两种接口：可调用对象 verifier(params)，或带 .check_authentication(params) 的对象
    if callable(verifier):
        resp = verifier(params)
    else:
        resp = verifier.check_authentication(params)
    if not resp.get("is_valid"):
        return CallbackResult(False, None, R_CHECK_FAILED, "Steam check_authentication 校验失败")

    return CallbackResult(True, steam_id64, R_OK, "绑定成功", response_nonce=nonce,
                          persona_name=params.get("openid.sreg.nickname") or None)
