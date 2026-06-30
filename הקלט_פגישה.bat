@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ========================================
echo    מערכת סיכום פגישות - הקלטה חיה
echo ========================================
echo.
echo  1 = מיקרופון בלבד
echo  2 = מיקרופון + קול מערכת (לפגישות Zoom/Meet/Teams)
echo.
set /p mode="בחר (1/2): "
if "%mode%"=="2" (
    ".venv\Scripts\python.exe" -m src.app_cli --system
) else (
    ".venv\Scripts\python.exe" -m src.app_cli
)
echo.
pause
