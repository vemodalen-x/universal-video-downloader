param(
    [switch]$Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$hostName = "com.vemodalen.universal_video_downloader"
$extensionId = "pafgpejhjpgdagalhphhdlkfkjldepme"
$sourceBridge = Join-Path $PSScriptRoot "UniversalVideoDownloaderBridge.exe"
$sourceExtension = Join-Path $PSScriptRoot "browser-extension"
$installRoot = Join-Path $env:LOCALAPPDATA "UniversalVideoDownloader\browser-companion"
$installedBridge = Join-Path $installRoot "UniversalVideoDownloaderBridge.exe"
$installedExtension = Join-Path $installRoot "extension"
$hostManifest = Join-Path $installRoot "$hostName.json"
$registryPaths = @(
    "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$hostName",
    "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\$hostName"
)

if ($Uninstall) {
    foreach ($registryPath in $registryPaths) {
        if (Test-Path -LiteralPath $registryPath) {
            Remove-Item -LiteralPath $registryPath -Recurse -Force
        }
    }
    if (Test-Path -LiteralPath $installRoot) {
        $resolvedRoot = (Resolve-Path -LiteralPath $installRoot).Path
        $expectedRoot = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "UniversalVideoDownloader\browser-companion"))
        if ($resolvedRoot -eq $expectedRoot) {
            Remove-Item -LiteralPath $resolvedRoot -Recurse -Force
        }
    }
    Write-Host "Browser companion native host removed. Remove the unpacked extension from Chrome or Edge separately."
    exit 0
}

if (-not (Test-Path -LiteralPath $sourceBridge -PathType Leaf)) {
    throw "UniversalVideoDownloaderBridge.exe was not found next to this installer."
}
if (-not (Test-Path -LiteralPath (Join-Path $sourceExtension "manifest.json") -PathType Leaf)) {
    throw "The browser-extension directory is incomplete."
}

New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
Copy-Item -LiteralPath $sourceBridge -Destination $installedBridge -Force
if (Test-Path -LiteralPath $installedExtension) {
    Remove-Item -LiteralPath $installedExtension -Recurse -Force
}
Copy-Item -LiteralPath $sourceExtension -Destination $installedExtension -Recurse -Force

$manifest = [ordered]@{
    name = $hostName
    description = "Universal Video Downloader browser companion"
    path = $installedBridge
    type = "stdio"
    allowed_origins = @("chrome-extension://$extensionId/")
}
$manifestJson = $manifest | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText($hostManifest, $manifestJson, [System.Text.UTF8Encoding]::new($false))

foreach ($registryPath in $registryPaths) {
    New-Item -Path $registryPath -Force | Out-Null
    Set-Item -Path $registryPath -Value $hostManifest
}

Write-Host "Browser companion native host installed for Chrome and Edge."
Write-Host "Extension directory: $installedExtension"
Write-Host "Expected extension ID: $extensionId"
Write-Host "Open chrome://extensions or edge://extensions, enable Developer mode, then choose Load unpacked."
