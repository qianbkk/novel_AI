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
set "BACKEND_PORT=8132"
set "FRONTEND_HOST=127.0.0.1"
set "FRONTEND_PORT=5293"

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
echo   %GREEN%1)%RESET% start backend     (uvicorn :8132)
echo   %GREEN%2)%RESET% start frontend    (vite    :5293)
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
REM Match :PORT followed by a space (both IPv4 "127.0.0.1:5293 " and
REM IPv6 "[::1]:5293 ").  Anchoring on the trailing space prevents ":8123"
REM from matching ":81230" or similar.
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
echo %CYAN%[Snapshot]%RESET%
echo %GRAY%------------------------------------------------------------%RESET%

call :find_pids_by_port %BACKEND_PORT%
set "BE_PIDS=!RESULT_PIDS!"
if defined BE_PIDS (
    echo   backend  :%BACKEND_PORT%    %GREEN%running%RESET%   PIDs: !BE_PIDS!
    set "BE_UP=1"
) else (
    echo   backend  :%BACKEND_PORT%    %GRAY%stopped%RESET%
    set "BE_UP=0"
)

call :find_pids_by_port %FRONTEND_PORT%
set "FE_PIDS=!RESULT_PIDS!"
if defined FE_PIDS (
    echo   frontend :%FRONTEND_PORT%    %GREEN%running%RESET%   PIDs: !FE_PIDS!
    set "FE_UP=1"
) else (
    echo   frontend :%FRONTEND_PORT%    %GRAY%stopped%RESET%
    set "FE_UP=0"
)
echo %GRAY%------------------------------------------------------------%RESET%

REM HTTP /health check (only when the port is listening; otherwise it is
REM obviously unreachable and we skip the call to avoid a 3s timeout per row).
echo %CYAN%[HTTP probe]%RESET%
if "%BE_UP%"=="1" goto :_probe_be
echo   backend /health    %GRAY%skipped (not listening)%RESET%
goto :_probe_be_end
:_probe_be
call :http_probe "backend /health    " "http://%BACKEND_HOST%:%BACKEND_PORT%/health" tri
:_probe_be_end

if "%FE_UP%"=="1" goto :_probe_fe
echo   frontend /         %GRAY%skipped (not listening)%RESET%
goto :_probe_fe_end
:_probe_fe
call :http_probe "frontend /         " "http://%FRONTEND_HOST%:%FRONTEND_PORT%/" bin
:_probe_fe_end
echo %GRAY%------------------------------------------------------------%RESET%
echo.
call :_hint
goto :eof

REM ---------- HTTP probe (tri: backend /health 3-state; bin: frontend / binary) ----------
:http_probe
set "LABEL=%~1"
set "URL=%~2"
set "MODE=%~3"
if /I "%MODE%"=="tri" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-WebRequest -Uri '%URL%' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -lt 400) { Write-Host ('  %LABEL% %GREEN%ok%RESET%      status=' + $r.StatusCode) } else { Write-Host ('  %LABEL% %YELLOW%http' + $r.StatusCode + '%RESET%') } } catch { Write-Host ('  %LABEL% %RED%fail%RESET%   ' + $_.Exception.Message) }" 2>&1
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-WebRequest -Uri '%URL%' -UseBasicParsing -TimeoutSec 3; Write-Host ('  %LABEL% %GREEN%ok%RESET%      status=' + $r.StatusCode) } catch { Write-Host ('  %LABEL% %YELLOW%n/a%RESET%     ' + $_.Exception.Message) }" 2>&1
)
goto :eof

