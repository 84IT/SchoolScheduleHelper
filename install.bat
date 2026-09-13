@echo off
chcp 65001 > nul
title Инсталация на Генератор за Контролни.

echo ====================================================
echo   Инсталация на Генератор за Контролни (МОН)
echo ====================================================
echo.

if exist "venv\Scripts\activate.bat" goto HAVEVENV

echo [1/3] Виртуална среда не е намерена - създавам venv...
python -m venv venv
if errorlevel 1 goto VENVFAIL
goto ACTIVATE

:HAVEVENV
echo [1/3] Виртуална среда venv вече съществува.

:ACTIVATE
echo [2/3] Активиране на виртуалната среда venv...
call venv\Scripts\activate.bat
if errorlevel 1 goto ACTIVATEFAIL

echo [3/3] Инсталиране на пакети от requirements.txt...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 goto PIPFAIL

echo.
echo ====================================================
echo   Готово! Използвайте run_app.bat, за да стартирате.
echo ====================================================
pause
goto :EOF

:VENVFAIL
echo.
echo [ГРЕШКА] Не успях да създам виртуална среда.
echo Проверете дали Python е инсталиран и наличен в PATH.
pause
exit /b 1

:ACTIVATEFAIL
echo.
echo [ГРЕШКА] Не успях да активирам виртуалната среда.
pause
exit /b 1

:PIPFAIL
echo.
echo [ГРЕШКА] Инсталирането на пакети се провали. Вижте съобщенията по-горе.
pause
exit /b 1
