@echo off
chcp 936 >nul
title 插件安装指引
echo ============================================
echo   中通客服智助 · 浏览器插件 安装步骤
echo ============================================
echo.
echo   第 1 步：打开 Chrome，地址栏输入 chrome://extensions 回车
echo   第 2 步：右上角打开「开发者模式」开关
echo   第 3 步：点「加载已解压的扩展程序」，选择本目录下的 extension 文件夹
echo   第 4 步：装好后，点插件里的「扩展程序选项」，把服务端密钥粘贴进去
echo.
echo   正在为你打开扩展管理页和插件文件夹...
echo.
pause
start chrome "chrome://extensions"
start "" explorer "%~dp0extension"
