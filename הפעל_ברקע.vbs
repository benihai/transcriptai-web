' מפעיל את זיהוי הפגישות ברקע - ללא חלון טרמינל כלל
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
proj = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = proj & "\.venv\Scripts\pythonw.exe"
sh.CurrentDirectory = proj
sh.Run """" & pyw & """ -m src.watcher", 0, False
