# run_all.ps1  (PowerShell 5.1 compatible)

$cpvs  = @("33100","33140","33141","33600","33690","34144","34300","45000","45233","60130","66510","90500")
$years = 2018..2022

# Adjust this to control parallelism (2–4 is usually safe)
$MaxParallel = 3

# Create folders if not exist
New-Item -ItemType Directory -Force -Path "out"  | Out-Null
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

# Helper: wait until running jobs are below threshold
function Wait-ForSlot($max) {
    while ((Get-Job -State Running).Count -ge $max) {
        Start-Sleep -Seconds 5
    }
}

foreach ($cpv in $cpvs) {
    foreach ($y in $years) {

        Wait-ForSlot $MaxParallel

        $outPath = Join-Path "out"  ("metrics_cpv{0}_y{1}.jsonl" -f $cpv, $y)
        $logPath = Join-Path "logs" ("metrics_cpv{0}_y{1}.log"   -f $cpv, $y)

        $args = @(
            "featuresall.py",
            "--cpv", $cpv,
            "--base_year", $y,
            "--out", $outPath,
            "--log", $logPath
        )

        Start-Job -Name ("cpv{0}_y{1}" -f $cpv, $y) -ScriptBlock {
            param($a)
            python @a
        } -ArgumentList (,$args) | Out-Null

        Write-Host ("Started: CPV={0}, base_year={1}" -f $cpv, $y)
    }
}

# Wait for all jobs to complete
Write-Host "Waiting for all jobs to finish..."
Get-Job | Wait-Job

# Print summary (failed jobs etc.)
Write-Host "All jobs finished. Summary:"
Get-Job | Select-Object Name, State, HasMoreData

# Optional: to see errors per job
# Get-Job | Receive-Job -Keep
