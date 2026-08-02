@echo off
cd /d "%~dp0"
git add .
set /p msg="Commit message (Enter for default): "
if "%msg%"=="" set msg=update
git commit -m "%msg%"
git push
echo.
echo Done!
pause
