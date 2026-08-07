@echo off
rem 实时查看小管家后台日志（bot.log），Ctrl+C 退出
chcp 65001 >nul
powershell -NoProfile -Command "Get-Content -Path '%~dp0logs\bot.log' -Wait -Encoding UTF8"
