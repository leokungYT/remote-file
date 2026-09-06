' Run master-tunnel.ps1 hidden (no console window).
' Registered by setup-master-tunnel.bat as scheduled task "RemoteFileTunnel" (at logon).
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & here & "\master-tunnel.ps1""", 0, False
