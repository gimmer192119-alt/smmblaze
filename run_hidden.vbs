Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "powershell.exe -ExecutionPolicy Bypass -File ""D:\auto1\run_hidden.ps1""", 0
Set WshShell = Nothing
