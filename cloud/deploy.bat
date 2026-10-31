@echo off
rem Double-click to put the Super Market cloud backup on YOUR Cloudflare account (first time it opens a browser to log in).
cd /d "%~dp0"
where node >nul 2>nul || (echo Install Node.js LTS first: https://nodejs.org & pause & exit /b 1)
if "%CLOUDFLARE_API_TOKEN%"=="" call npx --yes wrangler@4.45.0 whoami 2>nul | findstr /i "associated" >nul || call npx --yes wrangler@4.45.0 login
node deploy.mjs
pause
