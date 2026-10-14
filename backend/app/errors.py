"""统一 API 错误（稳定机器码 + 中文提示）。

所有业务错误统一返回：{"detail": {"code": "<MACHINE_CODE>", "message": "中文提示"}}
机器码供前端/测试断言使用，message 供用户阅读。
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.services.rate_limit import RATE_LIMIT_EXCEEDED

# 错误码表（稳定，勿改）
EMAIL_EXISTS = "EMAIL_EXISTS"                     # 409 注册邮箱已存在
INVALID_CREDENTIALS = "INVALID_CREDENTIALS"       # 401 邮箱或密码错误
TOKEN_EXPIRED = "TOKEN_EXPIRED"                   # 401 token 过期
TOKEN_INVALID = "TOKEN_INVALID"                   # 401 token 非法/伪造
AUTH_REQUIRED = "AUTH_REQUIRED"                   # 401 未登录
DEMO_NOT_FOUND = "DEMO_NOT_FOUND"                 # 404 demo 不存在
DEMO_NOT_PARSED = "DEMO_NOT_PARSED"               # 409 demo 未解析完成
DEMO_FORBIDDEN = "DEMO_FORBIDDEN"                 # 403 非本人 demo
FREE_DAILY_LIMIT = "FREE_DAILY_LIMIT"             # 429 免费额度已用尽
SUBSCRIPTION_REQUIRED = "SUBSCRIPTION_REQUIRED"   # 403 需订阅
FILE_TYPE_INVALID = "FILE_TYPE_INVALID"           # 415 扩展名不是 .dem
FILE_TOO_LARGE = "FILE_TOO_LARGE"                 # 413 超过 100MiB
FILE_CONTENT_INVALID = "FILE_CONTENT_INVALID"     # 422 内容不是合法 demo
QUEUE_UNAVAILABLE = "QUEUE_UNAVAILABLE"           # 503 解析队列不可用
AI_REPORT_NOT_FOUND = "AI_REPORT_NOT_FOUND"       # 404 AI 报告不存在
AI_REPORT_QUALITY_FAILED = "AI_REPORT_QUALITY_FAILED"  # 422 报告质量校验未通过
DUPLICATE_DEMO = "DUPLICATE_DEMO"                     # 409 当前用户已上传相同内容
BATCH_SIZE_INVALID = "BATCH_SIZE_INVALID"
UPLOAD_FAILED = "UPLOAD_FAILED"
DEMO_ALREADY_DELETED = "DEMO_ALREADY_DELETED"
DEMO_NOT_DELETED = "DEMO_NOT_DELETED"
DEMO_SOURCE_MISSING = "DEMO_SOURCE_MISSING"
SHARE_NOT_FOUND = "SHARE_NOT_FOUND"
ROUTE_UNAVAILABLE = "ROUTE_UNAVAILABLE"           # 409 该 demo 无可用路线回放数据（旧 demo/缺失）
TACTICAL_UNAVAILABLE = "TACTICAL_UNAVAILABLE"     # 409 该 demo 无可用战术回放数据（旧 demo/缺失/损坏）
NAVMESH_NOT_FOUND = "NAVMESH_NOT_FOUND"           # 404 地图 NavMesh 不存在/未接入（版本化产品路由）

# 6R14：Steam 身份 / 权益边界 / 团队
STEAM_IDENTITY_REQUIRED = "STEAM_IDENTITY_REQUIRED"   # 403 需绑定 Steam 才能使用个人进阶功能
STEAM_IDENTITY_NOT_IN_DEMO = "STEAM_IDENTITY_NOT_IN_DEMO"  # 403 这场没有识别到你（绑定 Steam 不在 roster）
STEAM_IDENTITY_CONFLICT = "STEAM_IDENTITY_CONFLICT"  # 409 该 Steam 已绑定其他账号/身份冲突
STEAM_BIND_REQUIRED = "STEAM_BIND_REQUIRED"          # 403 必须先绑定 Steam
STEAM_COOLDOWN = "STEAM_COOLDOWN"                    # 409 换绑/解绑冷却期内
RECENT_AUTH_REQUIRED = "RECENT_AUTH_REQUIRED"        # 403 需要最近 15 分钟密码再认证
STEAM_CSRF_INVALID = "STEAM_CSRF_INVALID"            # 403 CSRF/recent-auth token 无效
TEAM_REQUIRED = "TEAM_REQUIRED"                      # 403 需要团队工作区
TEAM_MAX_ONE = "TEAM_MAX_ONE"                        # 409 一个 Max 账号最多一个团队工作区
TEAM_SEAT_FULL = "TEAM_SEAT_FULL"                    # 409 团队座位已满
TEAM_SEAT_CONFLICT = "TEAM_SEAT_CONFLICT"            # 409 该 Steam 已属于其他 active 团队
TEAM_NOT_MEMBER = "TEAM_NOT_MEMBER"                  # 403 非团队成员
TEAM_GATE_LOW = "TEAM_GATE_LOW"                      # 403 团队报告门禁未达标
TEAM_COOLDOWN = "TEAM_COOLDOWN"                      # 409 团队 roster 变更冷却期
TEAM_VERIFY_REQUIRED = "TEAM_VERIFY_REQUIRED"        # 403 成员必须先完成 Steam 验证
TEAM_ROSTER_SIZE = "TEAM_ROSTER_SIZE"                # 403 团队 roster 需 5-7 人（5 主力 + 最多 2 替补）
TEAM_CALLER_NOT_IN_DEMO = "TEAM_CALLER_NOT_IN_DEMO"  # 403 调用者本人不在该 demo 阵容


class ApiError(Exception):
    """业务错误：status_code + 机器码 + 中文提示。"""

    def __init__(self, status_code: int, code: str, message: str, details: dict | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = {k: v for k, v in (details or {}).items() if k not in {"code", "message"}}
        super().__init__(message)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.message, **exc.details}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        # 参数校验失败统一 422 + 机器码
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": "VALIDATION_ERROR", "message": "请求参数不合法"}},
        )

    # 6R19：应用层速率限制超限 → 429 + Retry-After（与 nginx 的 SHARE_RATE_LIMITED 语义一致）。
    @app.exception_handler(RATE_LIMIT_EXCEEDED)
    async def rate_limit_handler(_: Request, exc: RATE_LIMIT_EXCEEDED):
        return JSONResponse(
            status_code=429,
            content={"detail": {"code": "RATE_LIMITED", "message": "访问过于频繁，请稍后再试"}},
            headers={"Retry-After": str(exc.retry_after)},
        )
