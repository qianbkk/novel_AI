@echo off
REM ============================================================
REM   NovelAI 后端管理 - 本地启动
REM   文件编码：UTF-8 with BOM
REM   平台：Windows 10/11
REM   用途：启动 / 停止 / 查看状态 / 查日志（后端 + 前端）
REM ============================================================

REM 1) 先强制切到 UTF-8 代码页，cmd 才能正确回显中文。
chcp 65001 >nul

REM 2) 窗口标题（用 CJK 安全的字符串）
title NovelAI 后端管理 - 本地启动

REM 3) 启用扩展 + 延迟变量展开
setlocal EnableExtensions EnableDelayedExpansion

REM ==================== 配置 ====================
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

REM ==================== ANSI 颜色辅助 ====================
for /F "tokens=1,2 delims=#" %%a in ('"prompt #$H#$E# & echo on & for %%b in (1) do rem"') do set "ESC=%%b"
set "GREEN=%ESC%[92m"
set "YELLOW=%ESC%[93m"
set "RED=%ESC%[91m"
set "CYAN=%ESC%[96m"
set "GRAY=%ESC%[90m"
set "RESET=%ESC%[0m"

REM ==================== 辅助子程序 ====================
REM 下面所有子程序只通过 `call :name` 到达；主流程必须跳过它们，
REM 直接跳到配置区末尾的 :main_start。
goto :main_start

:print_banner
echo.
echo %CYAN%============================================================%RESET%
echo %CYAN%   NovelAI 后端管理 - 本地启动%RESET%
echo %CYAN%============================================================%RESET%
echo   后端     : http://%BACKEND_HOST%:%BACKEND_PORT%  (uvicorn)
echo   前端     : http://%FRONTEND_HOST%:%FRONTEND_PORT%  (vite)
echo   API 文档 : http://%BACKEND_HOST%:%BACKEND_PORT%/docs
echo   日志目录 : %LOG_DIR%
echo %CYAN%------------------------------------------------------------%RESET%
goto :eof

:print_top_menu
echo.
echo %YELLOW%请选择操作：%RESET%
echo   %GREEN%1)%RESET% 启动全部          (uvicorn :8132 + vite :5293)
echo   %GREEN%2)%RESET% 停止全部
echo   %GREEN%3)%RESET% 重启全部          (先停止，等待，再启动)
echo   %GREEN%0)%RESET% 详细状态          (端口 + PID + HTTP /health)
echo   %GREEN%x)%RESET% 更多选项...
echo.
set /p "CHOICE=  请输入 [0-3, x]: "
goto :eof

