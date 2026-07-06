"""run_bridge_subprocess.py — engine subprocess 入口

为什么独立成脚本：
  bridge.run 之前用 BackgroundTasks 在 uvicorn worker 进程内跑 engine。
  uvicorn 重启（手动 / --reload / OOM）会杀掉 in-flight engine run。
  本脚本作为 subprocess 入口，engine 在独立 Python 进程里跑，
  uvicorn 重启不影响。

调用：
  python -m engine.workers.run_bridge_subprocess \\
      <run_id> <project_id> <command> <arg1> <arg2> ... <outline_mode>

stdout 行为：
  - 任何 print() 都进 stdout
  - 主进程读 stdout → 转 SSE 事件给前端
  - 同时主进程每 50 行 flush 到 BridgeRun.stdout_text 字段

环境变量契约（iter #84 P0 bug 修复后）：
  父进程（app/api/bridge.py::_spawn_engine_subprocess）通过 Popen 的 env 参数
  显式传以下关键 env；本脚本继承自 subprocess 进程 os.environ：
    - NOVEL_OUTLINE_MODE : outline 模式（由父进程强制覆盖）
    - NOVEL_AI_DIR       : 项目 novel_ai_dir（决定 orchestrator 输出路径，
                           缺失则写到默认 backend/data/engine/output/，
                           导致 bridge.reports 读不到最新 checkpoint）
    - NOVEL_ENGINE_MOCK  : "1" 强制 LLMRouter 走 mock（缺失会让 router
                           真去调 API 报 "MINIMAX_API_KEY 未设置"）
  不要在 main() 里清空或覆盖父进程传过来的这些 key —— 本脚本只负责把
  outline_mode 强制刷一遍，确保父进程的值不会被 stale cache 干扰。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 让 sys.path 包含 backend 根目录（workdir 已在 cwd 里设置）
BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def main() -> int:
    if len(sys.argv) < 5:
        print(f"用法: {sys.argv[0]} <run_id> <project_id> <command> <arg1> ... <outline_mode>",
              file=sys.stderr)
        return 1

    run_id      = sys.argv[1]
    project_id  = sys.argv[2]
    command     = sys.argv[3]
    args        = sys.argv[4:-1]  # 中间都是 command args
    outline_mode = sys.argv[-1]

    print(f"[subprocess] start run_id={run_id} project={project_id} cmd={command} args={args} mode={outline_mode}",
          flush=True)

    # 注入 outline_mode env（orchestrator 读 os.environ）
    import os
    os.environ["NOVEL_OUTLINE_MODE"] = outline_mode

    try:
        from engine.graph import run_graph_task
        from queue import Queue
        q = Queue()
        # 重要：run_graph_task 把 print() 通过 stdout pipe 走（subprocess 默认），
        # 但 SSE queue 必须 in-process。这里只调 run_graph_task，
        # 让它把 print() 输出到 stdout（subprocess 接管），
        # 不再走 SSE queue（主进程通过 stdout pipe 接收后转 SSE）。
        # 所以 run_graph_task 内部如果用 SSECapture，要让它不传 queue。
        exit_code, stdout_text = run_graph_task(
            project_id=project_id,
            command=command,
            args=args,
            run_id=run_id,
            queue=None,  # 不传 queue，全部走 stdout
        )
        print(f"[subprocess] done exit_code={exit_code} stdout_len={len(stdout_text or '')}",
              flush=True)
        return exit_code
    except Exception as e:
        import traceback
        print(f"[subprocess] FATAL: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())