#!/bin/bash
# 每天抓取法语新闻并写入 index.html（供 launchd 定时任务或手动运行）
cd "$(dirname "$0")" || exit 1
python3 fetch_news.py
