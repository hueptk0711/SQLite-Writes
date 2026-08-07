[CmdletBinding()]
param(
    [string]$Archive,
    [string]$Checksum
)

$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$incoming = Join-Path $workspace "04_results\00_incoming_from_server"
$extractedBase = Join-Path $workspace "04_results\01_extracted_archive"
$paperReady = Join-Path $workspace "04_results\02_paper_ready"
$reproducibility = Join-Path $workspace "07_reproducibility\server_final_run"

if (-not $Archive) {
    $candidate = Get-ChildItem -LiteralPath $incoming -File `
        -Filter "mp_fs_plus_final300_*.tar.gz" |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if (-not $candidate) {
        throw "No mp_fs_plus_final300_*.tar.gz archive found in $incoming"
    }
    $archivePath = $candidate.FullName
}
else {
    $archivePath = (Resolve-Path -LiteralPath $Archive).Path
}

if (-not $Checksum) {
    $checksumPath = "$archivePath.sha256"
}
else {
    $checksumPath = (Resolve-Path -LiteralPath $Checksum).Path
}
if (-not (Test-Path -LiteralPath $checksumPath -PathType Leaf)) {
    throw "Missing checksum file: $checksumPath"
}

$actual = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLower()
$expected = ((Get-Content -LiteralPath $checksumPath -Raw) -split "\s+")[0].ToLower()
if ($actual -ne $expected) {
    throw "SHA256 mismatch. Expected $expected but found $actual"
}

$archiveName = [System.IO.Path]::GetFileName($archivePath)
$runId = $archiveName -replace "\.tar\.gz$", ""
$extractRoot = Join-Path $extractedBase $runId
$resolvedBase = [System.IO.Path]::GetFullPath($extractedBase)
$resolvedExtract = [System.IO.Path]::GetFullPath($extractRoot)
if (-not $resolvedExtract.StartsWith(
    $resolvedBase + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Unsafe extraction target: $resolvedExtract"
}
if (Test-Path -LiteralPath $extractRoot) {
    throw "Run already extracted: $extractRoot"
}

New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
& tar -xzf $archivePath -C $extractRoot
if ($LASTEXITCODE -ne 0) {
    throw "tar extraction failed with exit code $LASTEXITCODE"
}

function Get-UniqueArtifact {
    param([string]$Name)
    $matches = @(Get-ChildItem -LiteralPath $extractRoot -Recurse -File `
        -Filter $Name)
    if ($matches.Count -ne 1) {
        throw "Expected exactly one $Name, found $($matches.Count)"
    }
    return $matches[0]
}

$resultJson = Get-UniqueArtifact "final_matrix_results.json"
$mainTable = Get-UniqueArtifact "final_main_table.csv"
$summaryMarkdown = Get-UniqueArtifact "final_matrix_summary.md"
$finalProtocol = Get-UniqueArtifact "final_protocol.json"
$modelManifest = Get-UniqueArtifact "final_model_manifest.json"
$environmentManifest = Get-UniqueArtifact "environment_manifest_final_server.json"
$v1AbortEvidence = Get-UniqueArtifact "v1_abort_evidence.json"
$protocolAmendment = Get-UniqueArtifact `
    "final_protocol_amendment_v2_out8192.json"
$failureAdjudication = Get-UniqueArtifact `
    "final_output_limit_adjudication_v2_1.json"
$adjudicationMarker = Get-UniqueArtifact "FINAL_RUN_ADJUDICATED.json"
$oracleGateAdjudication = Get-UniqueArtifact `
    "final_gold_oracle_gate_adjudication_v2_1_rev2.json"
$oracleAdjudicationMarker = Get-UniqueArtifact `
    "FINAL_ORACLE_ADJUDICATED.json"

