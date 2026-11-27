@echo off
rem LLM Data Studio: starts the local server (repo venv) and opens it in an Edge app window.
rem Extra arguments go to studio.server, e.g. --db D:\backup\taskset.db
setlocal
cd /d "%~dp0.."
set "PYTHONUTF8=1"
if not defined LLM_DATA_ROOT set "LLM_DATA_ROOT=E:\llm-data"
netstat -ano | findstr /r /c:"127.0.0.1:8767 .*LISTENING" >nul
if errorlevel 1 (
  start "LLM Data Studio (dong cua so nay de tat)" .venv\Scripts\python.exe -m studio.server --port 8767 %*
  ping -n 3 127.0.0.1 >nul
)
start "" msedge --app=http://127.0.0.1:8767