REM ---------- smart hint (consumes BE_UP/FE_UP set by :print_status) ----------
:_hint
if "%BE_UP%%FE_UP%"=="00" (
    echo %YELLOW%hint%RESET%  both stopped. Press %GREEN%3%RESET% from the menu, or run:  dev.bat start-all
    goto :eof
)
if "%BE_UP%"=="0" (
    echo %YELLOW%hint%RESET%  backend is down but frontend is up. The web UI will not be able to reach the API.
    echo          start it with menu option %GREEN%1%RESET%, or:  dev.bat start-backend
    goto :eof
)
if "%FE_UP%"=="0" (
    echo %YELLOW%hint%RESET%  backend is up but frontend is down. You can hit the API directly at http://%BACKEND_HOST%:%BACKEND_PORT%/docs
    echo          start the UI with menu option %GREEN%2%RESET%, or:  dev.bat start-frontend
)
goto :eof

REM ==================== START ====================

REM ---------- write a launcher .cmd (avoids quoting hell inside `start ... cmd /c`) ----------
:_write_launcher
set "LAUNCHER_PATH=%~1"
set "TARGET_DIR=%~2"
set "LOG_PATH=%~3"
set "CMD_LINE=%~4"
>  "%LAUNCHER_PATH%" echo @echo off
>> "%LAUNCHER_PATH%" echo chcp 65001 ^>nul
>> "%LAUNCHER_PATH%" echo cd /d "%TARGET_DIR%"
if "%ADD_BLANK%"=="1" >> "%LAUNCHER_PATH%" echo echo. ^>^> "%LOG_PATH%"
>> "%LAUNCHER_PATH%" echo echo ==== start at %DATE% %TIME% ==== ^>^> "%LOG_PATH%"
>> "%LAUNCHER_PATH%" echo %CMD_LINE% ^>^> "%LOG_PATH%" 2^>^&1
goto :eof

REM ---------- generic app starter (driven by env vars set by the thunk caller) ----------
:launch_app
set "LAST_RESULT=ok"
set "LAUNCHER=%LOG_DIR%\_start_%LOG_BASENAME%.cmd"
call :find_pids_by_port %APP_PORT%
if defined RESULT_PIDS (
    echo %YELLOW%[!APP_NAME! already running]%RESET% PIDs: !RESULT_PIDS!
    goto :eof
)
if not exist "%APP_DIR%" (
    echo %RED%[error]%RESET% !APP_NAME! dir missing: !APP_DIR!
    set "LAST_RESULT=fail"
    goto :eof
)
echo %GREEN%[starting !APP_NAME!]%RESET%  !TOOL_NAME!  :%APP_PORT%  --^>^>  %LOG_DIR%\!LOG_BASENAME!.log
call :_write_launcher "%LAUNCHER%" "%APP_DIR%" "%LOG_DIR%\!LOG_BASENAME!.log" "%CMD_LINE%"
cd /d "%APP_DIR%"
start "%WINDOW_TITLE%" /B cmd /c "%LAUNCHER%"
ping -n %START_WAIT% 127.0.0.1 >nul
call :find_pids_by_port %APP_PORT%
if defined RESULT_PIDS goto :_launch_app_report_up
if "%BOOT_RETRY%"=="1" (
    echo %YELLOW%[!APP_NAME! still booting]%RESET% waiting another !BOOT_MSG!s...
    ping -n %BOOT_WAIT% 127.0.0.1 >nul
    call :find_pids_by_port %APP_PORT%
    if defined RESULT_PIDS goto :_launch_app_report_up
)
echo %RED%[!APP_NAME! start failed]%RESET% see: %LOG_DIR%\!LOG_BASENAME!.log
set "LAST_RESULT=fail"
goto :eof
:_launch_app_report_up
echo %GREEN%[!APP_NAME! up]%RESET% PIDs: !RESULT_PIDS!    !UP_URL!
goto :eof

:start_backend
set "APP_NAME=backend"
set "APP_HOST=%BACKEND_HOST%"
set "APP_PORT=%BACKEND_PORT%"
set "APP_DIR=%BACKEND_DIR%"
set "TOOL_NAME=uvicorn"
set "UP_URL=http://%BACKEND_HOST%:%BACKEND_PORT%/docs"
set "LOG_BASENAME=backend"
set "WINDOW_TITLE=novelai-backend"
set "START_WAIT=5"
set "BOOT_RETRY=0"
set "BOOT_WAIT=0"
set "BOOT_MSG=0"
set "ADD_BLANK=1"
set "CMD_LINE=python -m uvicorn app.main:app --host %BACKEND_HOST% --port %BACKEND_PORT% --reload"
call :launch_app
goto :eof

