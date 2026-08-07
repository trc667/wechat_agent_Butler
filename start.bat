@echo off
cd /d "%~dp0"
title 小管家 - 微信智能管家
echo [小管家] 正在启动...
echo [小管家] 说明：
echo [小管家]   1. 首次运行会显示登录二维码，用手机微信扫一下即可，以后自动连接
echo [小管家]   2. 登录成功后去微信里给「小管家」发消息
echo [小管家]   3. 这个窗口别关 = 小管家在线
echo.
python -u ilink_bot.py
echo.
echo [小管家] 已退出，按任意键关闭窗口（上面有报错的话先看一眼）
pause >nul
