' Run the server watchdog hidden (no flashing window)
Set sh  = CreateObject("WScript.Shell")
sh.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.Run "server-watchdog.bat", 0, False
