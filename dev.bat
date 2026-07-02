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
if "%BE_UP%"=="1" goto :_be_probe_on
echo   backend /health    %GRAY%skipped (not listening)%RESET%
goto :_be_probe_end
:_be_probe_on
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-WebRequest -Uri 'http://%BACKEND_HOST%:%BACKEND_PORT%/health' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -lt 400) { Write-Host ('  backend /health    %GREEN%ok%RESET%      status=' + $r.StatusCode) } else { Write-Host ('  backend /health    %YELLOW%http' + $r.StatusCode + '%RESET%') } } catch { Write-Host ('  backend /health    %RED%fail%RESET%   ' + $_.Exception.Message) }" 2>&1
:_be_probe_end

if "%FE_UP%"=="1" goto :_fe_probe_on
echo   frontend /         %GRAY%skipped (not listening)%RESET%
goto :_fe_probe_end
:_fe_probe_on
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-WebRequest -Uri 'http://%FRONTEND_HOST%:%FRONTEND_PORT%' -UseBasicParsing -TimeoutSec 3; Write-Host ('  frontend /         %GREEN%ok%RESET%      status=' + $r.StatusCode) } catch { Write-Host ('  frontend /         %YELLOW%n/a%RESET%     ' + $_.Exception.Message) }" 2>&1
:_fe_probe_end
echo %GRAY%------------------------------------------------------------%RESET%

REM Smart hint: if both down, suggest the single command to start everything.
if "%BE_UP%%FE_UP%"=="00" goto :_hint_both_down
if "%BE_UP%"=="0" goto :_hint_be_down
if "%FE_UP%"=="0" goto :_hint_fe_down
goto :eof

:_hint_both_down
echo %YELLOW%hint%RESET%  both stopped. Press %GREEN%3%RESET% from the menu, or run:  dev.bat start-all
goto :eof

:_hint_be_down
echo %YELLOW%hint%RESET%  backend is down but frontend is up. The web UI will not be able to reach the API.
echo          start it with menu option %GREEN%1%RESET%, or:  dev.bat start-backend
goto :eof

:_hint_fe_down
echo %YELLOW%hint%RESET%  backend is up but frontend is down. You can hit the API directly at http://%BACKEND_HOST%:%BACKEND_PORT%/docs
echo          start the UI with menu option %GREEN%2%RESET%, or:  dev.bat start-frontend
goto :eof

REM ==================== START ====================

:start_backend
set "LAST_RESULT=ok"
call :find_pids_by_port %BACKEND_PORT%
if defined RESULT_PIDS (
    echo %YELLOW%[backend already running]%RESET% PIDs: !RESULT_PIDS!
    goto :eof
)
if not exist "%BACKEND_DIR%" (
    echo %RED%[error]%RESET% backend dir missing: %BACKEND_DIR%
    set "LAST_RESULT=fail"
    goto :eof
)
echo %GREEN%[starting backend]%RESET%  uvicorn  :%BACKEND_PORT%  --^>^>  %LOG_DIR%\backend.log
REM Write a tiny launcher .cmd to avoid quoting hell inside `start ... cmd /c`.
REM Use >> (append) instead of > (truncate) so a leftover writer on backend.log
REM (e.g. a ghost uvicorn from a previous session) does not block us.
set "BE_LAUNCHER=%LOG_DIR%\_start_backend.cmd"
>  "%BE_LAUNCHER%" echo @echo off
>> "%BE_LAUNCHER%" echo chcp 65001 ^>nul
>> "%BE_LAUNCHER%" echo cd /d "%BACKEND_DIR%"
>> "%BE_LAUNCHER%" echo echo. ^>^> "%LOG_DIR%\backend.log"
>> "%BE_LAUNCHER%" echo echo ==== start at %DATE% %TIME% ==== ^>^> "%LOG_DIR%\backend.log"
>> "%BE_LAUNCHER%" echo python -m uvicorn app.main:app --host %BACKEND_HOST% --port %BACKEND_PORT% --reload ^>^> "%LOG_DIR%\backend.log" 2^>^&1
cd /d "%BACKEND_DIR%"
start "novelai-backend" /B cmd /c "%BE_LAUNCHER%"
ping -n 5 127.0.0.1 >nul
call :find_pids_by_port %BACKEND_PORT%
if defined RESULT_PIDS (
    echo %GREEN%[backend up]%RESET% PIDs: !RESULT_PIDS!    http://%BACKEND_HOST%:%BACKEND_PORT%/docs
) else (
    echo %RED%[backend start failed]%RESET% see: %LOG_DIR%\backend.log
    set "LAST_RESULT=fail"
)
goto :eof

