@echo off
REM ============================================================
REM   Novel AI Fusion - Dev Manager
REM   File encoding: UTF-8 with BOM
REM   Platform: Windows 10/11
REM   Purpose: start / stop / status / log backend + frontend
REM ============================================================

REM 1) Force UTF-8 code page FIRST so cmd can echo CJK correctly.
chcp 65001 >nul

REM 2) Title (use a CJK-safe string)
title Novel AI Fusion - Dev Manager

REM 3) Enable extensions + delayed expansion
setlocal EnableExtensions EnableDelayedExpansion

REM ==================== CONFIG ====================
set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"

set "BACKEND_DIR=%PROJECT_ROOT%\backend"
set "FRONTEND_DIR=%PROJECT_ROOT%\frontend"

set "BACKEND_HOST=127.0.0.1"
set "BACKEND_PORT=8000"
set "FRONTEND_HOST=127.0.0.1"
set "FRONTEND_PORT=5173"

set "LOG_DIR=%PROJECT_ROOT%\.runlogs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM ==================== ANSI COLOR HELPERS ====================
for /F "tokens=1,2 delims=#" %%a in ('"prompt #$H#$E# & echo on & for %%b in (1) do rem"') do set "ESC=%%b"
set "GREEN=%ESC%[92m"
set "YELLOW=%ESC%[93m"
set "RED=%ESC%[91m"
set "CYAN=%ESC%[96m"
set "GRAY=%ESC%[90m"
set "RESET=%ESC%[0m"

REM ==================== HELPERS ====================
REM All subroutines below are ONLY reached via `call :name`; main flow must
REM skip over them by jumping straight to :main_start at the bottom of config.
goto :main_start

:print_banner
echo.
echo %CYAN%============================================================%RESET%
echo %CYAN%   Novel AI Fusion - Dev Manager%RESET%
echo %CYAN%============================================================%RESET%
echo   Backend  : http://%BACKEND_HOST%:%BACKEND_PORT%  (uvicorn)
echo   Frontend : http://%FRONTEND_HOST%:%FRONTEND_PORT%  (vite)
echo   API docs : http://%BACKEND_HOST%:%BACKEND_PORT%/docs
echo   Logs dir : %LOG_DIR%
echo %CYAN%------------------------------------------------------------%RESET%
goto :eof

:print_menu
echo.
echo %YELLOW%Choose action:%RESET%
echo   %GREEN%1)%RESET% start backend     (uvicorn :8000)
echo   %GREEN%2)%RESET% start frontend    (vite    :5173)
echo   %GREEN%3)%RESET% start BOTH
echo   %GREEN%4)%RESET% stop backend
echo   %GREEN%5)%RESET% stop frontend
echo   %GREEN%6)%RESET% stop BOTH
echo   %GREEN%7)%RESET% status           (port + pid + HTTP /health)
echo   %GREEN%8)%RESET% tail logs
echo   %GREEN%9)%RESET% restart BOTH
echo   %GREEN%0)%RESET% exit
echo.
set /p "CHOICE=  Your choice [0-9]: "
goto :eof

REM ---------- PID lookup by LISTENING port ----------
:find_pids_by_port
set "PORT_TO_CHECK=%~1"
set "RESULT_PIDS="
REM Match :PORT followed by a space (both IPv4 "127.0.0.1:5173 " and
REM IPv6 "[::1]:5173 ").  Anchoring on the trailing space prevents ":8000"
REM from matching ":80000" or similar.
for /F "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT_TO_CHECK% " ^| findstr LISTENING') do (
    if not defined RESULT_PIDS (set "RESULT_PIDS=%%P") else (set "RESULT_PIDS=!RESULT_PIDS! %%P")
)
goto :eof

:kill_pid
set "PID_TO_KILL=%~1"
taskkill /F /PID %PID_TO_KILL% >nul 2>&1
goto :eof

REM ---------- status panel ----------
:print_status
echo.
echo %CYAN%[Port / PID status]%RESET%
echo %GRAY%------------------------------------------------------------%RESET%

call :find_pids_by_port %BACKEND_PORT%
set "BE_PIDS=!RESULT_PIDS!"
if defined BE_PIDS (
    echo   backend  :%BACKEND_PORT%   %GREEN%RUNNING%RESET%   PIDs: !BE_PIDS!
) else (
    echo   backend  :%BACKEND_PORT%   %GRAY%stopped%RESET%
)

call :find_pids_by_port %FRONTEND_PORT%
set "FE_PIDS=!RESULT_PIDS!"
if defined FE_PIDS (
    echo   frontend :%FRONTEND_PORT%   %GREEN%RUNNING%RESET%   PIDs: !FE_PIDS!
) else (
    echo   frontend :%FRONTEND_PORT%   %GRAY%stopped%RESET%
)
echo %GRAY%------------------------------------------------------------%RESET%

