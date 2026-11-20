# Find LFS-tracked files not in local store (status = "-")
$output = & git lfs ls-files 2>&1
$missingFiles = @()
foreach ($line in $output) {
    # Format: HASH STATUS PATH
    # Status "-" means missing from local LFS store
    if ($line -match '^[0-9a-f]{40} - (.+)$') {
        $missingFiles += $matches[1]
    }
}

Write-Host "Found $($missingFiles.Count) LFS files missing from local store"

# Re-add each file to force LFS clean filter and populate local store
$count = 0
foreach ($file in $missingFiles) {
    if (Test-Path $file) {
        & git add "$file" 2>&1 | Out-Null
        $count++
        if ($count % 50 -eq 0) {
            Write-Host "Processed $count files..."
        }
    }
}

Write-Host "Re-added $count files to git index"

# Check status
& git diff --cached --name-only | Measure-Object -Line | ForEach-Object { Write-Host "Staged files: $($_.Lines)" }