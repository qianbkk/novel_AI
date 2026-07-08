@echo off
REM ============================================================
REM   NovelAI 后端管理 - 本地启动
REM   File encoding: UTF-8 with BOM
REM   Platform: Windows 10/11
REM   Purpose: start / stop / status / log backend + frontend
REM ============================================================

REM 1) Force UTF-8 code page FIRST so cmd can echo CJK correctly.
chcp 65001 >nul

REM 2) Title (use a CJK-safe string)
title NovelAI 后端管理 - 本地启动

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
echo %CYAN%   NovelAI 后端管理 - 本地启动%RESET%
echo %CYAN%============================================================%RESET%
echo   Backend  : http://%BACKEND_HOST%:%BACKEND_PORT%  (uvicorn)
echo   Frontend : http://%FRONTEND_HOST%:%FRONTEND_PORT%  (vite)
echo   API docs : http://%BACKEND_HOST%:%BACKEND_PORT%/docs
echo   Logs dir : %LOG_DIR%
echo %CYAN%------------------------------------------------------------%RESET%
goto :eof

:print_top_menu
echo.
echo %YELLOW%Choose action:%RESET%
echo   %GREEN%1)%RESET% start BOTH        (uvicorn :8132 + vite :5293)
echo   %GREEN%2)%RESET% stop BOTH
echo   %GREEN%3)%RESET% restart BOTH      (stop, wait, start both)
echo   %GREEN%0)%RESET% detailed status   (port + pid + HTTP /health)
echo   %GREEN%x)%RESET% more options...
echo.
set /p "CHOICE=  Your choice [0-3, x]: "
goto :eof

