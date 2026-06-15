@echo off
chcp 65001 >nul
cd /d D:\batch-verify

echo 运行批量核验...
echo 输入文件: D:\batch-verify\input.txt
echo 输出目录: D:\batch-verify\
echo.

python main.py
echo.
pause
