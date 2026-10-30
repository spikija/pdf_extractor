@echo off
cd /d "%~dp0"
set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
set PYTHONDONTWRITEBYTECODE=1
".venv\Scripts\python.exe" -m streamlit run review_app/app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
