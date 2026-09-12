# -*- coding: utf-8 -*-
"""日志与全局异常兜底的回归测试。

背景：错误原先只 print 到控制台，办案人员在客户机上无从查看。这里锁住
"日志确实落盘、异常确实被记下、重复配置不出岔子"。
"""
import logging
import logging.handlers
import sys
import threading

import pytest

import log_utils


@pytest.fixture
def fresh_logging(monkeypatch):
    """重置 log_utils 的"只配置一次"状态、根 logger 处理器与全局钩子。

    用例之间必须隔离：这些全是进程级全局状态。
    """
    monkeypatch.setattr(log_utils, "_configured", False)
    monkeypatch.setattr(log_utils, "log_dir", "")

    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_hook = sys.excepthook
    saved_thread_hook = getattr(threading, "excepthook", None)
    root.handlers = []

    yield root

    root.handlers = saved_handlers
    root.setLevel(saved_level)
    sys.excepthook = saved_hook
    if saved_thread_hook is not None:
        threading.excepthook = saved_thread_hook


def _read(tmp_path):
    return (tmp_path / log_utils._FILE_NAME).read_text(encoding="utf-8")


# ============================================================================
# 落盘
# ============================================================================

class Test日志落盘:

    def test_在指定目录建日志文件(self, fresh_logging, tmp_path):
        log_utils.setup_logging(str(tmp_path))
        assert (tmp_path / log_utils._FILE_NAME).exists()

    def test_记录日志目录(self, fresh_logging, tmp_path):
        log_utils.setup_logging(str(tmp_path))
        assert log_utils.get_log_dir() == str(tmp_path)

    @pytest.mark.parametrize("level,text", [
        (logging.INFO, "一条信息"),
        (logging.WARNING, "一条告警"),
        (logging.ERROR, "一条错误"),
    ])
    def test_info及以上都写入文件(self, fresh_logging, tmp_path, level, text):
        log_utils.setup_logging(str(tmp_path))
        logging.getLogger("测试").log(level, text)
        assert text in _read(tmp_path)

    def test_用轮转处理器(self, fresh_logging, tmp_path):
        log_utils.setup_logging(str(tmp_path))
        fhs = [h for h in fresh_logging.handlers
               if isinstance(h, logging.handlers.RotatingFileHandler)]
        assert len(fhs) == 1
        assert fhs[0].maxBytes == log_utils._MAX_BYTES
        assert fhs[0].backupCount == log_utils._BACKUP_COUNT

    def test_日志带时间与级别(self, fresh_logging, tmp_path):
        log_utils.setup_logging(str(tmp_path))
        logging.getLogger("测试模块").error("格式检查")
        line = [l for l in _read(tmp_path).splitlines() if "格式检查" in l][0]
        assert "ERROR" in line and "测试模块" in line
        assert line[:4].isdigit(), "开头应是年份"


# ============================================================================
# 幂等与降级
# ============================================================================

class Test幂等与降级:

    def test_重复配置不叠加处理器(self, fresh_logging, tmp_path):
        log_utils.setup_logging(str(tmp_path))
        n = len(fresh_logging.handlers)
        log_utils.setup_logging(str(tmp_path))
        assert len(fresh_logging.handlers) == n

    def test_重复配置不重复写(self, fresh_logging, tmp_path):
        log_utils.setup_logging(str(tmp_path))
        log_utils.setup_logging(str(tmp_path))
        logging.getLogger("测试").error("唯一标记")
        assert _read(tmp_path).count("唯一标记") == 1

    def test_目录不可写时退回临时目录(self, fresh_logging, tmp_path):
        """程序目录装在 Program Files 等只读位置时不能崩，要退回临时目录"""
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("x", encoding="utf-8")

        log_utils.setup_logging(str(blocker / "子目录"))

        d = log_utils.get_log_dir()
        assert d, "应退回临时目录而不是放弃日志"
        assert str(blocker) not in d
        assert "工伤助手日志" in d

    def test_不可写时仍不抛异常(self, fresh_logging, tmp_path):
        blocker = tmp_path / "b.txt"
        blocker.write_text("x", encoding="utf-8")
        log_utils.setup_logging(str(blocker / "子"))   # 不应抛
        logging.getLogger("测试").error("照样能记")


# ============================================================================
# 全局异常兜底
# ============================================================================

class Test异常兜底:

    def test_未捕获异常写进日志(self, fresh_logging, tmp_path):
        log_utils.setup_logging(str(tmp_path))
        log_utils.install_excepthook()

        try:
            raise ValueError("兜底测试异常")
        except ValueError:
            sys.excepthook(*sys.exc_info())

        text = _read(tmp_path)
        assert "兜底测试异常" in text
        assert "ValueError" in text
        assert "Traceback" in text, "要留下完整调用栈才好定位"

    def test_键盘中断不被当成崩溃记录(self, fresh_logging, tmp_path, monkeypatch):
        log_utils.setup_logging(str(tmp_path))
        log_utils.install_excepthook()

        seen = []
        monkeypatch.setattr(sys, "__excepthook__",
                            lambda *a: seen.append(a[0]))
        try:
            raise KeyboardInterrupt()
        except KeyboardInterrupt:
            sys.excepthook(*sys.exc_info())

        assert seen == [KeyboardInterrupt], "KeyboardInterrupt 应交回默认处理"
        assert "KeyboardInterrupt" not in _read(tmp_path)

    def test_子线程异常写进日志(self, fresh_logging, tmp_path):
        log_utils.setup_logging(str(tmp_path))
        log_utils.install_excepthook()

        def boom():
            raise RuntimeError("线程兜底测试")

        t = threading.Thread(target=boom, name="测试线程")
        t.start()
        t.join()

        text = _read(tmp_path)
        assert "线程兜底测试" in text
        assert "测试线程" in text

    def test_重复安装不报错(self, fresh_logging, tmp_path):
        log_utils.setup_logging(str(tmp_path))
        log_utils.install_excepthook()
        log_utils.install_excepthook()
