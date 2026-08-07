[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = "D:\paper kltn\text to sql"
$paperWorkspace = Join-Path $root "paper_ready_workspace_20260731"
$archiveRoot = Join-Path $root "99_archive_history_20260731"
$manifestPath = Join-Path $paperWorkspace `
    "07_reproducibility\CLEANUP_MANIFEST_20260731.tsv"
$summaryPath = Join-Path $paperWorkspace `
    "07_reproducibility\CLEANUP_SUMMARY_20260731.json"

if (-not (Test-Path -LiteralPath $paperWorkspace -PathType Container)) {
    throw "Missing canonical paper workspace: $paperWorkspace"
}
if (Test-Path -LiteralPath $archiveRoot) {
    throw "Archive target already exists: $archiveRoot"
}

& (Join-Path $PSScriptRoot "verify_workspace.ps1")

$moves = [ordered]@{
    "final_holdout_authoring_20260729" =
        "audit_and_results\final_holdout_authoring_20260729"
    "review_intake" =
        "audit_and_results\review_intake"
    "server_results" =
        "audit_and_results\server_results"
    "server_downloads" =
        "legacy_sources_and_results\server_downloads"
    "paper_sources" =
        "legacy_sources_and_results\paper_sources"
    "outputs" =
        "legacy_sources_and_results\outputs"
    "tools" =
        "legacy_sources_and_results\tools"
    "README_WORKSPACE.md" =
        "legacy_sources_and_results\README_WORKSPACE.md"
    "author_deliveries_M1_20260728" =
        "team_work_history\author_deliveries_M1_20260728"
    "author_deliveries_M2_20260728" =
        "team_work_history\author_deliveries_M2_20260728"
    "author_deliveries_M3_20260728" =
        "team_work_history\author_deliveries_M3_20260728"
    "author_intake_M3_20260728" =
        "team_work_history\author_intake_M3_20260728"
    "reviewer_deliveries_C1_20260728" =
        "team_work_history\reviewer_deliveries_C1_20260728"
    "reviewer_deliveries_C2_20260728" =
        "team_work_history\reviewer_deliveries_C2_20260728"
    "reviewer_deliveries_C3_20260728" =
        "team_work_history\reviewer_deliveries_C3_20260728"
    "reviewer_deliveries_cal_cybermarket_003_r2_20260728" =
        "team_work_history\reviewer_deliveries_cal_cybermarket_003_r2_20260728"
    "reviewer_deliveries_M1_20260728" =
        "team_work_history\reviewer_deliveries_M1_20260728"
    "reviewer_deliveries_M2_20260728" =
        "team_work_history\reviewer_deliveries_M2_20260728"
    "reviewer_deliveries_M3_20260728" =
        "team_work_history\reviewer_deliveries_M3_20260728"
}

$deletes = @(
    "mp_fs_plus",
    "mp_fs_plus.zip",
    "gpu_calibration_bundle_20260729",
    "gpu_final_bundle_20260731",
    "paper_writing_starter_release_20260730",
    "calibration_team_handoff_v2_2_20260727",
    "calibration_team_role_packages_v2_2_20260727",
    "CAL-A01_assignment_v2_2_20260727.zip",
    "CAL-R01_assignment_v2_2_20260727.zip",
    "CAL-R02_assignment_v2_2_20260727.zip",
    "calibration_team_coordinator_master_v2_2_20260727.zip",
    "calibration_team_coordinator_master_v2_2_final_20260727.zip",
    "calibration_team_packages_v2_2_20260727.sha256",
    "calibration_team_packages_v2_2_final_20260727.sha256"
)

function Resolve-SafeSource {
    param([string]$Relative)
    $candidate = Join-Path $root $Relative
    if (-not (Test-Path -LiteralPath $candidate)) {
        throw "Expected cleanup source is missing: $candidate"
    }
    $resolved = (Resolve-Path -LiteralPath $candidate).Path
    $prefix = $root + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith(
        $prefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Cleanup source is outside workspace: $resolved"
    }
    if ($resolved -eq $paperWorkspace -or $resolved -eq $archiveRoot) {
        throw "Protected path selected for cleanup: $resolved"
    }
    return $resolved
}

function Get-TargetStats {
    param([string]$Path)
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) {
        $files = @(Get-ChildItem -LiteralPath $Path -Recurse -File `
            -Force -ErrorAction SilentlyContinue)
        return @{
            Files = $files.Count
            Bytes = [long](($files | Measure-Object Length -Sum).Sum)
            SHA256 = $null
        }
    }
    return @{
        Files = 1
        Bytes = [long]$item.Length
        SHA256 = (Get-FileHash -LiteralPath $Path `
            -Algorithm SHA256).Hash.ToLower()
    }
}

