"""6R21F-E6A-COORD-CACHE：coordination 确定性计算的后端安全缓存。

设计原则（合同）：
- 缓存的是「确定性纯计算」的最终序列化 payload（build_coordination_analysis +
  _build_area_state_machine_payload）；**绝不缓存鉴权结果**——ownership/tier/entitlement
  每次请求都先于缓存查表执行。
- key 覆盖 demo/parser/schema/算法版本 + 相关输入（round_no、focus、identity、
  events、region geometry、team_names）；任一维度变化 → 新 key → 失效（重算）。
- 双通道 + 单飞：进程内 LRU(L1) + Redis(L2)；同 key 并发只计算一次（striped lock）。
- 失败不污染：build 抛错则不写任何缓存，回到未构建状态，下次真正重算。
- 用户/权限隔离：key 含 focus_player_id 与 demo 数据指纹；缓存值仅对已通过
  ownership/tier/entitlement 的请求返回（鉴权先于查表），绝不跨用户串用 player 段。
- 热路径显著低于 2s（缓存命中时跳过两个纯计算 ~5.5s+~5.4s；目标 ~200ms）。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict

from redis import Redis
from redis.exceptions import RedisError

from app.config import settings
from app.services.coordination import COORDINATION_SCHEMA_VERSION
from app.services.coordination_thresholds import VERSION as THRESHOLDS_VERSION

# 缓存 TTL（秒）。key 已覆盖版本/数据指纹，TTL 主要兜底回收陈旧条目。
DEFAULT_TTL = 6 * 60 * 60
# 进程内 LRU 容量：只保留最近构建的 N 个 key，避免常驻无限增长。
_MEM_MAX = 32
_MEM_TTL = DEFAULT_TTL
# 单飞 striped 锁数量（有界；不同 key 大多命中不同 stripe）。
_LOCK_STRIPES = 32

# 缓存 generation：coordination_cache_reset() 时 +1，使旧 Redis 键立即失效（测试/手动失效用）。
_generation = 0

# ---- Redis 客户端（带熔断，仿 rate_limit，Redis 不可用自动降级为进程内） ----
_redis_client: Redis | None = None
_redis_ok = True
_redis_last_fail = 0.0
_REDIS_FAIL_COOLDOWN = 5.0


def _redis() -> Redis | None:
    global _redis_client, _redis_ok, _redis_last_fail
    if _redis_client is not None and _redis_ok:
        return _redis_client
    if not _redis_ok and time.time() - _redis_last_fail < _REDIS_FAIL_COOLDOWN:
        return None
    if _redis_client is None:
        try:
            _redis_client = Redis.from_url(
                settings.redis_url, socket_connect_timeout=0.3, socket_timeout=0.5, decode_responses=False
            )
        except Exception:  # noqa: BLE001
            _redis_ok = False
            _redis_last_fail = time.time()
            return None
    try:
        _redis_client.ping()
        _redis_ok = True
        return _redis_client
    except RedisError:
        _redis_ok = False
        _redis_last_fail = time.time()
        return None


# ---- 进程内 L1（按 generation 隔离）+ 统计 ----
_mem: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
_mem_lock = threading.RLock()
_single_flight_locks = [threading.Lock() for _ in range(_LOCK_STRIPES)]
_stats = {"builds": 0, "hits_mem": 0, "hits_redis": 0, "coalesced": 0, "cold": 0}


def _redis_key(key: str) -> str:
    return f"cc:{_generation}:{key}"


def coordination_cache_reset() -> None:
    """清空进程内缓存 + 把 Redis 键切换到新的 generation（旧键由 TTL 回收），并归零统计。"""
    global _generation
    with _mem_lock:
        _mem.clear()
        _generation += 1
    _stats.update({"builds": 0, "hits_mem": 0, "hits_redis": 0, "coalesced": 0, "cold": 0})


def coordination_cache_forget_mem() -> None:
    """只清空进程内 L1（保留 Redis 层与 generation），用于展示跨进程 Redis 命中 / L1 抖动。"""
    with _mem_lock:
        _mem.clear()


def coordination_cache_stats() -> dict:
    return dict(_stats)


def _canon(v):
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return "NONFINITE"
        return round(v, 6)
    if isinstance(v, dict):
        return {str(k): _canon(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    return v


def coordination_cache_key(*, algo_schema=None, algo_thresholds=None, demo_id=None, match_id=None,
                           map_name=None, artifact_sha=None, artifact_schema=None, round_no=None,
                           focus_player_id=None, identity_fp=None, team_names=None, region_hash=None,
                           events_fp=None) -> str:
    """构建确定性缓存 key（sha256）：覆盖算法版本 + demo/parser/schema + 全部相关输入。

    算法版本 = coordination schema + thresholds version（口径变更 → 失效）。
    数据版本 = artifact sha256 + artifact schema（解析器/数据变更 → 失效）。
    """
    algo_schema = algo_schema or COORDINATION_SCHEMA_VERSION
    algo_thresholds = algo_thresholds or THRESHOLDS_VERSION
    parts = {
        "algo": f"{algo_schema}:{algo_thresholds}",
        "demo_id": demo_id,
        "match_id": match_id,
        "map_name": map_name,
        "artifact_sha": artifact_sha,
        "artifact_schema": artifact_schema,
        "round_no": round_no,
        "focus_player_id": focus_player_id,
        "identity_fp": identity_fp,
        "team_names": _canon(team_names),
        "region_hash": region_hash,
        "events_fp": events_fp,
    }
    b = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(b).hexdigest()


def _mem_get(key: str) -> bytes | None:
    now = time.time()
    with _mem_lock:
        item = _mem.get(key)
        if item is None:
            return None
        payload, exp = item
        if exp < now:
            _mem.pop(key, None)
            return None
        return payload


def _mem_set(key: str, payload: bytes) -> None:
    with _mem_lock:
        _mem[key] = (payload, time.time() + _MEM_TTL)
        _mem.move_to_end(key)
        while len(_mem) > _MEM_MAX:
            _mem.popitem(last=False)


def _redis_get(key: str) -> bytes | None:
    r = _redis()
    if r is None:
        return None
    try:
        return r.get(_redis_key(key))
    except RedisError:
        return None


def _redis_set(key: str, payload: bytes) -> None:
    r = _redis()
    if r is None:
        return
    try:
        r.set(_redis_key(key), payload, ex=DEFAULT_TTL)
    except RedisError:
        return


def coordination_cache_get_or_build(key: str, build_fn):
    """返回 (payload_bytes, source)。source ∈ {build, mem, redis, coalesced}。

    - L1 进程内命中 → mem；L2 Redis 命中 → redis（并回填 L1）。
    - 同 key 并发只计算一次（striped lock 单飞）；后到者等待后回读 → coalesced。
    - build_fn 抛错则不写任何缓存（失败不污染），异常向上传播。
    """
    payload = _mem_get(key)
    if payload is not None:
        _stats["hits_mem"] += 1
        return payload, "mem"

    payload = _redis_get(key)
    if payload is not None:
        _mem_set(key, payload)
        _stats["hits_redis"] += 1
        return payload, "redis"

    lock = _single_flight_locks[hash(key) % _LOCK_STRIPES]
    with lock:
        # 单飞：持锁后回读（另一个线程可能已构建/已缓存）。
        payload = _mem_get(key)
        if payload is not None:
            _stats["coalesced"] += 1
            return payload, "coalesced"
        payload = _redis_get(key)
        if payload is not None:
            _mem_set(key, payload)
            _stats["coalesced"] += 1
            return payload, "coalesced"
        payload = build_fn()  # 失败抛异常 → 不写缓存
        _mem_set(key, payload)
        _redis_set(key, payload)
        _stats["builds"] += 1
        return payload, "build"
