# MapControlExporter — CS2 NavMesh v36 → 结构 JSON（只读，官方 ValveResourceFormat 库）

## 用途
用官方 **ValveResourceFormat 19.2.6339** MIT 库只读解析 CS2 地图 `de_mirage.nav`（NavMesh v36），
导出为结构 JSON（每个 NavArea 的多边形角点、areaId、邻接/连接、ladder、hull、bounds、stats、deterministicHash）。
用于 6R21E 拓扑感知地图控制模型的 NavGraph 端点。**不重写/不复刻 BinaryKV3**（由官方库完成）。

## 来源 / 许可 / 归因（按 VRF 要求）
- **库**：`ValveResourceFormat` (MIT License) — https://github.com/ValveResourceFormat/ValveResourceFormat
- **NuGet 锁定精确版本**：`19.2.6339`（`MapControlExporter.csproj` `PackageReference` + `packages.lock.json`，`RestorePackagesWithLockFile=true`）。
- 工具链：Windows x64 `.NET SDK 10.0.400`（Microsoft 官方），安装于仓库外 `D:\dotnet-sdk\10.0.400\`
  （`dotnet-sdk-10.0.400-win-x64.zip`，SHA256 `83FBF0699A984D08063579B06EDFC8C327AF62234B1AAEEADC5D7AE7F5A0514A`，体积 300546129 B；未改系统 PATH、未覆盖已有 Runtime、无需管理员）。
- **归因**：本项目使用 ValveResourceFormat（MIT）解析 Valve 游戏导航数据；仅用于个人/本地分析，不随本仓库再分发任何 Valve 游戏资产或原始 `.nav`。

## 输入 / 输出（均放仓库外）
- 输入：`<本机CS2安装>\game\csgo\maps\de_mirage.vpk` 内的 `maps/de_mirage.nav`（用 `Source2Viewer-CLI.exe` 提取，见
  `scripts/vrf_extract_mirage_nav.ps1`；哈希 SHA256 `f9b23bb5d2303549259040ddfd5ed6c075760d2dfa5ab2025e097719e2e016aa`）。
- 输出：结构 JSON（请放在仓库外）；**原始 Valve VPK/.nav 与完整派生 JSON 均不提交本仓库**。

## 用法（仓库外隔离 SDK）
```powershell
$dotnet = 'D:\dotnet-sdk\10.0.400\dotnet.exe'
$nav    = $env:CS2_NAV_PATH
$out    = $env:VRF_NAVMESH_JSON
if (-not $nav -or -not $out) { throw '请设置 CS2_NAV_PATH 与 VRF_NAVMESH_JSON（仓库外路径）' }
& $dotnet restore
& $dotnet build -c Release
& $dotnet .\bin\Release\net10.0\MapControlExporter.dll $nav $out
```

## 结构 schema（`cs2-navmesh-v1`）
```
{ schema, source:{file,map,sha256,engine,navVersion,subVersion,analyzed},
  tool:{name,version,license},
  stats:{areaCount,ladderCount,edgeCount,connectedComponents,deterministicHash},
  bounds:{minX,minY,minZ,maxX,maxY,maxZ},
  areas:[ { id, hull, corners:[[x,y,z]...],
           connections:[{corner,areaId,edgeId}...],
           adjacency:[neighborAreaId...],
           laddersAbove:[...], laddersBelow:[...] } ],
  ladders:[...] }
```
- `corners` = 该 area 的多边形角点（世界坐标, unit）；`adjacency` = 去重后的邻接 areaId 列表（图边）。
- `deterministicHash` = 对按 id 排序的 area（角点 F3 + adjacency）的 canonical 串做 SHA256 → 稳定。

## 硬校验（已通过与 VRF 官方解析一致）
- `areaCount == 2544`（VRF 权威 2544）、`ladderCount == 2`、`deterministicHash` 稳定复现。
- 实测：`areas=2544 ladders=2 edges=3344 components=23 detHash=fa5e0cbba0d90bc6ee7895cef07db3e928121644c6fbbbc38aff4f8999779d59`。
- 注意：`connectedComponents=23` 表示存在若干相互不连通的子图（CS2 navmesh 含独立/分离区域），符合真实拓扑。

## 边界
- 只读；不改输入；未部署、未删数据、未真实 Steam 登录、未用 Qwen/Ollama/退役视觉/付费外部 AI；未改系统 PATH；
  未削弱既有门禁。原始/派生数据放仓库外；本仓库只提交：exporter 源码、packages.lock.json、本 README/结构 schema/统计、
  `scripts/vrf_extract_mirage_nav.ps1` 与合成 fixtures。
