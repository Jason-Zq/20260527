# =============================================================================
# 打包后端发布包（zip）
#
# 用法：右键 → 用 PowerShell 运行
#   或在 PowerShell 中：cd 项目根; .\deploy\pack-backend.ps1
#
# 输出：项目根\release\doc-audit-YYMMDD-HHMM.zip
#
# 包含：
#   backend/db/             ORM + CRUD（不含 __pycache__）
#   backend/*.py            后端源码
#   backend/requirements.txt
#   migrations/             数据库迁移脚本
#   requirements.txt        Python 依赖清单
#   alembic.ini             alembic 配置
#   config.json.example     配置模板
#   python部署 .txt          部署文档
#
# 不包含（敏感/大体积/无意义）：
#   .venv312/               虚拟环境（跨机器不兼容，必须在服务器上重建）
#   .git/                   git 历史
#   config.json             含真实 API Key 与 DB 密码
#   output/ temp/           运行时数据
#   __pycache__/            Python 缓存
#   frontend/               前端（本次仅部署后端）
#   tests/fixtures/         真实证件 PDF
# =============================================================================

$ErrorActionPreference = 'Stop'

# 切到项目根（脚本所在目录的上一级）
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# 时间戳
$stamp = Get-Date -Format 'yyMMdd-HHmm'
$releaseDir = Join-Path $projectRoot 'release'
$zipPath    = Join-Path $releaseDir "doc-audit-$stamp.zip"

if (-not (Test-Path $releaseDir)) {
    New-Item -ItemType Directory -Path $releaseDir | Out-Null
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ' 打包后端发布包' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host " 项目根: $projectRoot"
Write-Host " 输出:   $zipPath"
Write-Host ''

# 收集要打包的源文件（排除 __pycache__、.pyc）
$tempStaging = Join-Path $env:TEMP "doc-audit-pack-$stamp"
if (Test-Path $tempStaging) { Remove-Item -Recurse -Force $tempStaging }
New-Item -ItemType Directory -Path $tempStaging | Out-Null

# 1) backend/ 全部 .py（不含 __pycache__）
Write-Host '  [1/6] 收集 backend/ 源码 ...' -ForegroundColor Yellow
$backendDst = Join-Path $tempStaging 'backend'
New-Item -ItemType Directory -Path $backendDst | Out-Null

# backend 根目录的 .py 与 requirements.txt
Get-ChildItem -Path 'backend' -File | Where-Object {
    $_.Extension -in '.py', '.txt'
} | ForEach-Object {
    Copy-Item $_.FullName $backendDst
}

# backend/db/ 子目录（仅 .py）
$dbDst = Join-Path $backendDst 'db'
New-Item -ItemType Directory -Path $dbDst | Out-Null
Get-ChildItem -Path 'backend\db' -File -Filter '*.py' | ForEach-Object {
    Copy-Item $_.FullName $dbDst
}

# 2) migrations/
Write-Host '  [2/6] 收集 migrations/ ...' -ForegroundColor Yellow
$migDst = Join-Path $tempStaging 'migrations'
New-Item -ItemType Directory -Path $migDst | Out-Null
Copy-Item 'migrations\env.py' $migDst
Copy-Item 'migrations\script.py.mako' $migDst
$verDst = Join-Path $migDst 'versions'
New-Item -ItemType Directory -Path $verDst | Out-Null
Get-ChildItem -Path 'migrations\versions' -File -Filter '*.py' | ForEach-Object {
    Copy-Item $_.FullName $verDst
}

# 3) 项目根的配置和文档文件
Write-Host '  [3/6] 收集根目录配置 ...' -ForegroundColor Yellow
foreach ($f in @('requirements.txt', 'alembic.ini', 'config.json.example', 'python部署 .txt')) {
    if (Test-Path $f) {
        Copy-Item $f $tempStaging
    } else {
        Write-Host "    ⚠ $f 不存在，跳过" -ForegroundColor Yellow
    }
}

# 4) 列出即将打包的内容
Write-Host '  [4/6] 即将打包的文件清单 ...' -ForegroundColor Yellow
$fileList = Get-ChildItem $tempStaging -Recurse -File
Write-Host "       共 $($fileList.Count) 个文件"

# 5) 压缩成 zip（删除已有的同名 zip）
Write-Host '  [5/6] 压缩为 zip ...' -ForegroundColor Yellow
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path "$tempStaging\*" -DestinationPath $zipPath -CompressionLevel Optimal

# 6) 清理临时目录
Write-Host '  [6/6] 清理临时目录 ...' -ForegroundColor Yellow
Remove-Item -Recurse -Force $tempStaging

# 显示结果
$zipSize = (Get-Item $zipPath).Length
$zipSizeKB = [math]::Round($zipSize / 1KB, 1)
Write-Host ''
Write-Host '============================================================' -ForegroundColor Green
Write-Host ' ✓ 打包完成' -ForegroundColor Green
Write-Host '============================================================' -ForegroundColor Green
Write-Host " 输出: $zipPath"
Write-Host " 大小: $zipSizeKB KB"
Write-Host ''
Write-Host ' 下一步:' -ForegroundColor Cyan
Write-Host '   1. 把 zip 上传到服务器（远程桌面拖拽 / FTP / 网盘等）'
Write-Host '   2. 在服务器解压到 C:\apps\doc-audit'
Write-Host '   3. 按 python部署 .txt 文档从步骤 5 开始执行'
Write-Host ''
