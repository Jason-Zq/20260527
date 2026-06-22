<#
.SYNOPSIS
  archive_detect 一键部署脚本（Windows 服务器）。

.DESCRIPTION
  覆盖以下步骤：
    1. 环境预检（Python / npm / nssm / config.json）
    2. 创建 / 复用 .venv 并 pip install
    3. 前端 npm install + npm run build
    4. 用 nssm 把后端 uvicorn 注册为 Windows 服务（开机自启）
    5. 写 nginx.conf + nginx -t 验证 + 注册 nginx 服务
    6. 添加防火墙入站规则（HTTP 端口）
    7. 健康检查 + 给出访问入口

.PREREQUISITES
  - 已安装 Python 3.12 (in PATH)
  - 已安装 Node.js 18+ LTS (in PATH)，除非使用 -SkipFrontendBuild
  - 已下载 nssm.exe 放到 PATH（推荐 C:\Windows\System32）
  - 已解压 nginx 到 C:\nginx 或自行用 -NginxRoot 指定，除非 -SkipNginx
  - 已根据 config.example.json 创建 config.json 并填入真实 LLM api_key

.PARAMETER AppRoot
  archive_detect 项目根目录。默认 = 脚本所在目录。

.PARAMETER NginxRoot
  nginx 安装目录，默认 C:\nginx。

.PARAMETER BackendPort
  uvicorn 监听端口，默认 8765。

.PARAMETER HttpPort
  nginx 对外端口，默认 80。

.PARAMETER SkipFrontendBuild
  跳过 npm install / build（如果开发机上已 build 好直接传 dist 上来）。

.PARAMETER SkipNginx
  跳过 nginx 配置（如果你用其他反代）。

.PARAMETER SkipFirewall
  跳过防火墙规则（如果服务器有别的方式管理入站）。

.EXAMPLE
  以管理员身份打开 PowerShell：
    cd C:\apps\archive_detect
    .\deploy.ps1

.EXAMPLE
  自定义端口：
    .\deploy.ps1 -BackendPort 9000 -HttpPort 8080
#>

#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string]$AppRoot = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [string]$NginxRoot = "C:\nginx",
    [int]$BackendPort = 8765,
    [int]$HttpPort = 80,
    [switch]$SkipFrontendBuild,
    [switch]$SkipNginx,
    [switch]$SkipFirewall
)

$ErrorActionPreference = 'Stop'

# ========== 输出辅助 ==========
function Write-Step($msg) { Write-Host "`n>>> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
function Die($msg) {
    Write-Host "  ✗ $msg" -ForegroundColor Red
    exit 1
}
function Need-Cmd($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Die "找不到 $name；$hint"
    }
}

# ========== 0/7 预检 ==========
Write-Step "0/7 环境预检 (AppRoot=$AppRoot)"

if (-not (Test-Path (Join-Path $AppRoot "backend\main.py"))) {
    Die "未在 $AppRoot 下找到 backend\main.py，请把 deploy.ps1 放在 archive_detect 根目录"
}

Need-Cmd "python" "请先安装 Python 3.12 (https://www.python.org)"
$pyVer = (& python --version 2>&1)
Write-Ok "Python: $pyVer"

if (-not $SkipFrontendBuild) {
    Need-Cmd "npm" "请安装 Node.js 18+ LTS；如已在开发机 build 好，加 -SkipFrontendBuild"
}

Need-Cmd "nssm" "请下载 nssm.exe (https://nssm.cc) 放到 C:\Windows\System32"

# config.json 必须存在且 api_key 不是占位符
$cfgPath = Join-Path $AppRoot "config.json"
if (-not (Test-Path $cfgPath)) {
    Die "$cfgPath 不存在；请 'copy config.example.json config.json' 并填入真实 LLM api_key"
}
try {
    $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    Die "config.json 不是合法 JSON: $($_.Exception.Message)"
}
$apiKey = $cfg.llm.api_key
if (-not $apiKey -or $apiKey -like '*替换*' -or $apiKey -like '*xxxxx*' -or $apiKey -like '把这里*') {
    Die "config.json 里的 llm.api_key 还是占位符，没填真实值"
}
Write-Ok "config.json 检查通过"

# 路径准备
$venvDir    = Join-Path $AppRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$logsDir    = Join-Path $AppRoot "logs"
$reqFile    = Join-Path $AppRoot "backend\requirements.txt"
$frontDir   = Join-Path $AppRoot "frontend"
$distDir    = Join-Path $frontDir "dist"
$backendDir = Join-Path $AppRoot "backend"

# ========== 1/7 venv + 依赖 ==========
Write-Step "1/7 Python 虚拟环境 + 依赖"
if (-not (Test-Path $venvPython)) {
    & python -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { Die "venv 创建失败" }
    Write-Ok "已创建 $venvDir"
} else {
    Write-Ok "$venvDir 已存在，复用"
}

Write-Host "  ... 升级 pip"
& $venvPython -m pip install --upgrade pip --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Die "pip 升级失败" }

