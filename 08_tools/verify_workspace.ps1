[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$pairs = @(
    @{
        Archive = "archive\frozen_inference_source.zip"
        Checksum = "archive\frozen_inference_source.zip.sha256"
    },
    @{
        Archive = "03_protocol_and_data\final_holdout_release\mp_fs_plus_external_holdout_300_20260731.zip"
        Checksum = "03_protocol_and_data\final_holdout_release\mp_fs_plus_external_holdout_300_20260731.zip.sha256"
    },
    @{
        Archive = "03_protocol_and_data\calibration_evidence\mp_fs_plus_calibration60_20260729T141353Z.tar.gz"
        Checksum = "03_protocol_and_data\calibration_evidence\mp_fs_plus_calibration60_20260729T141353Z.tar.gz.sha256"
    }
)

$failures = @()
foreach ($pair in $pairs) {
    $archive = Join-Path $workspace $pair.Archive
    $checksum = Join-Path $workspace $pair.Checksum
    $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower()
    $expected = ((Get-Content -LiteralPath $checksum -Raw) -split "\s+")[0].ToLower()
    $match = $actual -eq $expected
    [pscustomobject]@{
        Artifact = $pair.Archive
        Match = $match
        SHA256 = $actual
    }
    if (-not $match) {
        $failures += $pair.Archive
    }
}

if ($failures.Count) {
    throw "Workspace verification failed: $($failures -join ', ')"
}
Write-Host "WORKSPACE VERIFICATION: PASS" -ForegroundColor Green
