' SyncLyrics Launcher
' Double-click to run hidden (no console window)
' Run with /debug argument to show console output

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Get the directory where this script is located
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonScript = fso.BuildPath(scriptDir, "sync_lyrics.py")

' Check if the Python script exists
If Not fso.FileExists(pythonScript) Then
    MsgBox "Error: sync_lyrics.py not found in " & scriptDir, vbCritical, "SyncLyrics"
    WScript.Quit 1
End If

' Check if debug mode is requested
debugMode = False
If WScript.Arguments.Count > 0 Then
    If LCase(WScript.Arguments(0)) = "/debug" Or LCase(WScript.Arguments(0)) = "-debug" Then
        debugMode = True
    End If
End If

' Build the Python command
If debugMode Then
    ' Debug mode - show console with output
    cmd = "cmd.exe /k ""title SyncLyrics Debug && python """ & pythonScript & """"""
    shell.Run cmd, 1, False  ' 1 = normal window, don't wait
Else
    ' Silent mode - completely hidden using pythonw.exe
    cmd = "pythonw.exe """ & pythonScript & """"
    shell.Run cmd, 0, False  ' 0 = hidden window, don't wait
End If
