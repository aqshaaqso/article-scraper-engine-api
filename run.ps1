$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Virtual environment belum tersedia. Ikuti langkah setup pada README.md."
}

Write-Host ""
Write-Host "Article Scraper Engine API" -ForegroundColor Green
Write-Host "Swagger UI  : http://127.0.0.1:8010/swagger/index.html"
Write-Host "OpenAPI JSON: http://127.0.0.1:8010/openapi.json"
Write-Host "ReDoc       : http://127.0.0.1:8010/redoc"
Write-Host "Health      : http://127.0.0.1:8010/health"
Write-Host ""
Write-Host "Tekan Ctrl+C untuk menghentikan backend." -ForegroundColor Yellow
Write-Host ""

& $PythonPath -m uvicorn article_scraper_lab.main:app --host 127.0.0.1 --port 8010
