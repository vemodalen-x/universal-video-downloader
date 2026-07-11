Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

python -m pip install -r requirements-build.txt
python -m pip install -r requirements.txt
python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name "UniversalVideoDownloader" `
  --icon "assets\app_icon_v2.ico" `
  --version-file "assets\version_info.txt" `
  --collect-all "yt_dlp" `
  --add-data "assets\app_icon_v2.ico;assets" `
  --add-data "assets\app_brand_v2_40.png;assets" `
  --add-data "assets\app_icon_v2_64.png;assets" `
  m3u8_desktop_app.py

$distDir = Join-Path $PSScriptRoot "dist\UniversalVideoDownloader"
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.md") -Destination $distDir -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "CHANGELOG.md") -Destination $distDir -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "LICENSE") -Destination $distDir -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "THIRD_PARTY_NOTICES.md") -Destination $distDir -Force

Write-Host "Build finished: dist\UniversalVideoDownloader\UniversalVideoDownloader.exe"
