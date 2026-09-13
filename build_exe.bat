@echo off
chcp 65001 > nul
title Build EXE.

echo ====================================================
echo   Изграждане на преносим .exe (PyInstaller)
echo ====================================================
echo.

if exist "venv\Scripts\activate.bat" goto ACTIVATE
echo [ГРЕШКА] Папката venv не е намерена!
echo Моля, изпълнете първо install.bat
pause
goto :EOF

:ACTIVATE
call venv\Scripts\activate.bat
if errorlevel 1 goto ACTIVATEFAIL

echo [1/2] Инсталиране на PyInstaller...
pip install pyinstaller
if errorlevel 1 goto PIPFAIL

echo [2/2] Изграждане на .exe - onedir режим (пази данните между стартиранията)...
pyinstaller --onedir --noconfirm --name MathExamPlanner ^
  --add-data "app.py;." ^
  --add-data "calendar_2026_2027.json;." ^
  --collect-all streamlit ^
  --collect-all docx ^
  --collect-all pandas ^
  --collect-all openpyxl ^
  --copy-metadata streamlit ^
  --copy-metadata pandas ^
  launcher.py
if errorlevel 1 goto BUILDFAIL

echo.
echo ====================================================
echo   Готово! Преносимото приложение е в dist\MathExamPlanner
echo   Копирайте ЦЯЛАТА папка dist\MathExamPlanner - тя Е
echo   преносимото приложение. Стартира се с MathExamPlanner.exe
echo   вътре в нея.
echo ====================================================
pause
goto :EOF

:ACTIVATEFAIL
echo.
echo [ГРЕШКА] Не успях да активирам виртуалната среда.
pause
exit /b 1

:PIPFAIL
echo.
echo [ГРЕШКА] Инсталирането на PyInstaller се провали.
pause
exit /b 1

:BUILDFAIL
echo.
echo [ГРЕШКА] Изграждането на .exe се провали. Вижте съобщенията по-горе.
pause
exit /b 1