REM HTTP /health check (PowerShell; returns 0 regardless of HTTP result, so we
REM detect failure from the catch branch's own output, not from errorlevel).
REM Trick: embed bat-expanded ANSI codes INTO the PowerShell command string
REM (single-quoted ' ' so PowerShell does not re-interpret), so $env:... is
REM never needed and the literal ESC characters reach the terminal.
echo %CYAN%[HTTP /health]%RESET%
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-WebRequest -Uri 'http://%BACKEND_HOST%:%BACKEND_PORT%/health' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -lt 400) { Write-Host ('  backend /health   %GREEN%OK    %RESET% status=' + $r.StatusCode) } else { Write-Host ('  backend /health   %YELLOW%HTTP%RESET% status=' + $r.StatusCode) } } catch { Write-Host ('  backend /health   %RED%FAIL%RESET% ' + $_.Exception.Message) }" 2>&1
if errorlevel 1 (
    echo   backend /health   %RED%FAIL%RESET%   PowerShell error
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-WebRequest -Uri 'http://%FRONTEND_HOST%:%FRONTEND_PORT%' -UseBasicParsing -TimeoutSec 3; Write-Host ('  frontend /        %GREEN%OK    %RESET% status=' + $r.StatusCode) } catch { Write-Host ('  frontend /        %YELLOW%N/A   %RESET% vite may still be compiling') }" 2>&1
if errorlevel 1 (
    echo   frontend /        %YELLOW%N/A%RESET%   PowerShell error
)
echo %GRAY%------------------------------------------------------------%RESET%
goto :eof

REM ==================== START ====================

:start_backend
call :find_pids_by_port %BACKEND_PORT%
if defined RESULT_PIDS (
    echo %YELLOW%[backend already running]%RESET% PIDs: !RESULT_PIDS!
    goto :eof
)
if not exist "%BACKEND_DIR%" (
    echo %RED%[error]%RESET% backend dir missing: %BACKEND_DIR%
    goto :eof
)
echo %GREEN%[starting backend]%RESET%  uvicorn  :%BACKEND_PORT%  --^>  %LOG_DIR%\backend.log
REM Write a tiny launcher .cmd to avoid quoting hell inside `start ... cmd /c`.
set "BE_LAUNCHER=%LOG_DIR%\_start_backend.cmd"
>  "%BE_LAUNCHER%" echo @echo off
>> "%BE_LAUNCHER%" echo chcp 65001 ^>nul
>> "%BE_LAUNCHER%" echo cd /d "%BACKEND_DIR%"
>> "%BE_LAUNCHER%" echo python -m uvicorn app.main:app --host %BACKEND_HOST% --port %BACKEND_PORT% --reload ^> "%LOG_DIR%\backend.log" 2^>^&1
cd /d "%BACKEND_DIR%"
start "novelai-backend" /B cmd /c "%BE_LAUNCHER%"
ping -n 5 127.0.0.1 >nul
call :find_pids_by_port %BACKEND_PORT%
if defined RESULT_PIDS (
    echo %GREEN%[backend up]%RESET% PIDs: !RESULT_PIDS!
) else (
    echo %RED%[backend start failed]%RESET% see: %LOG_DIR%\backend.log
)
goto :eof

:start_frontend
call :find_pids_by_port %FRONTEND_PORT%
if defined RESULT_PIDS (
    echo %YELLOW%[frontend already running]%RESET% PIDs: !RESULT_PIDS!
    goto :eof
)
if not exist "%FRONTEND_DIR%" (
    echo %RED%[error]%RESET% frontend dir missing: %FRONTEND_DIR%
    goto :eof
)
echo %GREEN%[starting frontend]%RESET%  vite     :%FRONTEND_PORT%  --^>  %LOG_DIR%\frontend.log
REM Write a tiny launcher .cmd to avoid quoting hell inside `start ... cmd /c`.
set "FE_LAUNCHER=%LOG_DIR%\_start_frontend.cmd"
>  "%FE_LAUNCHER%" echo @echo off
>> "%FE_LAUNCHER%" echo chcp 65001 ^>nul
>> "%FE_LAUNCHER%" echo cd /d "%FRONTEND_DIR%"
>> "%FE_LAUNCHER%" echo npm run dev -- --host %FRONTEND_HOST% --port %FRONTEND_PORT% ^> "%LOG_DIR%\frontend.log" 2^>^&1
cd /d "%FRONTEND_DIR%"
start "novelai-frontend" /B cmd /c "%FE_LAUNCHER%"
echo   waiting for vite to compile...
ping -n 6 127.0.0.1 >nul
call :find_pids_by_port %FRONTEND_PORT%
if defined RESULT_PIDS (
    echo %GREEN%[frontend up]%RESET% PIDs: !RESULT_PIDS!
) else (
    echo %YELLOW%[frontend still booting]%RESET% waiting another 5s...
    ping -n 6 127.0.0.1 >nul
    call :find_pids_by_port %FRONTEND_PORT%
    if defined RESULT_PIDS (echo %GREEN%[frontend up]%RESET% PIDs: !RESULT_PIDS!) else (echo %RED%[frontend start failed]%RESET% see: %LOG_DIR%\frontend.log)
)
goto :eof

REM ==================== STOP ====================