:print_submenu
echo.
echo %YELLOW%更多选项：%RESET%
echo   %GREEN%1)%RESET% 启动后端          (uvicorn :8132)
echo   %GREEN%2)%RESET% 启动前端          (vite    :5293)
echo   %GREEN%3)%RESET% 停止后端
echo   %GREEN%4)%RESET% 停止前端
echo   %GREEN%5)%RESET% 查看实时日志
echo   %GREEN%6)%RESET% 备份 SQLite       (快照 data/*.db)
echo   %GREEN%0)%RESET% 返回主菜单
echo   %GREEN%9)%RESET% 退出
echo.
set /p "CHOICE=  请输入 [0-6, 9]: "
goto :eof

REM ---------- 按监听端口查找 PID ----------
:find_pids_by_port
set "PORT_TO_CHECK=%~1"
set "RESULT_PIDS="
REM 匹配 ":PORT" 后跟一个空格（同时兼容 IPv4 "127.0.0.1:5293 " 和
REM IPv6 "[::1]:5293 "）。锚定在尾部空格上是为了防止 ":8123" 误匹配
REM 到 ":81230" 之类的端口号。
for /F "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT_TO_CHECK% " ^| findstr LISTENING') do (
    if not defined RESULT_PIDS (set "RESULT_PIDS=%%P") else (set "RESULT_PIDS=!RESULT_PIDS! %%P")
)
goto :eof

REM ---------- 从 .runlogs/<name>.pid 读取我们记录的 PID ----------
REM 把 TRUSTED_PIDS 设为空格分隔的 PID 列表；文件缺失/为空则留空。
REM 这是我们启动应用时写下的"白名单"，用来区分"我们自己的进程"
REM 和碰巧占用同一端口的外部进程。
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

REM ---------- 状态面板 ----------
:print_status
set "BE_PROBE_OK=0"
set "FE_PROBE_OK=0"
echo.
echo %CYAN%[快照]%RESET%
echo %GRAY%------------------------------------------------------------%RESET%

REM 后端快照 — 三种状态：
REM   running（运行中）：PID 文件存在，且我们记录的 PID 仍在监听该端口。
REM   foreign（外部进程）：端口被占用，但不是我们记录的 PID（外部进程）。
REM   stopped（已停止）：端口上无任何进程（若有陈旧 PID 文件则一并清理）。
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
    echo   backend  :%BACKEND_PORT%    %GREEN%运行中%RESET%   PID: !BE_PORT_PIDS!
) else (
    REM 没有 PID 文件，或者我们的 PID 已经消失 — 清理陈旧文件（如果有）。
    if exist "%LOG_DIR%\backend.pid" del "%LOG_DIR%\backend.pid" >nul 2>&1
    if defined BE_PORT_PIDS (
        echo   backend  :%BACKEND_PORT%    %YELLOW%外部进程%RESET%   PID: !BE_PORT_PIDS!  非 dev.bat 启动
    ) else (
        echo   backend  :%BACKEND_PORT%    %GRAY%已停止%RESET%
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
    echo   frontend :%FRONTEND_PORT%    %GREEN%运行中%RESET%   PID: !FE_PORT_PIDS!
) else (
    if exist "%LOG_DIR%\frontend.pid" del "%LOG_DIR%\frontend.pid" >nul 2>&1
    if defined FE_PORT_PIDS (
        echo   frontend :%FRONTEND_PORT%    %YELLOW%外部进程%RESET%   PID: !FE_PORT_PIDS!  非 dev.bat 启动
    ) else (
        echo   frontend :%FRONTEND_PORT%    %GRAY%已停止%RESET%
    )
    set "FE_UP=0"
)
echo %GRAY%------------------------------------------------------------%RESET%

REM HTTP /health 探测（始终探测 — 即使是外部进程也值得验证一下，
REM 如果端口已经没有进程了，探测会返回"down"而不是让人误以为"跳过"。
echo %CYAN%[HTTP 探测]%RESET%
call :http_probe "backend /health    " "http://%BACKEND_HOST%:%BACKEND_PORT%/health" tri
set "BE_PROBE_OK=!PROBE_OK!"
call :http_probe "frontend /         " "http://%FRONTEND_HOST%:%FRONTEND_PORT%/" bin
set "FE_PROBE_OK=!PROBE_OK!"
echo %GRAY%------------------------------------------------------------%RESET%
echo.
call :_hint
goto :eof

REM ---------- HTTP 探测（tri：后端 /health 三态；bin：前端 / 二态） ----------
REM PROBE_OK=1 表示 HTTP 状态码 <400，否则为 0。这里用 curl.exe 是因为
REM 它会根据 HTTP 状态设置 ERRORLEVEL，让我们能可靠地把结果读回父级
REM 批处理（子进程里的 PowerShell $global: 不会传回 cmd）。
:http_probe
set "LABEL=%~1"
set "URL=%~2"
set "MODE=%~3"
set "PROBE_OK=0"
set "PROBE_CODE="
if /I "%MODE%"=="tri" goto :_probe_tri
REM --- bin 模式（前端）---
REM 单独跑 curl 以便读到它真实的退出码（而不是 for 循环的）。
curl.exe -s -o NUL -w "%%{http_code}" --max-time 3 "%URL%" 1>"%TEMP%\_probe_code.txt" 2>nul
set "CURL_ERR=!errorlevel!"
set /p "PROBE_CODE=" < "%TEMP%\_probe_code.txt" 2>nul
del "%TEMP%\_probe_code.txt" 2>nul
if !CURL_ERR! NEQ 0 (
    echo   %LABEL%%YELLOW%n/a%RESET%     连接被拒绝或超时
    goto :eof
)
if "!PROBE_CODE!"=="" (
    echo   %LABEL%%YELLOW%n/a%RESET%     无响应
    goto :eof
)
set "PROBE_OK=1"
echo   %LABEL%%GREEN%ok%RESET%      status=!PROBE_CODE!
goto :eof
:_probe_tri
REM --- tri 模式（后端 /health）---
curl.exe -s -o NUL -w "%%{http_code}" --max-time 3 "%URL%" 1>"%TEMP%\_probe_code.txt" 2>nul
set "CURL_ERR=!errorlevel!"
set /p "PROBE_CODE=" < "%TEMP%\_probe_code.txt" 2>nul
del "%TEMP%\_probe_code.txt" 2>nul
if !CURL_ERR! NEQ 0 (
    echo   %LABEL%%RED%down%RESET%     连接被拒绝或超时
    goto :eof
)
if "!PROBE_CODE!"=="" (
    echo   %LABEL%%RED%down%RESET%     无响应
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

REM ---------- 智能提示（消费 :print_status 设置的 BE_UP/FE_UP） ----------
:_hint
REM 仅当后端和前端都不是 dev.bat 管理的、且端口上也没有外部监听时，
REM 才提示"全部已停止"。
if NOT "%BE_UP%%FE_UP%"=="00" goto :_hint_check_backend
if defined BE_PORT_PIDS goto :_hint_check_backend
if defined FE_PORT_PIDS goto :_hint_check_backend
echo %YELLOW%提示%RESET%  全部已停止。按 %GREEN%1%RESET% 启动全部，或运行：dev.bat start-all
goto :eof
:_hint_check_backend
if NOT "%BE_UP%"=="0" goto :_hint_check_frontend
REM 端口上可能仍有外部监听者响应了 /health，这种情况下
REM BE_PROBE_OK=1，此时提示"后端已停止"就不准确了。
if "%BE_PROBE_OK%"=="1" goto :_hint_check_frontend
if defined BE_PORT_PIDS (
    echo %YELLOW%提示%RESET%  后端端口 :%BACKEND_PORT% 上有外部进程（PID: !BE_PORT_PIDS!），但 /health 无响应。
    echo          dev.bat 无法管理该进程。请手动结束它，或修改 dev.bat 里的 BACKEND_PORT。
) else (
    echo %YELLOW%提示%RESET%  后端已停止，前端仍在运行。Web 界面将无法访问后端 API。
    echo          按 %GREEN%x%RESET% 再按 %GREEN%1%RESET% 启动后端，或运行：dev.bat start-backend
)
goto :eof
:_hint_check_frontend
if NOT "%FE_UP%"=="0" goto :eof
echo %YELLOW%提示%RESET%  后端已运行，前端未启动。可直接访问 API 文档：http://%BACKEND_HOST%:%BACKEND_PORT%/docs
echo          按 %GREEN%x%RESET% 再按 %GREEN%2%RESET% 启动前端，或运行：dev.bat start-frontend
goto :eof

REM ==================== 启动 ====================

REM ---------- 写一个启动器 .cmd（避免在 `start ... cmd /c` 里处理引号地狱） ----------
:_write_launcher
set "LAUNCHER_PATH=%~1"
set "TARGET_DIR=%~2"
set "LOG_PATH=%~3"
set "CMD_LINE=%~4"
>  "%LAUNCHER_PATH%" echo @echo off
>> "%LAUNCHER_PATH%" echo REM ---------- 可写性自检（捕获锁住日志文件的孤儿进程） ----------
>> "%LAUNCHER_PATH%" echo type nul ^>^> "%LOG_PATH%" 2^>^&1
>> "%LAUNCHER_PATH%" echo if errorlevel 1 ^(
>> "%LAUNCHER_PATH%" echo     echo [FATAL] 无法写入日志文件："%LOG_PATH%"
>> "%LAUNCHER_PATH%" echo     echo [FATAL] 很可能有其他进程正锁着它 [ERROR_SHARING_VIOLATION]。
>> "%LAUNCHER_PATH%" echo     echo [FATAL] 通常意味着上一次的 uvicorn / node 进程还没退出。
>> "%LAUNCHER_PATH%" echo     echo [FATAL]
>> "%LAUNCHER_PATH%" echo     echo [FATAL] 请找到并结束这个孤儿进程：
>> "%LAUNCHER_PATH%" echo     echo [FATAL]   powershell -NoProfile -Command "Get-Process python,node -ErrorAction SilentlyContinue ^| Where-Object { $_.StartTime -lt (Get-Date).AddHours(-1) } ^| Format-Table Id,ProcessName,StartTime -AutoSize"
>> "%LAUNCHER_PATH%" echo     echo [FATAL]   powershell -NoProfile -Command "Stop-Process -Id ^<pid^> -Force"
>> "%LAUNCHER_PATH%" echo     echo [FATAL]
>> "%LAUNCHER_PATH%" echo     echo [FATAL] 然后重新运行 dev.bat start-!APP_NAME!。
>> "%LAUNCHER_PATH%" echo     exit /b 1
>> "%LAUNCHER_PATH%" echo ^)
>> "%LAUNCHER_PATH%" echo chcp 65001 ^>nul
>> "%LAUNCHER_PATH%" echo cd /d "%TARGET_DIR%"
if "%ADD_BLANK%"=="1" >> "%LAUNCHER_PATH%" echo echo. ^>^> "%LOG_PATH%"
>> "%LAUNCHER_PATH%" echo echo ==== 启动于 %DATE% %TIME% ==== ^>^> "%LOG_PATH%"
>> "%LAUNCHER_PATH%" echo %CMD_LINE% ^>^> "%LOG_PATH%" 2^>^&1
goto :eof

REM ---------- 通用应用启动器（由调用方 thunk 提前设好的环境变量驱动） ----------
:launch_app
set "LAST_RESULT=ok"
set "LAUNCHER=%LOG_DIR%\_start_%LOG_BASENAME%.cmd"
call :find_pids_by_port %APP_PORT%
if defined RESULT_PIDS (
    echo %YELLOW%[!APP_NAME! 已在运行]%RESET% PID: !RESULT_PIDS!
    goto :eof
)
if not exist "%APP_DIR%" (
    echo %RED%[错误]%RESET% !APP_NAME! 目录不存在：!APP_DIR!
    set "LAST_RESULT=fail"
    goto :eof
)
echo %GREEN%[正在启动 !APP_NAME!]%RESET%  !TOOL_NAME!  :%APP_PORT%  --^>^>  %LOG_DIR%\!LOG_BASENAME!.log
call :_write_launcher "%LAUNCHER%" "%APP_DIR%" "%LOG_DIR%\!LOG_BASENAME!.log" "%CMD_LINE%"
cd /d "%APP_DIR%"
start "%WINDOW_TITLE%" /B cmd /c "%LAUNCHER%"
ping -n %START_WAIT% 127.0.0.1 >nul
call :find_pids_by_port %APP_PORT%
if defined RESULT_PIDS goto :_launch_app_report_up
if "%BOOT_RETRY%"=="1" (
    echo %YELLOW%[!APP_NAME! 仍在启动中]%RESET% 再等待 !BOOT_MSG! 秒...
    ping -n %BOOT_WAIT% 127.0.0.1 >nul
    call :find_pids_by_port %APP_PORT%
    if defined RESULT_PIDS goto :_launch_app_report_up
)
echo %RED%[!APP_NAME! 启动失败]%RESET% 详见：%LOG_DIR%\!LOG_BASENAME!.log
set "LAST_RESULT=fail"
goto :eof
:_launch_app_report_up
REM 记录我们的 PID，供 :print_status 之后区分"我们启动的进程"
REM 和碰巧占用同一端口的外部进程。格式：一行，空格分隔，同 RESULT_PIDS。
> "%PID_FILE%" echo !RESULT_PIDS!
echo %GREEN%[!APP_NAME! 已启动]%RESET% PID: !RESULT_PIDS!    !UP_URL!
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

REM ==================== 停止 ====================

:stop_port
set "APP_NAME=%~1"
set "APP_PORT=%~2"
set "ORPHAN_RETRY=%~3"
set "LAST_RESULT=ok"
set "PID_FILE=%LOG_DIR%\!APP_NAME!.pid"
call :find_pids_by_port %APP_PORT%
if not defined RESULT_PIDS (
    REM 端口上没有进程 — 清理陈旧的 PID 文件（如果有）。
    if exist "!PID_FILE!" del "!PID_FILE!" >nul 2>&1
    echo %GRAY%[!APP_NAME! 未运行]%RESET%
    goto :eof
)
echo %YELLOW%[正在停止 !APP_NAME!]%RESET%  PID: !RESULT_PIDS!
for %%P in (!RESULT_PIDS!) do call :kill_pid %%P
ping -n 2 127.0.0.1 >nul
call :find_pids_by_port %APP_PORT%
if not defined RESULT_PIDS (
    REM 成功 — 清除白名单，让下次启动更干净。
    if exist "!PID_FILE!" del "!PID_FILE!" >nul 2>&1
    echo %GREEN%[!APP_NAME! 已停止]%RESET%
    goto :eof
)
if /I not "%ORPHAN_RETRY%"=="1" (
    echo %RED%[停止失败]%RESET%   :%APP_PORT% 上仍有进程：!RESULT_PIDS!
    set "LAST_RESULT=fail"
    goto :eof
)
echo %YELLOW%[有残留进程，强制结束]%RESET%
for %%P in (!RESULT_PIDS!) do call :kill_pid %%P
ping -n 2 127.0.0.1 >nul
call :find_pids_by_port %APP_PORT%
if defined RESULT_PIDS (
    echo %RED%[停止失败]%RESET%   :%APP_PORT% 上仍有进程：!RESULT_PIDS!
    set "LAST_RESULT=fail"
) else (
    if exist "!PID_FILE!" del "!PID_FILE!" >nul 2>&1
    echo %GREEN%[!APP_NAME! 已停止]%RESET%
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
echo %GREEN%[全部已停止]%RESET%
goto :eof

REM ==================== 日志 ====================

REM ---------- backend/data/*.db 的 SQLite 快照（P1：数据丢失兜底） ----------
REM 复用 backend/app/backup_db.py —— 和 FastAPI 启动时走同一段代码，
REM 避免逻辑漂移。NOVEL_AI_SKIP_BACKUP=0 强制开启，即便用户在环境变量
REM 里设过其他值。这里调一个小启动脚本（scripts/backup_cli.py），
REM 因为 cmd 没法可靠地把多行 f-string 传给 python -c。
:do_backup
echo %CYAN%[备份]%RESET% 正在快照 backend/data/*.db
cd /d "%BACKEND_DIR%"
set NOVEL_AI_SKIP_BACKUP=0
python -m scripts.backup_cli
if errorlevel 1 (
    echo %YELLOW%[备份]%RESET% 未写出任何快照（请查看上方警告信息）
) else (
    echo %GREEN%[备份完成]%RESET%
)
goto :eof

:tail_logs
echo %CYAN%[实时日志 - 按 Ctrl+C 停止]%RESET%
echo %GRAY%------------------------------------------------------------%RESET%
if exist "%LOG_DIR%\backend.log" (
    echo %YELLOW%== %LOG_DIR%\backend.log ==%RESET%
) else (
    echo %GRAY%（尚无 backend.log）%RESET%
)
if exist "%LOG_DIR%\frontend.log" (
    echo %YELLOW%== %LOG_DIR%\frontend.log ==%RESET%
) else (
    echo %GRAY%（尚无 frontend.log）%RESET%
)
echo %GRAY%------------------------------------------------------------%RESET%
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content '%LOG_DIR%\backend.log','%LOG_DIR%\frontend.log' -Wait -Encoding UTF8 -Tail 20 -ErrorAction SilentlyContinue"
goto :eof

REM ==================== 主循环 ====================

:main_start
REM CLI 模式：dev.bat <参数> -> 执行一次对应动作后退出。
REM 适用于非交互场景（如 CI、计划任务、快速检查）。
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
    echo %RED%[错误]%RESET% 未知的命令行参数：%~1
    goto :print_help
)

REM 无命令行参数：进入交互菜单。（不要落到下面的 :print_help 标签，
REM 那样只会打印用法说明就退出。）
goto :top_menu

:print_help
echo.
echo %CYAN%用法：%RESET%
echo   dev.bat                       交互菜单
echo   dev.bat start-backend         启动 uvicorn :8132
echo   dev.bat start-frontend        启动 vite    :5293
echo   dev.bat start-all             启动全部
echo   dev.bat stop-backend          结束 8132 端口上的进程
echo   dev.bat stop-frontend         结束 5293 端口上的进程
echo   dev.bat stop-all              结束全部
echo   dev.bat restart-all           先停止再启动全部
echo   dev.bat status                打印端口 / PID / /health 状态
echo   dev.bat backup                快照两个 SQLite 数据库
echo   dev.bat help                  显示本帮助
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

REM ---------- 主菜单操作后的提示 ----------
:top_post_prompt
echo.
echo %GRAY%------------------------------------------------------------%RESET%
echo   按 %GREEN%M%RESET% 返回主菜单  %GREEN%x%RESET% 更多选项  %GREEN%0%RESET% 查看状态  %GREEN%Q%RESET% 退出
set /p "NEXT=  ... "
if /I "!NEXT!"=="Q" goto :exit_script
if /I "!NEXT!"=="X" goto :submenu
if "!NEXT!"=="0" (
    call :print_status
    echo.
    echo %GRAY%------------------------------------------------------------%RESET%
    echo   按 %GREEN%M%RESET% 返回主菜单  %GREEN%x%RESET% 更多选项  %GREEN%Q%RESET% 退出
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

REM ---------- 子菜单操作后的提示 ----------
:sub_post_prompt
echo.
echo %GRAY%------------------------------------------------------------%RESET%
echo   按 %GREEN%M%RESET% 更多选项  %GREEN%T%RESET% 返回主菜单  %GREEN%0%RESET% 查看状态  %GREEN%Q%RESET% 退出
set /p "NEXT=  ... "
if /I "!NEXT!"=="Q" goto :exit_script
if /I "!NEXT!"=="T" goto :top_menu
if "!NEXT!"=="0" (
    call :print_status
    echo.
    echo %GRAY%------------------------------------------------------------%RESET%
    echo   按 %GREEN%M%RESET% 更多选项  %GREEN%T%RESET% 返回主菜单  %GREEN%Q%RESET% 退出
    set /p "NEXT=  ... "
    if /I "!NEXT!"=="Q" goto :exit_script
    if /I "!NEXT!"=="T" goto :top_menu
)
goto :submenu

:exit_script
endlocal
echo.
echo %CYAN%再见。%RESET%
exit /b 0
