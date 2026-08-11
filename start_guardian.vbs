' 小管家守护进程启动器（隐藏窗口，开机自启用）
' 注意：守护进程必须用 python.exe（控制台）跑，pythonw.exe 会在部分会话下
' 拉起子进程时 0xC0000142 崩溃（日志见 logs/bot.log）
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.Run """D:\anaconda\anaconda1\python.exe"" """ & dir & "\run_bot_hidden.pyw""", 0, False
