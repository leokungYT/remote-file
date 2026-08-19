' Launches vpn-toggle.bat /auto with no visible window.
' Used by the "Surfshark VPN AutoConnect" scheduled task.
' Keep this file ASCII-only (wscript reads .vbs as ANSI).
Option Explicit
Dim sh, fso, bat
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
bat = fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "vpn-toggle.bat")
If Not fso.FileExists(bat) Then WScript.Quit 1
' 0 = hidden window, False = do not wait
sh.Run """" & bat & """ /auto", 0, False
