@echo off
setlocal

if exist "C:\Users\PC\AppData\Local\Programs\Python\Python313\python.exe" (
    "C:\Users\PC\AppData\Local\Programs\Python\Python313\python.exe" "%~dp0create_mirror_instance.py"
    goto :eof
)

where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0create_mirror_instance.py"
    goto :eof
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0create_mirror_instance.py"
    goto :eof
)

echo Python not found.
pause