# 优先使用 offline_wheels 目录（离线安装，秒级），否则走在线 PyPI
$wheelsDir = Join-Path $AppRoot "offline_wheels"
if ((Test-Path $wheelsDir) -and (Get-ChildItem $wheelsDir -Filter '*.whl' -ErrorAction SilentlyContinue)) {
    Write-Host "  ... 安装 requirements (从本地 offline_wheels/ 离线安装)"
    & $venvPython -m pip install --no-index --find-links $wheelsDir -r $reqFile --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "离线安装失败，尝试在线安装"
        & $venvPython -m pip install -r $reqFile --disable-pip-version-check
        if ($LASTEXITCODE -ne 0) { Die "pip install 失败" }
    }
} else {
    Write-Host "  ... 安装 requirements (在线，paddleocr+paddlepaddle 比较慢)"
    & $venvPython -m pip install -r $reqFile --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { Die "pip install 失败" }
}
Write-Ok "Python 依赖装好"

# ========== 2/7 前端 build ==========
if (-not $SkipFrontendBuild) {
    Write-Step "2/7 前端 build"
    Push-Location $frontDir
    try {
        if (-not (Test-Path "node_modules")) {
            Write-Host "  ... npm install"
            & npm install --silent
            if ($LASTEXITCODE -ne 0) { Die "npm install 失败" }
        }
        Write-Host "  ... npm run build"
        & npm run build
        if ($LASTEXITCODE -ne 0) { Die "npm run build 失败" }
        if (-not (Test-Path "$distDir\index.html")) { Die "build 后未找到 dist\index.html" }
        Write-Ok "frontend\dist 生成完毕"
    } finally {
        Pop-Location
    }
} else {
    Write-Warn "跳过前端 build (-SkipFrontendBuild)"
    if (-not (Test-Path "$distDir\index.html")) {
        Write-Warn "$distDir\index.html 不存在；nginx 起来后页面会 404"
    }
}

# ========== 3/7 日志目录 ==========
Write-Step "3/7 日志目录"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
Write-Ok "$logsDir"

# ========== 4/7 nssm 注册后端服务 ==========
Write-Step "4/7 注册 ArchiveDetect 服务 (uvicorn)"
$svcName = "ArchiveDetect"
& nssm status $svcName 2>$null | Out-Null
$svcExists = ($LASTEXITCODE -eq 0)

if ($svcExists) {
    Write-Host "  ... 停止现有服务以便更新配置"
    & nssm stop $svcName 2>$null | Out-Null
} else {
    & nssm install $svcName $venvPython | Out-Null
    if ($LASTEXITCODE -ne 0) { Die "nssm install 失败" }
}

$uviArgs = "-m uvicorn main:app --host 0.0.0.0 --port $BackendPort"
& nssm set $svcName Application      $venvPython | Out-Null
& nssm set $svcName AppParameters    $uviArgs    | Out-Null
& nssm set $svcName AppDirectory     $backendDir | Out-Null
& nssm set $svcName DisplayName      "文件留底检测后端 (archive-detect)" | Out-Null
& nssm set $svcName Description      "FastAPI archive-detect service on port $BackendPort" | Out-Null
& nssm set $svcName Start            SERVICE_AUTO_START | Out-Null
& nssm set $svcName AppStdout        (Join-Path $logsDir "stdout.log") | Out-Null
& nssm set $svcName AppStderr        (Join-Path $logsDir "stderr.log") | Out-Null
& nssm set $svcName AppRotateFiles   1 | Out-Null
& nssm set $svcName AppRotateBytes   10485760 | Out-Null
# 中文环境必须设，否则 print(任务id) 因 GBK 崩溃
& nssm set $svcName AppEnvironmentExtra "PYTHONIOENCODING=utf-8" "PYTHONUTF8=1" | Out-Null

& nssm start $svcName | Out-Null
Write-Host "  ... 等服务启动 (5s)"
Start-Sleep -Seconds 5

# 健康检查
$health = $null
$tries = 0
while ($tries -lt 6 -and -not $health) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/health" -TimeoutSec 3
    } catch {
        Start-Sleep -Seconds 2
        $tries++
    }
}
if (-not $health -or $health.status -ne "ok") {
    Write-Host "  /api/health 不可达，最近 30 行 stderr：" -ForegroundColor Red
    Get-Content (Join-Path $logsDir "stderr.log") -Tail 30 -ErrorAction SilentlyContinue
    Die "服务启动失败，请检查 $logsDir\stderr.log"
}
Write-Ok "ArchiveDetect 运行中 (port=$BackendPort, /api/health=ok)"

