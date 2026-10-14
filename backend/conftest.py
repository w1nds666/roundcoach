"""pytest 全局配置：测试环境允许使用开发默认 JWT_SECRET（DEBUG=true）。
6R19B：默认关闭应用层限流（RATE_LIMIT_ENABLED=false），避免 Redis 缺失时进程内 fallback 在测试间累积/误伤；
专项限流/outage 测试自行把 settings.rate_limit_enabled 置回 True。
"""
import os

os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