$report = Get-Content -LiteralPath $resultJson.FullName -Raw | ConvertFrom-Json
if ($report.status -ne "pass") {
    throw "Final result report status is not pass: $($report.status)"
}
if ($report.paper_result_eligible -ne $true) {
    throw "Final result report is not paper-result eligible"
}
if ($report.sample_count -ne 300 -or $report.method_count -ne 6) {
    throw "Expected 300 samples and 6 methods"
}
if ($report.report_version -ne 3 -or `
    $report.reporting_protocol_id -ne `
    "mp_fs_plus_external_holdout_v2_out8192+reporting_adjudication_v2_1_rev2") {
    throw "Expected the reporting-adjudication v2.1 rev2 report"
}
if ($report.reporting_amendment.amendment_id -ne `
    "conservative_failure_adjudication_v2_1") {
    throw "Unexpected reporting amendment"
}
if ($report.oracle_gate_adjudication.amendment_id -ne `
    "gold_oracle_null_status_gate_correction_v2_1_rev2") {
    throw "Unexpected Gold-MP oracle gate adjudication"
}
$protocol = Get-Content -LiteralPath $finalProtocol.FullName `
    -Raw | ConvertFrom-Json
if ($protocol.protocol_id -ne "mp_fs_plus_external_holdout_v2_out8192") {
    throw "Expected final protocol v2 out8192, found $($protocol.protocol_id)"
}
$protocolHash = (Get-FileHash -LiteralPath $finalProtocol.FullName `
    -Algorithm SHA256).Hash.ToLower()
