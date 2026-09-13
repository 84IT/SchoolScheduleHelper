@echo off
chcp 65001 > nul
title Стартиране на Генератор за Контролни.

echo ====================================================
echo   Стартиране на Генератор за Контролни (МОН)
echo ====================================================
echo.

if not exist "venv\Scripts\activate.bat" goto NOVENV

echo [1/2] Активиране на виртуалната среда venv...
call venv\Scripts\activate.bat

echo [2/2] Стартиране на приложението в браузъра...
echo.
streamlit run app.py

pause
goto :EOF

:NOVENV
echo [ГРЕШКА] Папката venv не е намерена!
echo Моля, изпълнете първо install.bat
pause
goto :EOF
