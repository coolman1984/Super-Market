@echo off
rem Connect THIS shop PC to the cloud copy (one time). You need the connection code (SMC2-...) from whoever sold/installed the program.
cd /d "%~dp0"
set /p CODE=Connection code (SMC2-...): 
if exist "python\python.exe" ( "python\python.exe" -X utf8 tools\syncctl.py join --code "%CODE%" ) else ( python -X utf8 tools\syncctl.py join --code "%CODE%" )
echo.
echo Now close the program and open it again. It will copy the shop to the cloud by itself.
pause
