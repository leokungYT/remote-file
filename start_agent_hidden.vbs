' Launch the Remote File Agent fully hidden (no window at all) - tray icon only.
' Double-click this file to start the agent silently.
' (Dependencies must already be installed - run start_agent.bat or install-autostart.bat once first.)
Dim fso, sh, folder
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = folder
' window style 0 = hidden, False = don't wait
sh.Run "pythonw " & Chr(34) & folder & "\agent.py" & Chr(34), 0, False
