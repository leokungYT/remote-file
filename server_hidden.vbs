' Launch the master server (auto-restart loop) in a minimized window.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
' 7 = minimized (not activated); False = don't wait
sh.Run "cmd /c run_server_forever.bat", 7, False
