# Run the analysis pipeline of the report: 01 -> 06 -> 07 -> 07b -> 08 -> 09 -> 10.
# Setup first:  py -3.14 -m venv .venv; .venv\Scripts\pip install -r requirements.txt
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1 -Zip D:\OSSGameBench.zip
#       (extracts the needed files from the downloaded zip with 00_extract_zip.py, then runs the pipeline)
#   powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1
#       (the data is already in the data folder, e.g. via 00_fetch_dataset.ps1 and 05_fetch_commits.py --analysed)
param([string]$Zip = "")
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here "..\.venv\Scripts\python.exe"
if ($Zip -ne "") { $Zip = (Resolve-Path $Zip).Path }  # resolve before moving into scripts
Push-Location $here
try {
    $steps = @()
    if ($Zip -ne "") { $steps += , @("00_extract_zip.py", $Zip) }
    foreach ($s in "01_preprocess.py", "06_link_fix_commits.py", "07_classify_files.py", "07b_repo_assets.py",
                   "08_theme_rq.py", "09_theme_figs.py", "10_validation.py") { $steps += , @($s) }
    foreach ($st in $steps) {
        Write-Host "=== $($st -join ' ')"
        & $py @st
        if ($LASTEXITCODE -ne 0) { throw "$($st[0]) failed" }
    }
} finally { Pop-Location }
