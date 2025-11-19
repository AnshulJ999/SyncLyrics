Set shell = CreateObject("WScript.Shell")

' 1️⃣ Get the path to pythonw.exe from PATH
pythonPath = shell.Exec("cmd /c where pythonw.exe").StdOut.ReadLine

' 2️⃣ Get the path to certifi's cacert.pem
' This calls Python to find the certifi path dynamically
certPathCmd = """" & pythonPath & """ -c ""import certifi; print(certifi.where())"""
certPath = shell.Exec("cmd /c " & certPathCmd).StdOut.ReadLine

' 3️⃣ Set REQUESTS_CA_BUNDLE for this process
shell.Environment("PROCESS")("REQUESTS_CA_BUNDLE") = certPath

' 4️⃣ Build the command to run your script (relative to this script's folder)
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
syncLyricsPath = fso.BuildPath(scriptDir, "sync_lyrics.py")

' 5️⃣ Run hidden, no output
cmd = """" & pythonPath & """ """ & syncLyricsPath & """ >nul 2>&1"
' shell.Run cmd, 0, False
' Show console window (2 instead of 0) and wait until it closes (True instead of False)
shell.Run cmd, 2, True
