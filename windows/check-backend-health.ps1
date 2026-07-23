# Checks whether a healthy Pharmacy ERP backend is already answering on
# port 8000. Prints exactly "yes" or "no" (nothing else) and exits 0
# either way, so the calling .bat file can read the single line of
# output without needing to parse anything more fragile than that.
#
# A leftover backend from an earlier run/test session sitting on this
# port is the single most common reason start-backend.bat used to
# crash with a raw, unreadable bind error -- confirmed by an actual bug
# report showing exactly that. This is what lets it check first and
# just say so, instead of trying and failing.

$ErrorActionPreference = 'Stop'

try {
    $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2
    if ($response.status -eq 'ok') {
        Write-Output 'yes'
    } else {
        Write-Output 'no'
    }
} catch {
    Write-Output 'no'
}
