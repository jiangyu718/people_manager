#!/usr/bin/env python3
"""Gunicorn/生产环境入口。

用法示例：
    gunicorn -w 1 -b 0.0.0.0:5000 wsgi:app

⚠️ 必须使用单 worker（-w 1）。APScheduler 以 BackgroundScheduler 形式
内嵌在进程中，多 worker 会重复触发定时任务。
"""
from main import app  # noqa: F401
