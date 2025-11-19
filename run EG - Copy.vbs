strCommand = "cmd /c pythonw.exe sync_lyrics.py"
' Hide the window completely and redirect output
strCommand = strCommand & " >nul 2>&1"
CreateObject("Wscript.Shell").Run strCommand, 0, False