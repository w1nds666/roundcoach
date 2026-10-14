# RoundCoach

RoundCoach 是一个面向 Counter-Strike 2 Demo 的开源赛后复盘与训练辅助平台。它把击杀、伤害、道具、路线、区域控制与团队协同放回同一条时间轴，帮助玩家从“发生了什么”追到“下一场怎么改”。

> 当前仓库是可自托管的社区源码快照，并不是已经运营的商业服务。项目不隶属于 Valve、Steam 或 Counter-Strike。

## 能做什么

- 上传并异步解析 `.dem` 文件
- 比赛概览、玩家统计、击杀与路线热力图
- 个人/团队战术回放、死亡标记与区域控制时间轴
- Steam OpenID 身份绑定、团队 roster、只读分享与权限隔离
- 10/30 场长期教练框架和可追溯证据
- 可选 AI 报告；默认使用本地 fake provider，不会把数据发给外部模型
- 桌面端与移动端响应式界面

## 技术栈

- FastAPI、SQLAlchemy、Alembic、Celery
- PostgreSQL、Redis、MinIO
- Vue 3、Vite、Naive UI、Canvas/SVG
- Docker Compose、Nginx

## 快速开始

要求：Docker Engine + Docker Compose v2。

```bash
git clone https://github.com/w1nds666/roundcoach.git
cd roundcoach
cp .env.example .env
```

把 `.env` 中所有 `REPLACE_...` 占位符换成你自己的随机值，然后启动：

```bash
docker compose up --build
```

默认入口：

- Web：`http://localhost`
- API 健康检查：`http://localhost/health`

生产环境必须另行配置 HTTPS、备份、访问控制、监控和符合当地法律的上线手续。不要把 `.env`、数据库、对象存储、原始 Demo 或访问令牌提交到 Git。

## 地图资产说明

这个公开仓库**不包含** Valve/CS2 的雷达图、VPK、`.nav` 或完整 NavMesh/区域派生数据。源码会在缺少这些文件时对相应能力降级或返回“地图未接入”。

要启用完整地图可视化，请从你合法拥有的本机 CS2 安装中自行提取资产，放入：

```text
backend/config/radars/<map>.png
backend/config/navmesh/<map>.json
backend/config/regions/<map>.json
```

仓库提供了 `tools/vrf-navmesh-export/` 和 `scripts/vrf_extract_*` 作为只读提取参考。详细边界见 [地图与第三方资产政策](docs/ASSET_POLICY.md)。

## 本地开发

后端：

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

前端：

```bash
cd frontend
npm ci
npm run dev
```

## 安全与隐私

- Demo 可能包含玩家昵称、Steam 标识、语音/聊天衍生信息和比赛行为数据；上传前请确认你有权处理。
- 分享链接属于访问凭证，应像密码一样保管并及时撤销。
- 真实 Steam 登录、真实付费 AI 与生产部署必须自行审计。
- 发现漏洞请阅读 [SECURITY.md](SECURITY.md)，不要在公开 Issue 中张贴密钥或个人数据。

## 项目状态

本仓库基于内部冻结提交 `fa25cae641448fc5d30f54ed83f6614593a0c369` 整理为无历史、无秘密、无游戏资产的公开快照。内部验收记录、性能原始 JSON、浏览器截图和服务器运行数据未公开。

## 参与贡献

参见 [CONTRIBUTING.md](CONTRIBUTING.md)。提交代码即表示你有权贡献该内容，并同意按本仓库许可证发布。

## 许可证

原创程序代码以 [MIT License](LICENSE) 发布。Counter-Strike、Steam、地图、雷达图、游戏数据及其他第三方内容不在该许可证授权范围内，其权利归各自权利人所有。

