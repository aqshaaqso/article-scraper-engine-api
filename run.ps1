param(
    [ValidateRange(1, 65535)]
    [int]$ApiPort = 8010
)

$ErrorActionPreference = "Stop"
$env:API_PORT = $ApiPort.ToString()

Write-Host "Starting FastAPI middleware, Go worker, and PostgreSQL on port $ApiPort..." -ForegroundColor Green
docker compose up -d --build --wait --wait-timeout 180
docker compose ps

Write-Host "Swagger UI: http://127.0.0.1:$ApiPort/swagger/index.html" -ForegroundColor Green
Write-Host "Health    : http://127.0.0.1:$ApiPort/health"
Write-Host "Readiness : http://127.0.0.1:$ApiPort/ready"
