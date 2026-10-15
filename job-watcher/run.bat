@echo off
rem Windows のタスクスケジューラから呼ぶ用
cd /d %~dp0
call .venv\Scripts\activate.bat
python job_watcher.py >> run.log 2>&1
