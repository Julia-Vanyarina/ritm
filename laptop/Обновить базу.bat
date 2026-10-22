@echo off
chcp 65001 >nul
cd /d "%~dp0"
py ritm_to_dbeaver.py %1
echo.
pause
