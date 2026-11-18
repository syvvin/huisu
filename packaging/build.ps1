param(
  [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

$version = "0.0.1"
Write-Host "回溯 $version build" -ForegroundColor Cyan
Write-Host "root: $root"

foreach ($required in @("data\dict\ecdict.db", "vocab\data\cefr.json.gz", "web\favicon.ico", "extractor\uttu\server.py")) {
  if (-not (Test-Path $required)) { throw "缺少构建资源：$required" }
}

$ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
$ffprobe = (Get-Command ffprobe -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg -or -not $ffprobe) { throw "需要 ffmpeg / ffprobe 在 PATH 中" }

if (Test-Path dist) { Remove-Item -Recurse -Force dist }
if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path release) { Remove-Item -Recurse -Force release }
New-Item -ItemType Directory -Path release | Out-Null

Write-Host "PyInstaller..." -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build "packaging\回溯.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

if (-not (Test-Path "dist\回溯\回溯.exe")) { throw "没有生成 dist\回溯\回溯.exe" }

if ($SkipInstaller) {
  Write-Host "跳过安装包（-SkipInstaller）" -ForegroundColor Yellow
  return
}

$iscc = $null
foreach ($candidate in @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)) {
  if (Test-Path $candidate) { $iscc = $candidate; break }
}
if (-not $iscc) {
  $found = Get-Command ISCC.exe -ErrorAction SilentlyContinue
  if ($found) { $iscc = $found.Source }
}
if (-not $iscc) {
  throw "未找到 Inno Setup 6，请先运行：winget install --id JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements"
}

Write-Host "Inno Setup: $iscc" -ForegroundColor Cyan
& $iscc "packaging\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup 失败" }

$installer = Get-ChildItem release -Filter "*.exe" | Select-Object -First 1
if (-not $installer) { throw "没有生成安装包" }
Write-Host ("完成：" + $installer.FullName) -ForegroundColor Green
Write-Host ("大小：" + [math]::Round($installer.Length / 1MB, 1) + " MB")
