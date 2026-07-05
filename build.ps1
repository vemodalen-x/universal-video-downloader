Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

python -m pip install -r requirements-build.txt
python -m pip install -r requirements.txt
python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name "UniversalVideoDownloader" `
  --icon "assets\app_icon.ico" `
  --collect-all "yt_dlp" `
  --add-data "assets\app_icon.ico;assets" `
  --add-data "assets\app_icon_64.png;assets" `
  m3u8_desktop_app.py

Write-Host "Build finished: dist\UniversalVideoDownloader\UniversalVideoDownloader.exe"
