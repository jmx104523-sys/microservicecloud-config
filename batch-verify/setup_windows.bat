@echo off
chcp 65001 >nul
set "TARGET=D:\batch-verify"
set "SRC=%~dp0"

echo ========================================
echo   部署批量核验工具到 %TARGET%
echo ========================================
echo.

if not exist "%TARGET%" (
    mkdir "%TARGET%"
    echo 已创建目录 %TARGET%
)

copy /Y "%SRC%main.py" "%TARGET%\" >nul
copy /Y "%SRC%verifier.py" "%TARGET%\" >nul
copy /Y "%SRC%id_card.py" "%TARGET%\" >nul
copy /Y "%SRC%config.yaml" "%TARGET%\" >nul
copy /Y "%SRC%input.txt" "%TARGET%\" >nul
copy /Y "%SRC%requirements.txt" "%TARGET%\" >nul
copy /Y "%SRC%install.bat" "%TARGET%\" >nul
copy /Y "%SRC%run.bat" "%TARGET%\" >nul

echo 已复制以下文件到 %TARGET% :
echo   main.py
echo   verifier.py
echo   id_card.py
echo   config.yaml
echo   input.txt
echo   requirements.txt
echo   install.bat
echo   run.bat
echo.
echo 下一步：
echo   1. 编辑 %TARGET%\config.yaml 填入你的接口地址
echo   2. 编辑 %TARGET%\input.txt 填入测试数据
echo   3. 双击 %TARGET%\install.bat 安装依赖
echo   4. 双击 %TARGET%\run.bat 运行
echo.
pause
