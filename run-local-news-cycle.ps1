# Local dev: scrape -> AI pipeline -> processed_articles (feed-ready).
# Start MongoDB first, then from PowerShell:
#   .\run-local-news-cycle.ps1
# Optional args go to manage.py, e.g.:
#   .\run-local-news-cycle.ps1 --sources rss dawn --scrape-limit 15 --pipeline-limit 25

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    & $venvPy manage.py run_news_cycle @args
} else {
    python manage.py run_news_cycle @args
}
