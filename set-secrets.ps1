# Copies the keys from .env into this repository's GitHub Actions Secrets.
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File .\set-secrets.ps1
# Values are piped straight to GitHub (encrypted there) and never printed.
$repo = "dtechmonkey/insta-automation"
$gh = "C:\Program Files\GitHub CLI\gh.exe"
$names = "GEMINI_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "HF_TOKEN", "PEXELS_API_KEY", "PIXABAY_API_KEY", "IG_USER_ID", "IG_ACCESS_TOKEN"

$envFile = Join-Path $PSScriptRoot ".env"
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*([A-Z_]+)\s*=\s*(.+?)\s*$' -and $names -contains $Matches[1]) {
        $name = $Matches[1]
        & $gh secret set $name --body $Matches[2] --repo $repo   # --body: piping adds a hidden newline
        if ($LASTEXITCODE -eq 0) { Write-Host "  set $name" } else { Write-Host "  FAILED $name" }
    }
}
Write-Host "`nSecrets now on GitHub (names only):"
& $gh secret list --repo $repo