:start_frontend
set "LAST_RESULT=ok"
call :find_pids_by_port %FRONTEND_PORT%
if defined RESULT_PIDS (
    echo %YELLOW%[frontend already running]%RESET% PIDs: !RESULT_PIDS!
    goto :eof
)
if not exist "%FRONTEND_DIR%" (
    echo %RED%[error]%RESET% frontend dir missing: %FRONTEND_DIR%
    set "LAST_RESULT=fail"
    goto :eof
)
echo %GREEN%[starting frontend]%RESET%  vite     :%FRONTEND_PORT%  --^>^>  %LOG_DIR%\frontend.log
REM Write a tiny launcher .cmd to avoid quoting hell inside `start ... cmd /c`.
set "FE_LAUNCHER=%LOG_DIR%\_start_frontend.cmd"
>  "%FE_LAUNCHER%" echo @echo off
>> "%FE_LAUNCHER%" echo chcp 65001 ^>nul
>> "%FE_LAUNCHER%" echo cd /d "%FRONTEND_DIR%"
>> "%FE_LAUNCHER%" echo echo ==== start at %DATE% %TIME% ==== ^>^> "%LOG_DIR%\frontend.log"
>> "%FE_LAUNCHER%" echo npm run dev -- --host %FRONTEND_HOST% --port %FRONTEND_PORT% ^>^> "%LOG_DIR%\frontend.log" 2^>^&1
cd /d "%FRONTEND_DIR%"
start "novelai-frontend" /B cmd /c "%FE_LAUNCHER%"
ping -n 6 127.0.0.1 >nul
call :find_pids_by_port %FRONTEND_PORT%
if defined RESULT_PIDS (
    echo %GREEN%[frontend up]%RESET% PIDs: !RESULT_PIDS!    http://%FRONTEND_HOST%:%FRONTEND_PORT%/
) else (
    echo %YELLOW%[frontend still booting]%RESET% waiting another 5s...
    ping -n 6 127.0.0.1 >nul
    call :find_pids_by_port %FRONTEND_PORT%
    if defined RESULT_PIDS (
        echo %GREEN%[frontend up]%RESET% PIDs: !RESULT_PIDS!    http://%FRONTEND_HOST%:%FRONTEND_PORT%/
    ) else (
        echo %RED%[frontend start failed]%RESET% see: %LOG_DIR%\frontend.log
        set "LAST_RESULT=fail"
    )
)
goto :eof

REM ==================== STOP ====================

:stop_backend
set "LAST_RESULT=ok"
call :find_pids_by_port %BACKEND_PORT%
if not defined RESULT_PIDS (
    echo %GRAY%[backend not running]%RESET%
    goto :eof
)
echo %YELLOW%[stopping backend]%RESET%  PIDs: !RESULT_PIDS!
for %%P in (!RESULT_PIDS!) do call :kill_pid %%P
ping -n 2 127.0.0.1 >nul
call :find_pids_by_port %BACKEND_PORT%
if not defined RESULT_PIDS (
    echo %GREEN%[backend stopped]%RESET%
) else (
    echo %RED%[stop failed]%RESET%   PIDs still on :%BACKEND_PORT%: !RESULT_PIDS!
    set "LAST_RESULT=fail"
)
goto :eof

:stop_frontend
set "LAST_RESULT=ok"
call :find_pids_by_port %FRONTEND_PORT%
if not defined RESULT_PIDS (
    echo %GRAY%[frontend not running]%RESET%
    goto :eof
)
echo %YELLOW%[stopping frontend]%RESET%  PIDs: !RESULT_PIDS!
for %%P in (!RESULT_PIDS!) do call :kill_pid %%P
REM Vite spawns node child; clean up orphans on 5293 port
ping -n 2 127.0.0.1 >nul
call :find_pids_by_port %FRONTEND_PORT%
if not defined RESULT_PIDS (
    echo %GREEN%[frontend stopped]%RESET%
) else (
    echo %YELLOW%[lingering node, force killing]%RESET%
    for %%P in (!RESULT_PIDS!) do call :kill_pid %%P
    ping -n 2 127.0.0.1 >nul
    call :find_pids_by_port %FRONTEND_PORT%
    if defined RESULT_PIDS (
        echo %RED%[stop failed]%RESET%   PIDs still on :%FRONTEND_PORT%: !RESULT_PIDS!
        set "LAST_RESULT=fail"
    ) else (
        echo %GREEN%[frontend stopped]%RESET%
    )
)
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
