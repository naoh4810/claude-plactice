#!/bin/sh
# Mac / Linux の cron から呼ぶ用
cd "$(dirname "$0")"
. .venv/bin/activate
python job_watcher.py >> run.log 2>&1
