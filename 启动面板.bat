@echo off
chcp 936 >nul
title 中医大脑面板
cd /d "%~dp0"

tasklist /fi "imagename eq python.exe" | find /i "python.exe" >nul
echo 正在启动面板...

start "" pyw "中医大脑面板.py"
timeout /t 4 >nul
start "" http://127.0.0.1:8020

echo.
echo   面板已启动： http://127.0.0.1:8020
echo   关掉那个黑窗口就是关面板。
echo.
timeout /t 3 >nul