:start_frontend
set "APP_NAME=frontend"
set "APP_HOST=%FRONTEND_HOST%"
set "APP_PORT=%FRONTEND_PORT%"
set "APP_DIR=%FRONTEND_DIR%"
set "TOOL_NAME=vite"
set "UP_URL=http://%FRONTEND_HOST%:%FRONTEND_PORT%/"
set "LOG_BASENAME=frontend"
set "WINDOW_TITLE=novelai-frontend"
set "START_WAIT=6"
set "BOOT_RETRY=1"
set "BOOT_WAIT=6"
set "BOOT_MSG=5"
set "ADD_BLANK=0"
set "CMD_LINE=npm run dev -- --host %FRONTEND_HOST% --port %FRONTEND_PORT%"
call :launch_app
goto :eof

REM ==================== STOP ====================

:stop_port
set "APP_NAME=%~1"
set "APP_PORT=%~2"
set "ORPHAN_RETRY=%~3"
set "LAST_RESULT=ok"
call :find_pids_by_port %APP_PORT%
if not defined RESULT_PIDS (
    echo %GRAY%[!APP_NAME! not running]%RESET%
    goto :eof
)
echo %YELLOW%[stopping !APP_NAME!]%RESET%  PIDs: !RESULT_PIDS!
for %%P in (!RESULT_PIDS!) do call :kill_pid %%P
ping -n 2 127.0.0.1 >nul
call :find_pids_by_port %APP_PORT%
if not defined RESULT_PIDS (
    echo %GREEN%[!APP_NAME! stopped]%RESET%
    goto :eof
)
if /I not "%ORPHAN_RETRY%"=="1" (
    echo %RED%[stop failed]%RESET%   PIDs still on :%APP_PORT%: !RESULT_PIDS!
    set "LAST_RESULT=fail"
    goto :eof
)
echo %YELLOW%[lingering node, force killing]%RESET%
for %%P in (!RESULT_PIDS!) do call :kill_pid %%P
ping -n 2 127.0.0.1 >nul
call :find_pids_by_port %APP_PORT%
if defined RESULT_PIDS (
    echo %RED%[stop failed]%RESET%   PIDs still on :%APP_PORT%: !RESULT_PIDS!
    set "LAST_RESULT=fail"
) else (
    echo %GREEN%[!APP_NAME! stopped]%RESET%
)
goto :eof

:stop_backend
call :stop_port backend %BACKEND_PORT% 0
goto :eof

:stop_frontend
call :stop_port frontend %FRONTEND_PORT% 1
goto :eof

:stop_all
set "LAST_RESULT=ok"
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
echo   dev.bat start-backend         start uvicorn :8132
echo   dev.bat start-frontend        start vite    :5293
echo   dev.bat start-all             start both
echo   dev.bat stop-backend          kill 8132 listener
echo   dev.bat stop-frontend         kill 5293 listener
echo   dev.bat stop-all              kill both
echo   dev.bat restart-all           stop then start both
echo   dev.bat status                print port / pid / /health
echo   dev.bat help                  show this
goto :exit_script

:menu_loop
call :print_banner
call :print_menu
if "%CHOICE%"=="" set "CHOICE=0"

set "LAST_RESULT=ok"

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

REM Only pause when the last action failed; otherwise the user wants to chain
REM the next action (e.g. start-backend, then start-frontend) without a wait.
if /I not "%LAST_RESULT%"=="ok" (
    echo.
    pause
)
goto :menu_loop

:exit_script
endlocal
echo.
echo %CYAN%Bye.%RESET%
exit /b 0
