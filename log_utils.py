# -*- coding: utf-8 -*-
"""统一日志与全局异常兜底。

背景：程序里的错误/告警原先只 print 到控制台，而办案人员是在客户机上双击运行的，
控制台根本看不见——出了问题手里零线索。这里把日志落到轮转文件，并挂上全局异常钩子。

用法：入口处调用 setup_logging() + install_excepthook()；
各模块直接用 logging.getLogger(__name__)，消息会冒泡到根 logger 的处理器。
"""
import logging
import logging.handlers
import os
import sys
import tempfile
import threading
import traceback

APP_LOGGER_NAME = "工伤助手"
_FILE_NAME = "app.log"
_MAX_BYTES = 2 * 1024 * 1024   # 单个日志文件上限 2MB
_BACKUP_COUNT = 5              # 轮转保留 5 个历史文件

_configured = False


def _writable_dir(preferred):
    """挑一个可写目录：优先程序目录，不可写（如装在 Program Files）时退回临时目录。"""
    candidates = [preferred, os.path.join(tempfile.gettempdir(), "工伤助手日志")]
    for d in candidates:
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write_probe")
            with open(probe, "w", encoding="utf-8"):
                pass
            os.remove(probe)
            return d
        except Exception:
            continue
    return ""


def setup_logging(preferred_dir=None, level=logging.INFO,
                  console_level=logging.WARNING):
    """配置根 logger：轮转文件 + 控制台。可重复调用，只生效一次。

    文件收 INFO 及以上（完整留痕）；控制台只收 WARNING 及以上，避免与程序里
    大量既有的 print 重复刷屏。
    """
    global _configured
    root = logging.getLogger()
    if _configured:
        return root

    fmt = logging.Formatter("%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    d = _writable_dir(preferred_dir)
    if d:
        try:
            fh = logging.handlers.RotatingFileHandler(
                os.path.join(d, _FILE_NAME), maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT, encoding="utf-8")
            fh.setFormatter(fmt)
            fh.setLevel(level)
            root.addHandler(fh)
        except Exception as e:
            print(f"⚠️ 无法创建日志文件: {e}")

    # pytest 会接管 sys.stderr，这里不插控制台处理器以免干扰其捕获
    if "pytest" not in sys.modules:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(console_level)
        root.addHandler(sh)

    root.setLevel(level)
    for noisy in ("urllib3", "requests", "charset_normalizer", "PIL", "docx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    logging.getLogger(APP_LOGGER_NAME).info(
        "日志已启动，目录: %s", d or "（无可写目录，仅控制台）")
    return root


def install_excepthook():
    """把未捕获异常写进日志。

    注意：PyQt5 调用完 sys.excepthook 后仍会 qFatal() 中止进程，所以这里只保证
    「留下证据」，不能靠它让程序活下去。要真正不崩，得单独给槽函数加保护。
    """
    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logging.getLogger(APP_LOGGER_NAME).critical(
            "未捕获异常:\n%s",
            "".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = _hook

    # 子线程里的未捕获异常（Qt 的 QThread 走 Python 线程时也在此兜住）
    if hasattr(threading, "excepthook"):
        def _thread_hook(args):
            if issubclass(args.exc_type, SystemExit):
                return
            logging.getLogger(APP_LOGGER_NAME).critical(
                "线程 %s 未捕获异常:\n%s",
                getattr(args.thread, "name", "?"),
                "".join(traceback.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback)))

        threading.excepthook = _thread_hook


def install_qt_message_handler():
    """把 Qt 自己的告警（布局/绘制/信号槽等）也收进日志。"""
    try:
        from PyQt5.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:
        return

    logger = logging.getLogger(f"{APP_LOGGER_NAME}.Qt")
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def _handler(msg_type, context, message):
        logger.log(levels.get(msg_type, logging.INFO), message)

    qInstallMessageHandler(_handler)
