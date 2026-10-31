@echo off
rem Owner forgot the password: set a new one ON THIS PC. Nothing else changes, no data is deleted.
cd /d "%~dp0"
if exist "python\python.exe" ( "python\python.exe" -X utf8 tools\shopctl.py owner-recovery ) else ( python -X utf8 tools\shopctl.py owner-recovery )
pause
