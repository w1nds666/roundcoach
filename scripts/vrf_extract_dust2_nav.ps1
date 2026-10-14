# 6R21F-E6E 可复现提取脚本：用官方 MIT ValveResourceFormat 19.2 从本机 CS2 地图 VPK 只读提取 de_dust2.nav
# 工具在仓库外隔离目录（不提交二进制、不改 PATH）；输出到仓库外；未提交原始 Valve VPK/.nav。
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts/vrf_extract_dust2_nav.ps1
# 依赖：
#   - 通过 VRF_CLI 指向 Source2Viewer-CLI.exe
#     （下载：https://github.com/ValveResourceFormat/ValveResourceFormat/releases/download/19.2/cli-windows-x64.zip
#       SHA256 53E7E8DAC1DDD876078346DE709C8DBE613A967E94CD0C969AA34C61EC07680D）
#   - 通过 CS2_DUST2_VPK 指向你本机合法安装中的 de_dust2.vpk
#   - 通过 VRF_OUT 指定仓库外输出目录

$ErrorActionPreference = 'Stop'
$VrfExe = $env:VRF_CLI
$Vpk = $env:CS2_DUST2_VPK
$OutDir = $env:VRF_OUT
if (-not $VrfExe) { throw '请设置 VRF_CLI，指向 Source2Viewer-CLI.exe' }
if (-not $Vpk) { throw '请设置 CS2_DUST2_VPK，指向 de_dust2.vpk' }
if (-not $OutDir) { throw '请设置 VRF_OUT，且使用仓库外目录' }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (-not (Test-Path $VrfExe)) { throw "缺少 VRF CLI: $VrfExe" }
if (-not (Test-Path $Vpk))    { throw "缺少地图 VPK: $Vpk" }

Write-Output "== VRF version =="
& $VrfExe --version | Select-Object -First 1

Write-Output "== vpk_list: 找 nav =="
& $VrfExe -i "$Vpk" --vpk_list | Select-String -Pattern 'maps/de_dust2.nav' -CaseSensitive:$false

Write-Output "== 提取 maps/de_dust2.nav =="
& $VrfExe -i "$Vpk" -o "$OutDir" --vpk_decompile --vpk_filepath "maps/de_dust2.nav"

$Nav = Join-Path $OutDir 'maps\de_dust2.nav'
if (-not (Test-Path $Nav)) { throw "提取失败: $Nav" }
Write-Output ("extracted len=" + (Get-Item $Nav).Length)
Write-Output ("sha256=" + (Get-FileHash -Algorithm SHA256 -Path $Nav).Hash)
Write-Output "== 服务端解析摘要（权威 v36 元数据）=="
& $VrfExe -i "$Nav" | Select-Object -First 12
