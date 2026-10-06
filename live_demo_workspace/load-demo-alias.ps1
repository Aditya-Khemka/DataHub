# Dot-source this file (". .\load-demo-alias.ps1") to get a `datahub` command in this PowerShell session.
# It delegates to datahub.ps1 so both share one copy of the argument handling.
$script:DataHubWrapper = Join-Path $PSScriptRoot "datahub.ps1"

function datahub {
    & $script:DataHubWrapper @args
}

Write-Host "Loaded datahub alias for /app/live_demo_workspace"
Write-Host "Example: datahub init"
Write-Host 'Example: datahub push -m "Initial demo snapshot"'
Write-Host "Example: datahub pull"
Write-Host "Example: datahub log"
Write-Host 'Example: datahub query "row_count == 9"'
