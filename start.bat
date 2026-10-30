@echo off
rem Super market - start on this PC. No installation: uses the bundled python\python.exe,
rem otherwise an installed Python 3.9+. Pass "training" for the separate training shop,
rem "lan" to let other tills on the private network connect.
cd /d "%~dp0"
if exist "python\python.exe" (
  start "Super Market" /min "python\python.exe" -X utf8 launcher.py %*
  goto :eof
)
where py >nul 2>nul
if %errorlevel%==0 (
  start "Super Market" /min py -3 -X utf8 launcher.py %*
  goto :eof
)
start "Super Market" /min python -X utf8 launcher.py %*
