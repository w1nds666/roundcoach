"""6R19B 应用层速率限制（原子 Redis 固定窗口 + 可信 proxy/IP + Redis 故障受限 fallback）。

背景：nginx 只对 /api/share/resolve 限速；login/register/steam/upload 应用层此前无节流。本模块为第二道闸。

设计（回应 Codex P0/P1）：
- **原子计数**：Redis 用 Lua 原子执行 `INCR` + 首请求 `EXPIRE`，并返回 (count, ttl)；并发首请求不可能同时读到
  旧计数而绕过阈值。
- **可信 proxy/IP**：只信任 `rate_limit_trusted_proxies`（IP/CIDR，逗号分隔）声明为已知 nginx/内部代理的对端；
  对可信对端取 `X-Forwarded-For` 中**最右非信任** IP 作为真实客户端；直连（对端不在可信集）一律用 socket IP，
  忽略任何 forwarding 头（防客户端伪造 X-Forwarded-For 绕过）。
- **Redis 故障 fail-open 明示 + 受限 fallback**：Redis 不可用时**不再纯放行**，而是切换到进程内固定窗口
  （线程安全）——仍按 (scope, ip) 限流（多 worker 下为 N×，但有限），并告警；同时依赖 nginx 每 location 的
  `limit_req` 作第一道闸。不放宽任何业务门禁。
- 依赖形式：`Depends(rate_limit("login"))`；超限 429 `RATE_LIMITED` + `Retry-After`。
"""
from __future__ import annotations

import ipaddress
import logging
import os
import threading
import time

from fastapi import Request
from redis import Redis
from redis.exceptions import RedisError

from app.config import settings

log = logging.getLogger("rate_limit")

# scope -> (限次数, 窗口秒)。login/share/AI 为暴力/枚举/成本重点，限额较紧；register/upload 放宽以兼容正常使用。
DEFAULT_LIMITS: dict[str, tuple[int, int]] = {
    "login": (60, 60),
    "register": (120, 60),
    "steam_start": (30, 60),
    "steam_callback": (60, 60),
    "upload": (60, 60),
    "share_resolve": (30, 60),
    "ai_report": (30, 60),
}

# 原子 Lua：INCR；首次 EXPIRE；返回 {count, ttl}
_ATOMIC_SCRIPT = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {c, redis.call('TTL', KEYS[1])}
"""

# 熔断器冷却（秒）
REDIS_FAIL_COOLDOWN = 30
_redis_client: Redis | None = None
_redis_ok = True
_redis_last_fail = 0.0


def _redis() -> Redis | None:
    """缓存 Redis 客户端；不可用时快速返回 None（不阻塞）。熔断器避免每请求连接超时。"""
    global _redis_client, _redis_ok, _redis_last_fail
    if not _redis_ok and time.time() - _redis_last_fail < REDIS_FAIL_COOLDOWN:
        return None
    if _redis_client is None:
        try:
            _redis_client = Redis.from_url(
                settings.redis_url, socket_connect_timeout=0.3, socket_timeout=0.5, decode_responses=True
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("rate_limit: redis init failed (%s)", exc)
            _redis_ok = False
            _redis_last_fail = time.time()
            return None
    try:
        _redis_client.ping()
        _redis_ok = True
        return _redis_client
    except Exception as exc:  # noqa: BLE001
        log.warning("rate_limit: redis unreachable (%s); fallback to in-process limiter", exc)
        _redis_ok = False
        _redis_last_fail = time.time()
        return None


class RATE_LIMIT_EXCEEDED(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"rate limited, retry after {retry_after}s")


def _parse_trusted(raw: str) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _is_trusted(ip: str, trusted: list[str]) -> bool:
    if not trusted or not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for spec in trusted:
        try:
            if "/" in spec and addr in ipaddress.ip_network(spec, strict=False):
                return True
        except ValueError:
            continue
        if ip == spec:
            return True
    return False


def _client_ip(request: Request, trusted: list[str]) -> str:
    """可信 proxy 策略：直连用 socket IP；可信对端取 XFF 最右非信任 IP；否则 socket IP。"""
    peer = request.client.host if request.client else "unknown"
    if not _is_trusted(peer, trusted):
        return peer  # 直连或对端不可信：用 socket IP，忽略 forwarding 头
    xff = request.headers.get("x-forwarded-for", "")
    for ip in reversed([x.strip() for x in xff.split(",") if x.strip()]):
        if not _is_trusted(ip, trusted):
            return ip
    xri = (request.headers.get("x-real-ip") or "").strip()
    return xri or peer


# ---- 进程内受限 fallback（Redis 不可用时）----
_mem_lock = threading.Lock()
_mem_store: dict[str, tuple[int, int]] = {}  # key -> (count, expires_ts)


def _mem_incr(key: str, limit: int, window: int) -> tuple[int, int]:
    """线程安全进程内固定窗口；返回 (count, ttl)。超限由调用方判断。"""
    now = time.time()
    with _mem_lock:
        entry = _mem_store.get(key)
        if entry is None or entry[1] <= now:
            count, expires = 1, now + window
        else:
            count, expires = entry[0] + 1, entry[1]
        _mem_store[key] = (count, expires)
        # 顺带清理过期条目（有界，防无限增长）
        if len(_mem_store) > 4096:
            _mem_store.clear()
        return count, max(0, int(expires - now))


def _atomic_incr(r: Redis, key: str, window: int) -> tuple[int, int]:
    """Redis 原子 INCR+EXPIRE；返回 (count, ttl)。"""
    res = r.eval(_ATOMIC_SCRIPT, 1, key, str(window))
    count = int(res[0])
    ttl = max(0, int(res[1]))
    return count, ttl


def _limit_of(scope: str) -> tuple[int, int]:
    limit, window = DEFAULT_LIMITS.get(scope, (60, 60))
    env_limit = os.getenv(f"RATE_LIMIT_{scope.upper()}")
    if env_limit:
        limit = int(env_limit)
    return limit, window


def rate_limit(scope: str):
    """返回 FastAPI 依赖：按 (scope, 可信客户端 IP) 原子计数；超限抛 RATE_LIMIT_EXCEEDED。"""
    limit, window = _limit_of(scope)

    def dependency(request: Request):
        if not settings.rate_limit_enabled:
            return
        trusted = _parse_trusted(settings.rate_limit_trusted_proxies)
        ip = _client_ip(request, trusted)
        key = f"rl:{scope}:{ip}"
        r = _redis()
        if r is not None:
            try:
                count, ttl = _atomic_incr(r, key, window)
            except RATE_LIMIT_EXCEEDED:
                raise
            except RedisError as exc:
                log.warning("rate_limit: redis error scope=%s (%s); in-process fallback", scope, exc)
                count, ttl = _mem_incr(key, limit, window)
            except Exception as exc:  # noqa: BLE001
                log.warning("rate_limit: unexpected scope=%s (%s); in-process fallback", scope, exc)
                count, ttl = _mem_incr(key, limit, window)
        else:
            # Redis 不可用：受限 fallback（并非纯放行），并明示告警。
            log.warning("rate_limit: redis unavailable scope=%s ip=<redacted>; in-process fallback active", scope)
            count, ttl = _mem_incr(key, limit, window)
        if count > limit:
            raise RATE_LIMIT_EXCEEDED(int(ttl or window))

    return dependency
