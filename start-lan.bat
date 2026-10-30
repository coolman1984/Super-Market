@echo off
rem Main shop PC only: lets other tills on the PRIVATE network open http://<this-pc-ip>:8765
rem Allow port 8765 in Windows Firewall for the Private profile only (see docs\WINDOWS.md).
call "%~dp0start.bat" lan