$rows = @()
foreach ($entry in $moves.GetEnumerator()) {
    $source = Resolve-SafeSource $entry.Key
    $destination = Join-Path $archiveRoot $entry.Value
    $destinationFull = [System.IO.Path]::GetFullPath($destination)
    $archivePrefix = [System.IO.Path]::GetFullPath($archiveRoot) +
        [System.IO.Path]::DirectorySeparatorChar
    if (-not $destinationFull.StartsWith(
        $archivePrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Unsafe archive destination: $destinationFull"
    }
    if (Test-Path -LiteralPath $destinationFull) {
        throw "Archive destination already exists: $destinationFull"
    }
    $stats = Get-TargetStats $source
    $rows += [pscustomobject]@{
        Action = "move"
        Source = $source
        Destination = $destinationFull
        FileCount = $stats.Files
        SizeBytes = $stats.Bytes
        SHA256IfSingleFile = $stats.SHA256
        Status = "planned"
    }
}
foreach ($relative in $deletes) {
    $source = Resolve-SafeSource $relative
    $stats = Get-TargetStats $source
    $rows += [pscustomobject]@{
        Action = "delete"
        Source = $source
        Destination = $null
        FileCount = $stats.Files
        SizeBytes = $stats.Bytes
        SHA256IfSingleFile = $stats.SHA256
        Status = "planned"
    }
}

$rows |
    Export-Csv -LiteralPath $manifestPath -Delimiter "`t" `
        -NoTypeInformation -Encoding utf8

New-Item -ItemType Directory -Path $archiveRoot -Force | Out-Null
foreach ($entry in $moves.GetEnumerator()) {
    $source = Resolve-SafeSource $entry.Key
    $destination = Join-Path $archiveRoot $entry.Value
    $parent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Move-Item -LiteralPath $source -Destination $destination
}

foreach ($relative in $deletes) {
    $source = Resolve-SafeSource $relative
    $item = Get-Item -LiteralPath $source -Force
    if ($item.PSIsContainer) {
        Remove-Item -LiteralPath $source -Recurse -Force
    }
    else {
        Remove-Item -LiteralPath $source -Force
    }
}

foreach ($row in $rows) {
    if ($row.Action -eq "move") {
        $row.Status = if (Test-Path -LiteralPath $row.Destination) {
            "completed"
        }
        else {
            "failed"
        }
    }
    else {
        $row.Status = if (-not (Test-Path -LiteralPath $row.Source)) {
            "completed"
        }
        else {
            "failed"
        }
    }
}
$rows |
    Export-Csv -LiteralPath $manifestPath -Delimiter "`t" `
        -NoTypeInformation -Encoding utf8

$failed = @($rows | Where-Object Status -ne "completed")
$summary = [ordered]@{
    status = if ($failed.Count) { "failed" } else { "completed" }
    completed_at_utc = [DateTime]::UtcNow.ToString("o")
    root = $root
    canonical_workspace = $paperWorkspace
    archive_root = $archiveRoot
    moved_target_count = @($rows | Where-Object Action -eq "move").Count
    deleted_target_count = @($rows | Where-Object Action -eq "delete").Count
    moved_bytes = [long]((
        $rows |
            Where-Object Action -eq "move" |
            Measure-Object SizeBytes -Sum
    ).Sum)
    deleted_bytes = [long]((
        $rows |
            Where-Object Action -eq "delete" |
            Measure-Object SizeBytes -Sum
    ).Sum)
    failed_target_count = $failed.Count
    manifest = $manifestPath
}
$summary |
    ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath $summaryPath -Encoding utf8

if ($failed.Count) {
    throw "Cleanup completed with $($failed.Count) failed targets"
}

Write-Host ""
Write-Host "ROOT CLEANUP: COMPLETED" -ForegroundColor Green
Write-Host "Moved targets: $($summary.moved_target_count)"
Write-Host "Deleted targets: $($summary.deleted_target_count)"
Write-Host "Moved GiB: $([math]::Round($summary.moved_bytes / 1GB, 2))"
Write-Host "Deleted MiB: $([math]::Round($summary.deleted_bytes / 1MB, 2))"
Write-Host "Archive: $archiveRoot"
Write-Host "Manifest: $manifestPath"

