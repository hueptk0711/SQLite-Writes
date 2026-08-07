[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$output = Join-Path $workspace "07_reproducibility\WORKSPACE_FILE_INVENTORY.tsv"

$rows = Get-ChildItem -LiteralPath $workspace -Recurse -File |
    Where-Object { $_.FullName -ne $output } |
    Sort-Object FullName |
    ForEach-Object {
        [pscustomobject]@{
            RelativePath = $_.FullName.Substring($workspace.Length + 1)
            SizeBytes = $_.Length
            SHA256 = (Get-FileHash -LiteralPath $_.FullName `
                -Algorithm SHA256).Hash.ToLower()
        }
    }

$rows |
    Export-Csv -LiteralPath $output -Delimiter "`t" `
        -NoTypeInformation -Encoding utf8

Write-Host "WORKSPACE INVENTORY: $($rows.Count) files"
Write-Host "OUTPUT: $output"

