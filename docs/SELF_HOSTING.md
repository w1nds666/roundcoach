# Self-hosting checklist

这是最低限度清单，不替代安全审计：

- 使用随机且互不复用的 PostgreSQL、MinIO 和 JWT 密钥
- 保持 `AI_PROVIDER=fake`、`ALLOW_EXTERNAL_LLM=false`，直到完成数据外发评估
- 只通过 HTTPS 暴露服务，并限制管理端口与对象存储端口
- 配置数据库、对象存储和 `.env` 的加密备份及恢复演练
- 对上传大小、队列并发、容器内存和磁盘空间设置硬限制
- 启用日志脱敏，不记录 Steam OpenID 回调参数、JWT 或分享 token
- 明确数据保留与删除策略，并在邀请测试前告知测试者
- 上线前完成当地要求的域名、备案、隐私政策和用户协议流程