:print_submenu
echo.
echo %YELLOW%More options:%RESET%
echo   %GREEN%1)%RESET% start backend     (uvicorn :8132)
echo   %GREEN%2)%RESET% start frontend    (vite    :5293)
echo   %GREEN%3)%RESET% stop backend
echo   %GREEN%4)%RESET% stop frontend
echo   %GREEN%5)%RESET% tail logs
echo   %GREEN%6)%RESET% backup SQLite     (snapshot data/*.db)
echo   %GREEN%0)%RESET% back to top menu
echo   %GREEN%9)%RESET% exit
echo.
set /p "CHOICE=  Your choice [0-6, 9]: "
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

REM ---------- Read our recorded PIDs from .runlogs/<name>.pid ----------
REM Sets TRUSTED_PIDS to space-separated PIDs, or empty if file missing/blank.
REM This is the "guest list" we wrote when WE started the app, so we can
REM distinguish our process from any foreign listener squatting on the port.
:read_pid_file
set "TRUSTED_PIDS="
if exist "%~1" (
    set /p "TRUSTED_PIDS=" < "%~1"
)
goto :eof

:kill_pid
set "PID_TO_KILL=%~1"
taskkill /F /PID %PID_TO_KILL% >nul 2>&1
goto :eof

REM ---------- status panel ----------
:print_status
set "BE_PROBE_OK=0"
set "FE_PROBE_OK=0"
echo.
echo %CYAN%[Snapshot]%RESET%
echo %GRAY%------------------------------------------------------------%RESET%

REM Backend snapshot — three states:
REM   running : PID file exists AND our recorded PID is still on the port.
REM   foreign : port is bound but NOT by our recorded PID (foreign process).
REM   stopped : nothing on the port (PID file cleared if stale).
call :find_pids_by_port %BACKEND_PORT%
set "BE_PORT_PIDS=!RESULT_PIDS!"
call :read_pid_file "%LOG_DIR%\backend.pid"
set "BE_TRUSTED_PIDS=!TRUSTED_PIDS!"
set "BE_UP=0"
if defined BE_TRUSTED_PIDS (
    for %%P in (!BE_TRUSTED_PIDS!) do (
        for %%Q in (!BE_PORT_PIDS!) do (
            if "%%P"=="%%Q" set "BE_UP=1"
        )
    )
)
if "!BE_UP!"=="1" (
    echo   backend  :%BACKEND_PORT%    %GREEN%running%RESET%   PIDs: !BE_PORT_PIDS!
) else (
    REM Either no PID file or our PID is gone — clean up stale file if any.
    if exist "%LOG_DIR%\backend.pid" del "%LOG_DIR%\backend.pid" >nul 2>&1
    if defined BE_PORT_PIDS (
        echo   backend  :%BACKEND_PORT%    %YELLOW%foreign%RESET%   PIDs: !BE_PORT_PIDS!  not started by dev.bat
    ) else (
        echo   backend  :%BACKEND_PORT%    %GRAY%stopped%RESET%
    )
    set "BE_UP=0"
)

call :find_pids_by_port %FRONTEND_PORT%
set "FE_PORT_PIDS=!RESULT_PIDS!"
call :read_pid_file "%LOG_DIR%\frontend.pid"
set "FE_TRUSTED_PIDS=!TRUSTED_PIDS!"
set "FE_UP=0"
if defined FE_TRUSTED_PIDS (
    for %%P in (!FE_TRUSTED_PIDS!) do (
        for %%Q in (!FE_PORT_PIDS!) do (
            if "%%P"=="%%Q" set "FE_UP=1"
        )
    )
)
if "!FE_UP!"=="1" (
    echo   frontend :%FRONTEND_PORT%    %GREEN%running%RESET%   PIDs: !FE_PORT_PIDS!
) else (
    if exist "%LOG_DIR%\frontend.pid" del "%LOG_DIR%\frontend.pid" >nul 2>&1
    if defined FE_PORT_PIDS (
        echo   frontend :%FRONTEND_PORT%    %YELLOW%foreign%RESET%   PIDs: !FE_PORT_PIDS!  not started by dev.bat
    ) else (
        echo   frontend :%FRONTEND_PORT%    %GRAY%stopped%RESET%
    )
    set "FE_UP=0"
)
echo %GRAY%------------------------------------------------------------%RESET%

REM HTTP /health check (always probe — even a foreign listener deserves verification,
REM and if the port is dead the probe returns "down" instead of misleading "skipped").
echo %CYAN%[HTTP probe]%RESET%
call :http_probe "backend /health    " "http://%BACKEND_HOST%:%BACKEND_PORT%/health" tri
set "BE_PROBE_OK=!PROBE_OK!"
call :http_probe "frontend /         " "http://%FRONTEND_HOST%:%FRONTEND_PORT%/" bin
set "FE_PROBE_OK=!PROBE_OK!"
echo %GRAY%------------------------------------------------------------%RESET%
echo.
call :_hint
goto :eof

REM ---------- HTTP probe (tri: backend /health 3-state; bin: frontend / binary) ----------
REM Sets PROBE_OK=1 if HTTP <400, PROBE_OK=0 otherwise. We use curl.exe because
REM it sets ERRORLEVEL based on HTTP status, letting us reliably read the result
REM back into the parent batch (PowerShell $global: in a child process does NOT
REM propagate back to cmd).
:http_probe
set "LABEL=%~1"
set "URL=%~2"
set "MODE=%~3"
set "PROBE_OK=0"
set "PROBE_CODE="
if /I "%MODE%"=="tri" goto :_probe_tri
REM --- bin mode (frontend) ---
REM Run curl separately so we can read its real exit code (not the for's).
curl.exe -s -o NUL -w "%%{http_code}" --max-time 3 "%URL%" 1>"%TEMP%\_probe_code.txt" 2>nul
set "CURL_ERR=!errorlevel!"
set /p "PROBE_CODE=" < "%TEMP%\_probe_code.txt" 2>nul
del "%TEMP%\_probe_code.txt" 2>nul
if !CURL_ERR! NEQ 0 (
    echo   %LABEL%%YELLOW%n/a%RESET%     connection refused or timeout
    goto :eof
)
if "!PROBE_CODE!"=="" (
    echo   %LABEL%%YELLOW%n/a%RESET%     no response
    goto :eof
)
set "PROBE_OK=1"
echo   %LABEL%%GREEN%ok%RESET%      status=!PROBE_CODE!
goto :eof
:_probe_tri
REM --- tri mode (backend /health) ---
curl.exe -s -o NUL -w "%%{http_code}" --max-time 3 "%URL%" 1>"%TEMP%\_probe_code.txt" 2>nul
set "CURL_ERR=!errorlevel!"
set /p "PROBE_CODE=" < "%TEMP%\_probe_code.txt" 2>nul
del "%TEMP%\_probe_code.txt" 2>nul
if !CURL_ERR! NEQ 0 (
    echo   %LABEL%%RED%down%RESET%     connection refused or timeout
    goto :eof
)
if "!PROBE_CODE!"=="" (
    echo   %LABEL%%RED%down%RESET%     no response
    goto :eof
)
set /a "HTTP_NUM=!PROBE_CODE!" 2>nul
if !HTTP_NUM! LSS 400 (
    set "PROBE_OK=1"
    echo   %LABEL%%GREEN%ok%RESET%      status=!PROBE_CODE!
) else (
    echo   %LABEL%%YELLOW%http!PROBE_CODE!%RESET%
)
goto :eof

REM ---------- smart hint (consumes BE_UP/FE_UP set by :print_status) ----------
:_hint
REM "Both stopped" only when neither is dev-managed AND no foreign listener exists.
if NOT "%BE_UP%%FE_UP%"=="00" goto :_hint_check_backend
if defined BE_PORT_PIDS goto :_hint_check_backend
if defined FE_PORT_PIDS goto :_hint_check_backend
echo %YELLOW%hint%RESET%  both stopped. Press %GREEN%1%RESET% to start both, or run:  dev.bat start-all
goto :eof
:_hint_check_backend
if NOT "%BE_UP%"=="0" goto :_hint_check_frontend
REM Port might still have a foreign listener that responded to /health,
REM in which case BE_PROBE_OK=1 and "backend is down" would be a lie.
if "%BE_PROBE_OK%"=="1" goto :_hint_check_frontend
if defined BE_PORT_PIDS (
    echo %YELLOW%hint%RESET%  backend port :%BACKEND_PORT% has a foreign process (PIDs: !BE_PORT_PIDS!) but /health is NOT responding.
    echo          dev.bat cannot manage that process. Find and kill it, or change BACKEND_PORT in dev.bat.
) else (
    echo %YELLOW%hint%RESET%  backend is down but frontend is up. The web UI will not be able to reach the API.
    echo          press %GREEN%x%RESET% then %GREEN%1%RESET% to start backend, or run:  dev.bat start-backend
)
goto :eof
:_hint_check_frontend
if NOT "%FE_UP%"=="0" goto :eof
echo %YELLOW%hint%RESET%  backend is up but frontend is down. You can hit the API directly at http://%BACKEND_HOST%:%BACKEND_PORT%/docs
echo          press %GREEN%x%RESET% then %GREEN%2%RESET% to start frontend, or run:  dev.bat start-frontend
goto :eof

REM ==================== START ====================

REM ---------- write a launcher .cmd (avoids quoting hell inside `start ... cmd /c`) ----------
:_write_launcher
set "LAUNCHER_PATH=%~1"
set "TARGET_DIR=%~2"
set "LOG_PATH=%~3"
set "CMD_LINE=%~4"
>  "%LAUNCHER_PATH%" echo @echo off
>> "%LAUNCHER_PATH%" echo REM ---------- writability self-check (catches orphan processes locking the log) ----------
>> "%LAUNCHER_PATH%" echo type nul ^>^> "%LOG_PATH%" 2^>^&1
>> "%LAUNCHER_PATH%" echo if errorlevel 1 ^(
>> "%LAUNCHER_PATH%" echo     echo [FATAL] Cannot write to log: "%LOG_PATH%"
>> "%LAUNCHER_PATH%" echo     echo [FATAL] Another process is probably locking it [ERROR_SHARING_VIOLATION].
>> "%LAUNCHER_PATH%" echo     echo [FATAL] This usually means a previous uvicorn / node worker is still alive.
>> "%LAUNCHER_PATH%" echo     echo [FATAL]
>> "%LAUNCHER_PATH%" echo     echo [FATAL] Find and kill the orphan:
>> "%LAUNCHER_PATH%" echo     echo [FATAL]   powershell -NoProfile -Command "Get-Process python,node -ErrorAction SilentlyContinue ^| Where-Object { $_.StartTime -lt (Get-Date).AddHours(-1) } ^| Format-Table Id,ProcessName,StartTime -AutoSize"
>> "%LAUNCHER_PATH%" echo     echo [FATAL]   powershell -NoProfile -Command "Stop-Process -Id ^<pid^> -Force"
>> "%LAUNCHER_PATH%" echo     echo [FATAL]
>> "%LAUNCHER_PATH%" echo     echo [FATAL] Then re-run dev.bat start-!APP_NAME!.
>> "%LAUNCHER_PATH%" echo     exit /b 1
>> "%LAUNCHER_PATH%" echo ^)
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
REM Record our PID(s) so :print_status can later distinguish "our" process
REM from any foreign listener squatting on the port. Format: one line, space-
REM separated, same as RESULT_PIDS.
> "%PID_FILE%" echo !RESULT_PIDS!
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
set "PID_FILE=%LOG_DIR%\backend.pid"
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
set "TOOL_NAME=vite   "
set "UP_URL=http://%FRONTEND_HOST%:%FRONTEND_PORT%/"
set "LOG_BASENAME=frontend"
set "WINDOW_TITLE=novelai-frontend"
set "PID_FILE=%LOG_DIR%\frontend.pid"
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
set "PID_FILE=%LOG_DIR%\!APP_NAME!.pid"
call :find_pids_by_port %APP_PORT%
if not defined RESULT_PIDS (
    REM Nothing on port — clear any stale PID file.
    if exist "!PID_FILE!" del "!PID_FILE!" >nul 2>&1
    echo %GRAY%[!APP_NAME! not running]%RESET%
    goto :eof
)
echo %YELLOW%[stopping !APP_NAME!]%RESET%  PIDs: !RESULT_PIDS!
for %%P in (!RESULT_PIDS!) do call :kill_pid %%P
ping -n 2 127.0.0.1 >nul
call :find_pids_by_port %APP_PORT%
if not defined RESULT_PIDS (
    REM Success — drop the guest list so a future start is clean.
    if exist "!PID_FILE!" del "!PID_FILE!" >nul 2>&1
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
    if exist "!PID_FILE!" del "!PID_FILE!" >nul 2>&1
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

REM ---------- SQLite snapshot of backend/data/*.db (P1: data-loss mitigation) ----------
REM Reuses backend/app/backup_db.py — same code path as FastAPI startup, so we
REM don't drift. NOVEL_AI_SKIP_BACKUP=0 forces it ON even if user set it in env.
REM We call a small launcher script (scripts/backup_cli.py) because cmd can't
REM reliably pipe multi-line f-strings through python -c.
:do_backup
echo %CYAN%[backup]%RESET% taking snapshots of backend/data/*.db
cd /d "%BACKEND_DIR%"
set NOVEL_AI_SKIP_BACKUP=0
python -m scripts.backup_cli
if errorlevel 1 (
    echo %YELLOW%[backup]%RESET% no snapshots written (check warnings above)
) else (
    echo %GREEN%[backup done]%RESET%
)
goto :eof

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
    if /I "%~1"=="backup"          call :do_backup        & goto :exit_script
    if /I "%~1"=="help"            goto :print_help
    echo %RED%[error]%RESET% unknown CLI arg: %~1
    goto :print_help
)

REM No CLI arg: fall through to interactive menu. (Don't fall through to
REM :print_help label below - that would just print Usage and exit.)
goto :top_menu

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
echo   dev.bat backup                snapshot both SQLite DBs
echo   dev.bat help                  show this
goto :exit_script

:top_menu
call :print_banner
call :print_top_menu
if "%CHOICE%"=="" goto :top_menu

set "LAST_RESULT=ok"

if "%CHOICE%"=="1" (
    call :start_backend
    call :start_frontend
)
if "%CHOICE%"=="2" call :stop_all
if "%CHOICE%"=="3" (
    call :stop_all
    ping -n 3 127.0.0.1 >nul
    call :start_backend
    call :start_frontend
)
if "%CHOICE%"=="0" call :print_status
if /I "%CHOICE%"=="X" goto :submenu

REM ---------- top-menu post-action prompt ----------
:top_post_prompt
echo.
echo %GRAY%------------------------------------------------------------%RESET%
echo   Press %GREEN%M%RESET% for top menu  %GREEN%x%RESET% for more options  %GREEN%0%RESET% for status  %GREEN%Q%RESET% to quit
set /p "NEXT=  ... "
if /I "!NEXT!"=="Q" goto :exit_script
if /I "!NEXT!"=="X" goto :submenu
if "!NEXT!"=="0" (
    call :print_status
    echo.
    echo %GRAY%------------------------------------------------------------%RESET%
    echo   Press %GREEN%M%RESET% for top menu  %GREEN%x%RESET% for more options  %GREEN%Q%RESET% to quit
    set /p "NEXT=  ... "
    if /I "!NEXT!"=="Q" goto :exit_script
    if /I "!NEXT!"=="X" goto :submenu
)
goto :top_menu

:submenu
call :print_banner
call :print_submenu
if "%CHOICE%"=="" goto :submenu

set "LAST_RESULT=ok"

if "%CHOICE%"=="1" call :start_backend
if "%CHOICE%"=="2" call :start_frontend
if "%CHOICE%"=="3" call :stop_backend
if "%CHOICE%"=="4" call :stop_frontend
if "%CHOICE%"=="5" call :tail_logs
if "%CHOICE%"=="6" call :do_backup
if "%CHOICE%"=="0" goto :top_menu
if "%CHOICE%"=="9" goto :exit_script

REM ---------- submenu post-action prompt ----------
:sub_post_prompt
echo.
echo %GRAY%------------------------------------------------------------%RESET%
echo   Press %GREEN%M%RESET% for more options  %GREEN%T%RESET% for top menu  %GREEN%0%RESET% for status  %GREEN%Q%RESET% to quit
set /p "NEXT=  ... "
if /I "!NEXT!"=="Q" goto :exit_script
if /I "!NEXT!"=="T" goto :top_menu
if "!NEXT!"=="0" (
    call :print_status
    echo.
    echo %GRAY%------------------------------------------------------------%RESET%
    echo   Press %GREEN%M%RESET% for more options  %GREEN%T%RESET% for top menu  %GREEN%Q%RESET% to quit
    set /p "NEXT=  ... "
    if /I "!NEXT!"=="Q" goto :exit_script
    if /I "!NEXT!"=="T" goto :top_menu
)
goto :submenu

:exit_script
endlocal
echo.
echo %CYAN%Bye.%RESET%
exit /b 0
