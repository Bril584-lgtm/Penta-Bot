' Launches the Penta-Bot watchdog with no visible window.
' A copy/shortcut of this file lives in the Startup folder so the bot starts at logon.
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""C:\Users\batma\Penta-Bot\watchdog.ps1""", 0, False
