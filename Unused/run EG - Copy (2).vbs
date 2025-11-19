' Set the SSL certificate path
strSSLPath = "C:\Users\Anshul\AppData\Local\Programs\Python\Python311\Lib\site-packages\certifi\cacert.pem"
CreateObject("WScript.Shell").Environment("PROCESS")("REQUESTS_CA_BUNDLE") = strSSLPath

' Build the command
strCommand = "cmd /c pythonw.exe sync_lyrics.py"
' Hide the window completely and redirect output
strCommand = strCommand & " >nul 2>&1"
CreateObject("Wscript.Shell").Run strCommand, 0, False