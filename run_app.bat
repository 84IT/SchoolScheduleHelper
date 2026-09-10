@echo off
chcp 65001 > nul
title Стартиране на Генератор за Контролни.

echo ====================================================
echo   Стартиране на Генератор за Контролни (МОН)
echo ====================================================
echo.

if exist "venv\Scripts\activate.bat" (
    echo [1/2] Активиране на виртуалната среда (venv)...
    call venv\Scripts\activate.bat
) else (
    echo [ГРЕШКА] Папката 'venv' не е намерена! 
    echo Моля, създайте я с: python -m venv venv
    pause
    exit /b
)

echo [2/2] Стартиране на приложението в браузъра...
echo.
streamlit run app.py

pause
