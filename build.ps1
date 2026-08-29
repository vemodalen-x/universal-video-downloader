param(
    [switch]$SkipDependencyInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code ${LASTEXITCODE}: $Executable $($Arguments -join ' ')"
    }
}

function Get-PinnedBaiduConnector {
    param([Parameter(Mandatory = $true)][string]$Root)

    $connectorVersion = "4.0.2"
    $archiveName = "BaiduPCS-Go-v$connectorVersion-windows-x64.zip"
    $archiveSha256 = "ce72b3155a710b7c4a2b15611c3aebd11a057d7cccf0529e7703bdde04f0aa30"
    $executableSha256 = "e44769b49156fa3f094431da87231021e6874b6519ea82da4b8af0637662576d"
    $licenseSha256 = "ddadea2805326e3cb072a8b6769885fc1399475922e4c7d60f5e9f8e28c63e3d"
    $vendorDir = Join-Path $Root "build\vendor\baidupcs-go\$connectorVersion"
    $archivePath = Join-Path $vendorDir $archiveName
    $downloadPath = "$archivePath.download"
    $executablePath = Join-Path $vendorDir "$($archiveName.Replace('.zip', ''))\BaiduPCS-Go.exe"
    $licensePath = Join-Path $vendorDir "LICENSE"
    New-Item -ItemType Directory -Path $vendorDir -Force | Out-Null

    $archiveReady = (Test-Path -LiteralPath $archivePath) -and (
        (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $archiveSha256
    )
    if (-not $archiveReady) {
        Invoke-WebRequest -UseBasicParsing `
            -Uri "https://github.com/qjfoidnh/BaiduPCS-Go/releases/download/v$connectorVersion/$archiveName" `
            -OutFile $downloadPath
        $downloadHash = (Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($downloadHash -ne $archiveSha256) {
            throw "BaiduPCS-Go archive checksum mismatch"
        }
        Move-Item -LiteralPath $downloadPath -Destination $archivePath -Force
    }

    if (-not (Test-Path -LiteralPath $executablePath) -or (
        (Get-FileHash -LiteralPath $executablePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $executableSha256
    )) {
        Expand-Archive -LiteralPath $archivePath -DestinationPath $vendorDir -Force
    }
    if ((Get-FileHash -LiteralPath $executablePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $executableSha256) {
        throw "BaiduPCS-Go executable checksum mismatch"
    }

    $licenseReady = (Test-Path -LiteralPath $licensePath) -and (
        (Get-FileHash -LiteralPath $licensePath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $licenseSha256
    )
    if (-not $licenseReady) {
        Invoke-WebRequest -UseBasicParsing `
            -Uri "https://raw.githubusercontent.com/qjfoidnh/BaiduPCS-Go/v$connectorVersion/LICENSE" `
            -OutFile $licensePath
    }
    if ((Get-FileHash -LiteralPath $licensePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $licenseSha256) {
        throw "BaiduPCS-Go license checksum mismatch"
    }
    Unblock-File -LiteralPath $executablePath -ErrorAction SilentlyContinue
    return @{ Executable = $executablePath; License = $licensePath }
}

$root = $PSScriptRoot
$versionInfoPath = Join-Path $root "assets\version_info.txt"
$versionInfo = Get-Content -LiteralPath $versionInfoPath -Raw -Encoding UTF8
$versionMatch = [regex]::Match($versionInfo, "StringStruct\(u'ProductVersion',\s*u'(?<version>\d+\.\d+\.\d+)'\)")
if (-not $versionMatch.Success) {
    throw "Unable to read ProductVersion from assets\version_info.txt"
}
$version = $versionMatch.Groups["version"].Value
$bridgeVersionInfo = Get-Content -LiteralPath (Join-Path $root "assets\bridge_version_info.txt") -Raw -Encoding UTF8
if ($bridgeVersionInfo -notmatch "StringStruct\(u'ProductVersion',\s*u'$([regex]::Escape($version))'\)") {
    throw "Browser bridge ProductVersion does not match the desktop application"
}
$tag = "v$version"
$previousPythonUtf8 = $env:PYTHONUTF8
$previousPythonIoEncoding = $env:PYTHONIOENCODING

Push-Location $root
try {
    $baiduConnector = Get-PinnedBaiduConnector -Root $root
    if ($SkipDependencyInstall) {
        $releasePython = (Get-Command python -ErrorAction Stop).Source
    }
    else {
        $venvDir = Join-Path $root "build\release-venv"
        & python -m venv --clear $venvDir
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to create isolated release environment"
        }
        $releasePython = Join-Path $venvDir "Scripts\python.exe"
        Invoke-Python -Executable $releasePython -Arguments @(
            "-m", "pip", "install", "--require-hashes", "--only-binary=:all:", "-r", "requirements-release.txt"
        )
    }

    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    Invoke-Python -Executable $releasePython -Arguments @("-m", "PyInstaller", "--noconfirm", "--clean", "UniversalVideoDownloader.spec")
    Invoke-Python -Executable $releasePython -Arguments @("-m", "PyInstaller", "--noconfirm", "--clean", "UniversalVideoDownloaderBridge.spec")

    $distDir = Join-Path $root "dist\UniversalVideoDownloader"
    $bridgePath = Join-Path $root "dist\UniversalVideoDownloaderBridge.exe"
    if (-not (Test-Path -LiteralPath (Join-Path $distDir "UniversalVideoDownloader.exe"))) {
        throw "Desktop executable was not created"
    }
    if (-not (Test-Path -LiteralPath $bridgePath)) {
        throw "Browser bridge executable was not created"
    }

    Copy-Item -LiteralPath $bridgePath -Destination $distDir -Force
    Copy-Item -LiteralPath $baiduConnector.Executable -Destination (Join-Path $distDir "BaiduPCS-Go.exe") -Force
    Copy-Item -LiteralPath $baiduConnector.License -Destination (Join-Path $distDir "BaiduPCS-Go-LICENSE.txt") -Force
    Copy-Item -LiteralPath (Join-Path $root "browser_extension") -Destination (Join-Path $distDir "browser-extension") -Recurse -Force
    foreach ($file in @(
        "install_browser_companion.ps1",
        "README.md",
        "RELEASE_NOTES.md",
        "CHANGELOG.md",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md"
    )) {
        Copy-Item -LiteralPath (Join-Path $root $file) -Destination $distDir -Force
    }

    $archiveName = "UniversalVideoDownloader-$tag-windows-x64.zip"
    $archivePath = Join-Path (Join-Path $root "dist") $archiveName
    Compress-Archive -Path (Join-Path $distDir "*") -DestinationPath $archivePath -CompressionLevel Optimal -Force

    $digest = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $checksumPath = Join-Path (Join-Path $root "dist") "SHA256SUMS-$tag.txt"
    $checksumLine = "$digest  $archiveName`n"
    [System.IO.File]::WriteAllText($checksumPath, $checksumLine, [System.Text.UTF8Encoding]::new($false))

    Invoke-Python -Executable $releasePython -Arguments @(
        "tools\verify_release_package.py",
        "--zip", $archivePath,
        "--source-root", $root
    )

    Write-Host "Build finished: dist\UniversalVideoDownloader\UniversalVideoDownloader.exe"
    Write-Host "Release archive: dist\$archiveName"
    Write-Host "SHA-256: $digest"
}
finally {
    $env:PYTHONUTF8 = $previousPythonUtf8
    $env:PYTHONIOENCODING = $previousPythonIoEncoding
    Pop-Location
}
