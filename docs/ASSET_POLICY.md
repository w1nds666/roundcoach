# 地图与第三方资产政策

## 公开仓库不包含的内容

- Counter-Strike 2 的 VPK、`.nav`、雷达图和武器图标
- 从上述文件导出的完整 NavMesh 或区域几何产物
- 原始比赛 Demo、真实账号数据、生产数据库与对象存储
- 内部性能证据、浏览器截图和包含访问凭证的运行记录

这些文件在公开快照中被有意移除。MIT License 只覆盖本项目原创源码，不授予任何 Valve、Steam 或其他第三方内容的权利。

## 本地提取

`tools/vrf-navmesh-export/` 使用 MIT 许可的 ValveResourceFormat 库读取本机合法安装中的导航数据。使用者需自行确认游戏许可、当地法律和第三方项目许可证。

建议生成目录：

```text
backend/config/radars/
backend/config/navmesh/
backend/config/regions/
```

这些目录已被根 `.gitignore` 忽略。不要把生成物提交到 Pull Request。

## Demo 隐私

即便 Demo 可由游戏客户端生成，也可能包含可关联到个人的昵称、SteamID、比赛行为及聊天信息。公开前应进行最小化、匿名化并取得适当授权。

