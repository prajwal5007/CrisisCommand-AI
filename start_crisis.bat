@echo off
setlocal
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo Virtual environment not found.
    echo Create it with: py -m venv venv
    echo Then run: venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
call "venv\Scripts\activate.bat"
python -u run.py
echo.
echo CrisisCommand AI server stopped.
pause
