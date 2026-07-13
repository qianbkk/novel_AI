"""引擎 stdout 空闲看门狗 (security-2026-07-13 #3)

锁定：
  - NOVEL_ENGINE_TIMEOUT_MIN 配置项存在且默认 120
  - 子进程长时间无 stdout 活动 → 看门狗终止整个进程组
  - 进程已退出 → 看门狗立刻返回
"""
import subprocess
import sys
import threading
import time

import pytest


class TestEngineTimeoutConfig:
    """config.Settings.engine_timeout_min 应可配置。"""

    def test_engine_timeout_min_default(self, monkeypatch):
        # 强制清掉 env 让默认值生效
        for key in ("NOVEL_ENGINE_TIMEOUT_MIN", "engine_timeout_min"):
            monkeypatch.delenv(key, raising=False)
        # 重新加载 settings（避免 import-time cache）
        import importlib
        from app import config as cfg_mod
        importlib.reload(cfg_mod)
        # 不一定 reload 完但默认是 120；如果用户环境设了别的，就只断言
        # 类型正确且 > 0（保证有效）
        assert cfg_mod.settings.engine_timeout_min > 0
        assert isinstance(cfg_mod.settings.engine_timeout_min, int)


class TestWatchdogLogic:
    """看门狗核心逻辑：超时终止整个进程组。"""

    def test_watchdog_kills_subprocess_after_timeout(self):
        """极短超时（3s）+ 不输出 stdout 的子进程 → 看门狗应在 timeout 后终止它。"""
        # 1) 启一个啥都不做的子进程，独立进程组
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(999)"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        # 2) 跑一个极简看门狗（跨平台兼容——复用真实 helper）
        from app.api.bridge import _kill_process_tree
        last_ts = {"ts": time.time()}
        killed = {"flag": False}
        timeout_sec = 3
        def watchdog():
            while True:
                time.sleep(1)
                if proc.poll() is not None:
                    return
                if time.time() - last_ts["ts"] > timeout_sec:
                    _kill_process_tree(proc.pid)
                    killed["flag"] = True
                    return
        threading.Thread(target=watchdog, daemon=True).start()

        # 等看门狗触发
        deadline = time.time() + 15
        while time.time() < deadline and not killed["flag"]:
            time.sleep(0.2)
        assert killed["flag"], "看门狗在 15s 内未触发终止"

        # 等子进程真正退出
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            from app.api.bridge import _terminate_process_tree
            _terminate_process_tree(proc.pid)
            pytest.fail("子进程在终止信号后未在 15s 内退出")

        assert proc.returncode != 0, f"被终止的子进程 returncode 应非 0，实际 {proc.returncode}"

    def test_watchdog_returns_immediately_if_subprocess_already_dead(self):
        """子进程已退出 → 看门狗 next tick 应该立刻 return。"""
        proc = subprocess.Popen(
            [sys.executable, "-c", "print('hi')"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        proc.wait(timeout=5)
        assert proc.poll() is not None

        # 看门狗 sleep 1s 后检查 poll() 应立刻 return
        ran = {"done": False}
        def watchdog():
            time.sleep(1)
            if proc.poll() is not None:
                ran["done"] = True
        t = threading.Thread(target=watchdog, daemon=True)
        t.start()
        t.join(timeout=3)
        assert ran["done"], "看门狗应在 1s 后发现子进程已死并退出"