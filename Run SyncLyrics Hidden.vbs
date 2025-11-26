' SyncLyrics Launcher
' Double-click to run hidden (no console window)
' Run with /debug argument to show console output

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonScript = fso.BuildPath(scriptDir, "sync_lyrics.py")

' Check if the Python script exists
If Not fso.FileExists(pythonScript) Then
    MsgBox "Error: sync_lyrics.py not found in " & scriptDir, vbCritical, "SyncLyrics"
    WScript.Quit 1
End If

' Force working directory to script location
shell.CurrentDirectory = scriptDir

' Check debug arg
debugMode = False
If WScript.Arguments.Count > 0 Then
    If InStr(LCase(WScript.Arguments(0)), "debug") > 0 Then debugMode = True
End If

If debugMode Then
    ' Visible console
    shell.Run "cmd /k python """ & pythonScript & """", 1, False
Else
    ' Invisible - assumes pythonw is in PATH
    shell.Run "pythonw """ & pythonScript & """", 0, False
End If
