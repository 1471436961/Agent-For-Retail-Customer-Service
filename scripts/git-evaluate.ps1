# Dot-source in PowerShell: . .\scripts\git-evaluate.ps1
# Current session only. No profile, execution-policy or global Git-hook changes.
if (Test-Path Function:\git) {
    Write-Warning 'An existing git function was kept. Use: py -3 scripts/git-evaluate.py push'
    return
}
if (Test-Path Alias:\git) {
    Write-Warning 'An existing git alias was kept. Use: py -3 scripts/git-evaluate.py push'
    return
}
function global:git {
    $gitExecutable = (Get-Command git.exe -CommandType Application -ErrorAction Stop).Source
    if ($args.Count -gt 0 -and $args[0] -eq 'push') {
        $repositoryRoot = & $gitExecutable rev-parse --show-toplevel
        if ($LASTEXITCODE -ne 0) { return }
        $client = Join-Path $repositoryRoot 'scripts/git-evaluate.py'
        if (Test-Path $client) {
            $pushArgs = @($args | Select-Object -Skip 1)
            if (Get-Command py.exe -ErrorAction SilentlyContinue) {
                & py.exe -3 $client push @pushArgs
            } else {
                & python.exe $client push @pushArgs
            }
            return
        }
    }
    & $gitExecutable @args
}
Write-Host 'Enabled git push -> evaluation progress (this PowerShell session). Disable: Remove-Item Function:\git'
