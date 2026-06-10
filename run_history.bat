@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "LOG_FILE=%SCRIPT_DIR%sync_history.log"
set "TIMESTAMP=%date% %time%"

echo ============================================================
echo  Aptem Sync - Run History
echo ============================================================
echo.

if "%1"=="run" goto :do_run
if "%1"=="history" goto :show_history
if "%1"=="clear" goto :clear_history

echo Usage:
echo   run_history.bat run        - Run the sync and log the result
echo   run_history.bat history    - Show the run history log
echo   run_history.bat clear      - Clear the run history log
echo.
echo   Running without arguments shows this help message.
goto :eof

:do_run
echo [%TIMESTAMP%] Starting sync...
echo.
cd /d "%SCRIPT_DIR%"
python manage.py sync_aptem > "%TEMP%\aptem_run_output.tmp" 2>&1
set "EXIT_CODE=!errorlevel!"

type "%TEMP%\aptem_run_output.tmp"

set "STATUS=SUCCESS"
if !EXIT_CODE! NEQ 0 set "STATUS=FAILED"

echo. >> "%LOG_FILE%"
echo ------------------------------------------------------------ >> "%LOG_FILE%"
echo Run: %TIMESTAMP% >> "%LOG_FILE%"
echo Status: !STATUS! >> "%LOG_FILE%"
type "%TEMP%\aptem_run_output.tmp" >> "%LOG_FILE%"
echo ------------------------------------------------------------ >> "%LOG_FILE%"

echo.
echo Result logged to: %LOG_FILE%
goto :eof

:show_history
if not exist "%LOG_FILE%" (
    echo No history found yet. Run "run_history.bat run" first.
    goto :eof
)
echo Log file: %LOG_FILE%
echo.
type "%LOG_FILE%"
goto :eof

:clear_history
if not exist "%LOG_FILE%" (
    echo No history file to clear.
    goto :eof
)
del "%LOG_FILE%"
echo History cleared.
goto :eof
