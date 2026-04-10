@echo off
REM Local dev: scrape -> AI pipeline -> processed_articles (feed-ready).
REM Start MongoDB on your PC first, then double-click this file or run from cmd:
REM   run-local-news-cycle.bat
REM With options:
REM   run-local-news-cycle.bat --sources rss --scrape-limit 20 --pipeline-limit 30

cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" manage.py run_news_cycle %*
) else (
  python manage.py run_news_cycle %*
)

echo.
pause
