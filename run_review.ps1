# Запуск агента перевірки робіт у Windows: збирання -> оцінювання моделлю -> звіт.
# Вручну:  powershell -ExecutionPolicy Bypass -File .\run_review.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

New-Item -ItemType Directory -Force logs | Out-Null
$Log = "logs\run-$(Get-Date -Format yyyyMMdd-HHmmss).log"
Start-Transcript -Path $Log -Append | Out-Null
try {
    Write-Host "== Збирання нових робіт"
    $Out = & $Py review.py fetch
    if ($LASTEXITCODE -ne 0) { $Out; throw "Збирання не вдалося" }
    $Out
    $RunDir = ($Out | Select-String '^RUN_DIR=(.*)$').Matches[0].Groups[1].Value
    $RelRun = $RunDir
    $Items = (Get-Content "$RunDir\manifest.json" -Raw -Encoding UTF8 | ConvertFrom-Json).items.Count
    if ($Items -eq 0) { Write-Host "Нових зданих робіт немає."; return }

    if (Get-ChildItem "$RunDir\packets\*.json" -ErrorAction SilentlyContinue) {
        Write-Host "== Оцінювання (claude -p)"
        claude -p "Виконай інструкції з AGENT.md. Тека запуску: $RelRun" `
            --permission-mode dontAsk `
            --allowedTools "Read(./AGENT.md)" "Read(./criteria/**)" "Read(./$RelRun/packets/**)" `
                           "Read(./$RelRun/files/**)" "Glob" "Write(./$RelRun/results/**)" `
            --disallowedTools "Bash" "WebFetch" "WebSearch" `
                              "Read(./token.json)" "Read(./credentials.json)" "Read(./state.json)" `
                              "Read(./config.yaml)" "Read(./$RelRun/manifest.json)"
    }

    Write-Host "== Оновлення звіту"
    & $Py review.py apply --run $RunDir
} finally {
    Stop-Transcript | Out-Null
}
