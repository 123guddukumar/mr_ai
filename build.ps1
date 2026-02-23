# ═══════════════════════════════════════════════════════════════════════
# MR AI RAG v2 — Windows Quick Start Script
# Run in PowerShell (right-click → "Run as Administrator" not needed)
# Usage: .\build.ps1
# ═══════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║       MR AI RAG v2 — Builder         ║" -ForegroundColor Cyan
Write-Host "║   Optimized for i3 + 8GB RAM         ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Check Docker is running ──────────────────────────────────────────────
Write-Host "▶ Checking Docker Desktop..." -ForegroundColor Yellow
try {
    docker info | Out-Null
    Write-Host "  ✓ Docker is running" -ForegroundColor Green
} catch {
    Write-Host "  ✕ Docker Desktop is not running!" -ForegroundColor Red
    Write-Host "    Please start Docker Desktop and try again." -ForegroundColor Red
    exit 1
}

# ── Check .env file ───────────────────────────────────────────────────────
if (-Not (Test-Path ".env")) {
    Write-Host ""
    Write-Host "▶ Creating .env from .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "  ✓ .env created" -ForegroundColor Green
    Write-Host "  ⚠ Open .env and add your API keys before using the app" -ForegroundColor Yellow
} else {
    Write-Host "▶ .env found ✓" -ForegroundColor Green
}

# ── Enable BuildKit for faster builds ────────────────────────────────────
$env:DOCKER_BUILDKIT = "1"
$env:COMPOSE_DOCKER_CLI_BUILD = "1"

Write-Host ""
Write-Host "▶ Building Docker image..." -ForegroundColor Yellow
Write-Host "  This takes 8-12 min on first build (downloading packages)." -ForegroundColor Gray
Write-Host "  Subsequent builds use cache and take ~1-2 min." -ForegroundColor Gray
Write-Host ""

$start = Get-Date

# Build with progress output
docker compose build --progress=plain 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  ✕ Build failed! Check errors above." -ForegroundColor Red
    exit 1
}

$elapsed = [math]::Round(((Get-Date) - $start).TotalMinutes, 1)
Write-Host ""
Write-Host "  ✓ Build complete in $elapsed min" -ForegroundColor Green

# ── Start containers ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "▶ Starting MR AI RAG..." -ForegroundColor Yellow
docker compose up -d

if ($LASTEXITCODE -ne 0) {
    Write-Host "  ✕ Failed to start container" -ForegroundColor Red
    exit 1
}

# ── Wait for health check ─────────────────────────────────────────────────
Write-Host ""
Write-Host "▶ Waiting for app to be ready..." -ForegroundColor Yellow
Write-Host "  (First run downloads embedding model ~90MB — may take 2 min)" -ForegroundColor Gray

$maxWait = 120   # seconds
$waited  = 0
$ready   = $false

while ($waited -lt $maxWait) {
    Start-Sleep -Seconds 5
    $waited += 5
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8000/api/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {}
    Write-Host "  ... waiting ($waited s)" -ForegroundColor Gray
}

Write-Host ""
if ($ready) {
    Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Green
    Write-Host "║   ✓ MR AI RAG is READY!              ║" -ForegroundColor Green
    Write-Host "║                                      ║" -ForegroundColor Green
    Write-Host "║   Open: http://localhost:8000         ║" -ForegroundColor Green
    Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Green
    # Auto-open browser
    Start-Process "http://localhost:8000"
} else {
    Write-Host "  ⚠ App may still be loading. Check logs:" -ForegroundColor Yellow
    Write-Host "    docker compose logs -f" -ForegroundColor Cyan
    Write-Host "  Then open: http://localhost:8000" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  docker compose logs -f        # live logs"
Write-Host "  docker compose down           # stop"
Write-Host "  docker compose restart        # restart"
Write-Host "  docker compose down -v        # stop + delete all data"
Write-Host ""