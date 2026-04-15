@echo off
title JP Sourcing Server
cd /d %~dp0
echo ========================================
echo   JP Sourcing Server Starting...
echo   http://localhost:3002
echo ========================================
echo.
python app.py
pause