# ========== 5/7 nginx ==========
if (-not $SkipNginx) {
    Write-Step "5/7 配置 nginx"
    $nginxExe = Join-Path $NginxRoot "nginx.exe"
    $nginxConf = Join-Path $NginxRoot "conf\nginx.conf"

    if (-not (Test-Path $nginxExe)) {
        Write-Warn "未找到 $nginxExe；跳过 nginx 配置"
        Write-Warn "请下载 nginx (https://nginx.org/en/download.html) 解压到 $NginxRoot 后重跑本脚本"
    } else {
        # 把 dist 路径转成 nginx 接受的正斜杠
        $nginxDist = ($distDir -replace '\\', '/')

        # 这里用 single-quoted here-string，避免 PowerShell 把 $uri 解释成自己的变量
        # 用 __NGINX_xxx__ 占位符再回填，避开 PowerShell 解析
        $template = @'
worker_processes  1;

events {
    worker_connections  1024;
}

http {
    include       mime.types;
    default_type  application/octet-stream;
    sendfile        on;
    keepalive_timeout  65;

    gzip on;
    gzip_types text/plain text/css application/javascript application/json image/svg+xml;
    gzip_min_length 1024;

    server {
        listen       __HTTP_PORT__;
        server_name  localhost;

        # 单文件上限 50MB × 最多 20 个 = 1000MB，留余量给 multipart 边界
        client_max_body_size 1100M;

        location / {
            root   __DIST_PATH__;
            index  index.html;
            try_files $uri $uri/ /index.html;
        }

        location /api/ {
            proxy_pass http://127.0.0.1:__BACKEND_PORT__;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

            # OCR + LLM 单文件最长 200s，留 5min 余量
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
        }
    }
}
'@
        $confText = $template `
            -replace '__HTTP_PORT__', $HttpPort `
            -replace '__BACKEND_PORT__', $BackendPort `
            -replace '__DIST_PATH__', $nginxDist

        # 备份原配置（如果存在）
        if (Test-Path $nginxConf) {
            $backup = "$nginxConf.bak.$(Get-Date -Format 'yyyyMMddHHmmss')"
            Copy-Item $nginxConf $backup
            Write-Ok "原 nginx.conf 备份到 $backup"
        }
        Set-Content -Path $nginxConf -Value $confText -Encoding UTF8
        Write-Ok "已写 $nginxConf"

        # nginx -t 验证（向 stderr 输出）
        Push-Location $NginxRoot
        try {
            $testOut = & .\nginx.exe -t 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Host $testOut -ForegroundColor Red
                Die "nginx -t 测试失败；如要回滚，把 $backup 改回 nginx.conf"
            }
            Write-Ok "nginx -t 通过"
        } finally {
            Pop-Location
        }

        # 注册 nginx 服务
        $nginxSvc = "NginxArchive"
        & nssm status $nginxSvc 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            & nssm restart $nginxSvc | Out-Null
            Write-Ok "$nginxSvc 已重启（应用新配置）"
        } else {
            & nssm install $nginxSvc $nginxExe | Out-Null
            & nssm set $nginxSvc AppDirectory $NginxRoot | Out-Null
            & nssm set $nginxSvc DisplayName "Nginx (archive-detect)" | Out-Null
            & nssm set $nginxSvc Description "Reverse proxy + static for archive-detect" | Out-Null
            & nssm set $nginxSvc Start SERVICE_AUTO_START | Out-Null
            & nssm start $nginxSvc | Out-Null
            Write-Ok "$nginxSvc 已注册并启动"
        }
    }
} else {
    Write-Warn "跳过 nginx (-SkipNginx)"
}

# ========== 6/7 防火墙 ==========
if (-not $SkipFirewall) {
    Write-Step "6/7 防火墙规则"
    $ruleName = "Archive Detect HTTP ($HttpPort)"
    $rule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if ($rule) {
        Write-Ok "防火墙规则已存在: $ruleName"
    } else {
        New-NetFirewallRule -DisplayName $ruleName `
            -Direction Inbound -Protocol TCP `
            -LocalPort $HttpPort -Action Allow | Out-Null
        Write-Ok "已添加 inbound TCP $HttpPort"
    }
} else {
    Write-Warn "跳过防火墙 (-SkipFirewall)"
}

# ========== 7/7 收尾 ==========
Write-Step "7/7 部署完成"
Write-Host ""
Write-Host "  访问地址:" -ForegroundColor Green
Write-Host "    http://<服务器IP>/      (端口 $HttpPort，nginx 转发)"
Write-Host "    http://127.0.0.1:$BackendPort/api/health   (后端直连)"
Write-Host ""
Write-Host "  关键路径:"
Write-Host "    配置:  $cfgPath"
Write-Host "    日志:  $logsDir\stdout.log / stderr.log"
Write-Host "    venv:  $venvDir"
Write-Host "    dist:  $distDir"
Write-Host ""
Write-Host "  日常运维:" -ForegroundColor Yellow
Write-Host "    nssm restart ArchiveDetect      # 改后端 .py 后用"
Write-Host "    nssm restart NginxArchive       # 改前端 dist 不用，改 nginx.conf 后用"
Write-Host "    nssm stop  ArchiveDetect"
Write-Host "    nssm start ArchiveDetect"
Write-Host "    Get-Content $logsDir\stdout.log -Tail 50 -Wait"
Write-Host ""
Write-Host "  安全提醒:" -ForegroundColor Yellow
Write-Host "    config.json 含 LLM API Key，建议执行:"
Write-Host "      icacls `"$cfgPath`" /inheritance:r /grant:r `"SYSTEM:F`" `"Administrators:F`""
Write-Host ""
