Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Get the directory where this script is located
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
powershellScript = fso.BuildPath(scriptDir, "run_synclyrics_hidden.ps1")

' Check if debug mode is requested
debugMode = False
If WScript.Arguments.Count > 0 Then
    If LCase(WScript.Arguments(0)) = "/debug" Then
        debugMode = True
    End If
End If

' Build the PowerShell command
If debugMode Then
    ' Debug mode - show output
    cmd = "powershell.exe -ExecutionPolicy Bypass -File """ & powershellScript & """ -Debug"
    shell.Run cmd, 1, True   ' 1 = normal window
Else
    ' Silent mode - completely hidden
    cmd = "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & powershellScript & """"
    shell.Run cmd, 0, False  ' 0 = hidden
End If
