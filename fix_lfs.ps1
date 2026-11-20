# Parse git lfs fsck output to find missing files
$output = & git lfs fsck 2>&1
$missingFiles = @()
foreach ($line in $output) {
    if ($line -match 'openError: (.+) \(.+\) could not be checked') {
        $missingFiles += $matches[1]
    }
}

Write-Host "Found $($missingFiles.Count) missing LFS files"

# Remove missing files from git tracking
foreach ($file in $missingFiles) {
    if (Test-Path $file) {
        Write-Host "File exists on disk: $file"
    } else {
        Write-Host "Removing from tracking: $file"
        & git rm --cached "$file" 2>&1 | Out-Null
    }
}

# Check remaining missing
$output2 = & git lfs fsck 2>&1
$remainingMissing = ($output2 | Where-Object { $_ -match 'could not be checked' }).Count
Write-Host "Remaining missing after cleanup: $remainingMissing"