:stop_backend
call :find_pids_by_port %BACKEND_PORT%
if not defined RESULT_PIDS (
    echo %GRAY%[backend not running]%RESET%
    goto :eof
)
echo %YELLOW%[stopping backend]%RESET%  PIDs: !RESULT_PIDS!
for %%P in (!RESULT_PIDS!) do call :kill_pid %%P
ping -n 2 127.0.0.1 >nul
call :find_pids_by_port %BACKEND_PORT%
if not defined RESULT_PIDS (echo %GREEN%[backend stopped]%RESET%) else (echo %RED%[stop failed]%RESET%)
goto :eof

:stop_frontend
call :find_pids_by_port %FRONTEND_PORT%
if not defined RESULT_PIDS (
    echo %GRAY%[frontend not running]%RESET%
    goto :eof
)
echo %YELLOW%[stopping frontend]%RESET%  PIDs: !RESULT_PIDS!
for %%P in (!RESULT_PIDS!) do call :kill_pid %%P
REM Vite spawns node child; clean up orphans on 5173 port
ping -n 2 127.0.0.1 >nul
call :find_pids_by_port %FRONTEND_PORT%
if not defined RESULT_PIDS (
    echo %GREEN%[frontend stopped]%RESET%
) else (
    echo %YELLOW%[lingering node, force killing]%RESET%
    for %%P in (!RESULT_PIDS!) do call :kill_pid %%P
)
goto :eof

:stop_all
call :stop_backend
call :stop_frontend
echo %GREEN%[all stopped]%RESET%
goto :eof

REM ==================== LOGS ====================

:tail_logs
echo %CYAN%[live log - Ctrl+C to stop]%RESET%
echo %GRAY%------------------------------------------------------------%RESET%
if exist "%LOG_DIR%\backend.log" (
    echo %YELLOW%== %LOG_DIR%\backend.log ==%RESET%
) else (
    echo %GRAY%(no backend.log yet)%RESET%
)
if exist "%LOG_DIR%\frontend.log" (
    echo %YELLOW%== %LOG_DIR%\frontend.log ==%RESET%
) else (
    echo %GRAY%(no frontend.log yet)%RESET%
)
echo %GRAY%------------------------------------------------------------%RESET%
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content '%LOG_DIR%\backend.log','%LOG_DIR%\frontend.log' -Wait -Encoding UTF8 -Tail 20 -ErrorAction SilentlyContinue"
goto :eof

REM ==================== MAIN LOOP ====================

:main_start
REM CLI mode: dev.bat <arg> -> run that action once and exit.
REM Useful for non-interactive use (e.g. CI, scheduled tasks, quick checks).
if not "%~1"=="" (
    if /I "%~1"=="start-backend"   call :start_backend  & goto :exit_script
    if /I "%~1"=="start-frontend"  call :start_frontend & goto :exit_script
    if /I "%~1"=="start-all"       call :start_backend  & call :start_frontend & goto :exit_script
    if /I "%~1"=="stop-backend"    call :stop_backend   & goto :exit_script
    if /I "%~1"=="stop-frontend"   call :stop_frontend  & goto :exit_script
    if /I "%~1"=="stop-all"        call :stop_all       & goto :exit_script
    if /I "%~1"=="status"          call :print_banner   & call :print_status   & goto :exit_script
    if /I "%~1"=="restart-all"     call :stop_all & ping -n 3 127.0.0.1 ^>nul & call :start_backend & call :start_frontend & goto :exit_script
    if /I "%~1"=="help"            goto :print_help
    echo %RED%[error]%RESET% unknown CLI arg: %~1
    goto :print_help
)

REM No CLI arg: fall through to interactive menu. (Don't fall through to
REM :print_help label below - that would just print Usage and exit.)
goto :menu_loop

:print_help
echo.
echo %CYAN%Usage:%RESET%
echo   dev.bat                       interactive menu
echo   dev.bat start-backend         start uvicorn :8000
echo   dev.bat start-frontend        start vite    :5173
echo   dev.bat start-all             start both
echo   dev.bat stop-backend          kill 8000 listener
echo   dev.bat stop-frontend         kill 5173 listener
echo   dev.bat stop-all              kill both
echo   dev.bat restart-all           stop then start both
echo   dev.bat status                print port / pid / /health
echo   dev.bat help                  show this
goto :exit_script

:menu_loop
call :print_banner
call :print_menu
if "%CHOICE%"=="" set "CHOICE=0"

if "%CHOICE%"=="1" call :start_backend
if "%CHOICE%"=="2" call :start_frontend
if "%CHOICE%"=="3" (
    call :start_backend
    call :start_frontend
)
if "%CHOICE%"=="4" call :stop_backend
if "%CHOICE%"=="5" call :stop_frontend
if "%CHOICE%"=="6" call :stop_all
if "%CHOICE%"=="7" call :print_status
if "%CHOICE%"=="8" call :tail_logs
if "%CHOICE%"=="9" (
    call :stop_all
    ping -n 3 127.0.0.1 >nul
    call :start_backend
    call :start_frontend
)

if "%CHOICE%"=="0" goto :exit_script

echo.
pause
goto :menu_loop

:exit_script
endlocal
echo.
echo %CYAN%Bye.%RESET%
exit /b 0
