<#
Downloads a portable, no-install-required Redis server for Windows into
.\redis-portable\ next to this script's caller, if one isn't already
there. Called by Pharmacy-ERP.bat so a fresh machine with neither
redis-server nor Memurai on PATH can still be gotten running
automatically -- the person running the installer shouldn't have to
separately find, download, and install Redis themselves.

Source: https://github.com/redis-windows/redis-windows -- an actively
maintained project that builds real Redis (up to 8.x) for Windows via
GitHub Actions, unlike the long-abandoned tporadowski/redis fork stuck
on Redis 4/5. Resolves whatever the CURRENT latest release actually is
via GitHub's API rather than a hardcoded version number, so this does
not go stale as new Redis versions ship.

Exit code 0 on success (including "already present, nothing to do").
Exit code 1 on any failure, with a clear message -- the caller falls
back to printing manual Memurai instructions when this fails, it never
leaves the person with just a silent error.
#>

$ErrorActionPreference = "Stop"
$destDir = Join-Path $PSScriptRoot "..\redis-portable"
$exePath = Join-Path $destDir "redis-server.exe"

if (Test-Path $exePath) {
    Write-Host "[OK] Portable Redis already present."
    exit 0
}

try {
    Write-Host "Looking up the latest Redis-for-Windows release..."
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/redis-windows/redis-windows/releases/latest" -UseBasicParsing

    # Prefer the plain msys2 build (just the binaries) over the
    # "-with-Service" variant -- this script runs redis-server.exe
    # directly as a background process (matching how Memurai/a PATH
    # install would be used), it doesn't need or want a registered
    # Windows service.
    $asset = $release.assets |
        Where-Object { $_.name -like "*-x64-msys2.zip" -and $_.name -notlike "*Service*" } |
        Select-Object -First 1

    if (-not $asset) {
        throw "No matching Redis release asset found (expected a '*-x64-msys2.zip' file)."
    }

    Write-Host "Downloading $($asset.name) ..."
    $zipPath = Join-Path $env:TEMP "redis-portable-download.zip"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath -UseBasicParsing

    Write-Host "Extracting..."
    if (Test-Path $destDir) { Remove-Item $destDir -Recurse -Force }
    Expand-Archive -Path $zipPath -DestinationPath $destDir -Force
    Remove-Item $zipPath -Force

    # Some release zips nest everything under one subfolder
    # (Redis-X.Y.Z-Windows-x64-msys2\...) instead of putting
    # redis-server.exe at the top level -- flatten that if so.
    if (-not (Test-Path $exePath)) {
        $nested = Get-ChildItem -Path $destDir -Filter "redis-server.exe" -Recurse | Select-Object -First 1
        if ($nested) {
            Get-ChildItem -Path $nested.DirectoryName | Move-Item -Destination $destDir -Force
        }
    }

    if (-not (Test-Path $exePath)) {
        throw "Downloaded and extracted, but redis-server.exe was not found afterward."
    }

    Write-Host "[OK] Portable Redis ready at $destDir"
    exit 0
} catch {
    Write-Host "[ERROR] Automatic Redis download failed: $($_.Exception.Message)"
    exit 1
}
