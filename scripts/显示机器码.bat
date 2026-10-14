@echo off
chcp 936 >nul
title 机器码
cd /d %~dp0..
echo.
echo   你的机器码（发给管理员用于远程更新登记）：
echo.
type config\machine_id 2>nul
echo.
echo   访问码：
type config\access_code.txt 2>nul
echo.
echo.
pause
