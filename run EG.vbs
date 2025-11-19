Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' --- 1️⃣ Detect debug mode ---
debugMode = False
If WScript.Arguments.Count > 0 Then
    If LCase(WScript.Arguments(0)) = "/debug" Then
        debugMode = True
    End If
End If

' --- 2️⃣ Choose Python executable ---
If debugMode Then
    pythonPath = shell.Exec("cmd /c where python.exe").StdOut.ReadLine
Else
    pythonPath = shell.Exec("cmd /c where pythonw.exe").StdOut.ReadLine
End If

' --- 3️⃣ Get certifi's cacert.pem path dynamically ---
certPathCmd = """" & pythonPath & """ -c ""import certifi; print(certifi.where())"""
certPath = shell.Exec("cmd /c " & certPathCmd).StdOut.ReadLine

' --- 4️⃣ Set REQUESTS_CA_BUNDLE for this process ---
shell.Environment("PROCESS")("REQUESTS_CA_BUNDLE") = certPath

' --- 5️⃣ Build path to sync_lyrics.py relative to this script ---
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
syncLyricsPath = fso.BuildPath(scriptDir, "sync_lyrics.py")

' --- 6️⃣ Build final command ---
If debugMode Then
    ' Show console and do not hide output
    cmd = """" & pythonPath & """ """ & syncLyricsPath & """"
    shell.Run cmd, 1, True   ' 1 = normal window
Else
    ' Silent mode: use shell.Run directly instead of cmd /c
    cmd = """" & pythonPath & """ """ & syncLyricsPath & """"
    shell.Run cmd, 0, False  ' 0 = hidden
End If
