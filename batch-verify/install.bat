@echo off
chcp 65001 >nul
cd /d D:\batch-verify

echo 正在安装 Python 依赖...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo 安装失败，请确认已安装 Python 并添加到 PATH。
    pause
    exit /b 1
)

echo.
echo 依赖安装完成！
pause
