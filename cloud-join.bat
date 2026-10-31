@echo off
rem Connect THIS shop PC to the cloud copy (one time). You need the cloud address, shop id and a one-time code from the owner/admin.
cd /d "%~dp0"
set /p URL=Cloud address (https://...workers.dev): 
set /p STORE=Shop id: 
set /p CODE=One-time code: 
if exist "python\python.exe" ( "python\python.exe" -X utf8 tools\syncctl.py join --url %URL% --store %STORE% --code %CODE% ) else ( python -X utf8 tools\syncctl.py join --url %URL% --store %STORE% --code %CODE% )
echo.
echo Now close the program and open it again. It will copy the shop to the cloud by itself.
pause