$amendment = Get-Content -LiteralPath $protocolAmendment.FullName `
    -Raw | ConvertFrom-Json
if ($amendment.status -ne "frozen_capacity_only_amendment") {
    throw "Protocol amendment record is not frozen"
}
if ($amendment.successor.sha256 -ne $protocolHash) {
    throw "Protocol amendment successor hash does not match final_protocol.json"
}
if ($amendment.predecessor.sha256 -ne `
    "41eee8d41c2205af9485e03fd654a9a46a28720f78511b61babe1443c7d3820a") {
    throw "Unexpected predecessor protocol hash"
}
$abortEvidence = Get-Content -LiteralPath $v1AbortEvidence.FullName `
    -Raw | ConvertFrom-Json
if ($abortEvidence.status -ne "pass" -or `
    $abortEvidence.trigger.sample_id -ne "final_vaccine_031") {
    throw "Protocol-v1 abort evidence is missing or inconsistent"
}
$adjudication = Get-Content -LiteralPath $failureAdjudication.FullName `
    -Raw | ConvertFrom-Json
if ($adjudication.status -ne `
    "frozen_conservative_failure_adjudication") {
    throw "Failure-adjudication record is not frozen"
}
if ($adjudication.base_protocol_sha256 -ne $protocolHash) {
    throw "Failure adjudication does not bind to final_protocol.json"
}
if ($adjudication.prediction_artifacts_modified -ne $false -or `
    $adjudication.score_policy -ne `
    "retain_in_denominator_and_score_as_incorrect") {
    throw "Failure adjudication is not conservative"
}
$affectedIds = @($adjudication.affected_sample_ids)
if ($affectedIds.Count -ne 2 -or `
    $affectedIds[0] -ne "final_polar_048" -or `
    $affectedIds[1] -ne "final_vaccine_047") {
    throw "Unexpected adjudicated sample IDs"
}
$adjudicationHash = (Get-FileHash -LiteralPath `
    $failureAdjudication.FullName -Algorithm SHA256).Hash.ToLower()
$marker = Get-Content -LiteralPath $adjudicationMarker.FullName `
    -Raw | ConvertFrom-Json
if ($marker.status -ne "adjudicated_as_incorrect" -or `
    $marker.adjudication_record_sha256 -ne $adjudicationHash -or `
    $marker.prediction_artifacts_modified -ne $false) {
    throw "MP-FS+ adjudication marker is inconsistent"
}
$oracleAdjudication = Get-Content -LiteralPath `
    $oracleGateAdjudication.FullName -Raw | ConvertFrom-Json
if ($oracleAdjudication.status -ne "frozen_oracle_gate_correction" -or `
    $oracleAdjudication.verified_rows -ne 300 -or `
    $oracleAdjudication.actual_missing_predictions -ne 0 -or `
    $oracleAdjudication.prediction_artifacts_modified -ne $false -or `
    $oracleAdjudication.evaluation_artifacts_modified -ne $false -or `
    $oracleAdjudication.metrics_modified -ne $false) {
    throw "Gold-MP oracle gate adjudication is inconsistent"
}
$oracleAdjudicationHash = (Get-FileHash -LiteralPath `
    $oracleGateAdjudication.FullName -Algorithm SHA256).Hash.ToLower()
$oracleMarker = Get-Content -LiteralPath `
    $oracleAdjudicationMarker.FullName -Raw | ConvertFrom-Json
if ($oracleMarker.status -ne "adjudicated_oracle_complete" -or `
    $oracleMarker.adjudication_record_sha256 -ne `
    $oracleAdjudicationHash -or `
    $oracleMarker.verified_rows -ne 300 -or `
    $oracleMarker.target_state_accuracy -ne 1.0 -or `
    $oracleMarker.prediction_artifacts_modified -ne $false) {
    throw "Gold-MP oracle adjudication marker is inconsistent"
}

$destinations = @(
    (Join-Path $paperReady "reports\final_matrix_results.json"),
    (Join-Path $paperReady "tables\final_main_table.csv"),
    (Join-Path $paperReady "reports\final_matrix_summary.md")
)
foreach ($destination in $destinations) {
    if (Test-Path -LiteralPath $destination) {
        throw "Paper-ready destination already exists: $destination"
    }
}

New-Item -ItemType Directory -Path (Join-Path $paperReady "tables") -Force |
    Out-Null
New-Item -ItemType Directory -Path (Join-Path $paperReady "reports") -Force |
    Out-Null
New-Item -ItemType Directory -Path $reproducibility -Force | Out-Null

Copy-Item -LiteralPath $resultJson.FullName `
    -Destination (Join-Path $paperReady "reports\final_matrix_results.json")
Copy-Item -LiteralPath $mainTable.FullName `
    -Destination (Join-Path $paperReady "tables\final_main_table.csv")
Copy-Item -LiteralPath $summaryMarkdown.FullName `
    -Destination (Join-Path $paperReady "reports\final_matrix_summary.md")
Copy-Item -LiteralPath $finalProtocol.FullName -Destination $reproducibility
Copy-Item -LiteralPath $modelManifest.FullName -Destination $reproducibility
Copy-Item -LiteralPath $environmentManifest.FullName -Destination $reproducibility
Copy-Item -LiteralPath $v1AbortEvidence.FullName -Destination $reproducibility
Copy-Item -LiteralPath $protocolAmendment.FullName `
    -Destination $reproducibility
Copy-Item -LiteralPath $failureAdjudication.FullName `
    -Destination $reproducibility
Copy-Item -LiteralPath $adjudicationMarker.FullName `
    -Destination $reproducibility
Copy-Item -LiteralPath $oracleGateAdjudication.FullName `
    -Destination $reproducibility
Copy-Item -LiteralPath $oracleAdjudicationMarker.FullName `
    -Destination $reproducibility
Copy-Item -LiteralPath $checksumPath -Destination $reproducibility

$importReport = [ordered]@{
    status = "pass"
    imported_at_utc = [DateTime]::UtcNow.ToString("o")
    run_id = $runId
    archive = $archivePath
    archive_sha256 = $actual
    checksum = $checksumPath
    final_protocol_id = $protocol.protocol_id
    final_protocol_sha256 = $protocolHash
    protocol_amendment_id = $amendment.amendment_id
    predecessor_protocol_sha256 = $amendment.predecessor.sha256
    reporting_protocol_id = $report.reporting_protocol_id
    failure_adjudication_id = $adjudication.amendment_id
    failure_adjudication_sha256 = $adjudicationHash
    adjudicated_sample_ids = $affectedIds
    oracle_gate_adjudication_id = $oracleAdjudication.amendment_id
    oracle_gate_adjudication_sha256 = $oracleAdjudicationHash
    oracle_verified_rows = $oracleAdjudication.verified_rows
    extracted_to = $extractRoot
    paper_result_eligible = $true
    sample_count = 300
    method_count = 6
    paper_ready_files = $destinations
}
$importReport |
    ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath (Join-Path $reproducibility "IMPORT_REPORT.json") `
        -Encoding utf8

Write-Host ""
Write-Host "FINAL RESULT IMPORT: PASS" -ForegroundColor Green
Write-Host "Paper-result eligible: True"
Write-Host "Run ID: $runId"
Write-Host "Archive SHA256: $actual"
Write-Host "Main table: $(Join-Path $paperReady 'tables\final_main_table.csv')"
Write-Host "Summary: $(Join-Path $paperReady 'reports\final_matrix_summary.md')"
