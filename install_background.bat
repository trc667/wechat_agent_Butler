@echo off
rem ============================================================
rem  一键安装"后台常驻"
rem   1) 电源:关显示器 10 分钟(息屏),系统永不睡眠、合盖不睡
rem   2) 开机自启:在"启动文件夹"创建快捷方式,指向 run_bot_hidden.pyw
rem   3) 立即在后台启动小管家
rem
rem  卸载:删掉启动文件夹里的 xiaoji_bot.lnk,电源设置自己调回
rem  注意:安装前先关掉旧的 bot 终端窗口,避免两个实例打架
rem ============================================================

echo [1/3] 设置电源:息屏不睡眠 ...
powercfg /change monitor-timeout-ac 10
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT

echo [2/3] 注册开机自启 ...
set "PRJDIR=%~dp0"
powershell -NoProfile -Command "$lnk=Join-Path ([Environment]::GetFolderPath('Startup')) 'xiaoji_bot.lnk';$w=New-Object -ComObject WScript.Shell;$c=$w.CreateShortcut($lnk);$c.TargetPath=$env:WINDIR+'\System32\wscript.exe';$c.Arguments=chr(34)+$env:PRJDIR+'start_guardian.vbs'+chr(34);$c.WorkingDirectory=$env:PRJDIR;$c.WindowStyle=7;$c.Save()"

echo [3/3] 现在后台启动 ...
start "" "%WINDIR%\System32\wscript.exe" "%PRJDIR%start_guardian.vbs"

echo.
echo 完成!日志在 logs\bot.log
echo 想看实时对话:双击 view_log.bat(Ctrl+C 退出查看)
pause
