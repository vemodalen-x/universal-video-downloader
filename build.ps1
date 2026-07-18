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
