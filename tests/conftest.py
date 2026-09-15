# -*- coding: utf-8 -*-
"""pytest 公共夹具。

**先改路径，再 import app_main。** MainWindow 构造时会用 `path_utils` 的路径，
指错了就会读到、甚至写到真实案卷。所以下面这段必须在模块顶层、
在任何跟 app_main 相关的 import 之前跑完。
"""

import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── 沙箱：所有路径指向临时目录，绝不碰真实案卷 ──
SANDBOX = pathlib.Path(tempfile.mkdtemp(prefix="gongshang_test_"))
for _sub in ("storage", "templates", "templates/谈话模板", "templates/文书模板",
             "config", "data"):
    (SANDBOX / _sub).mkdir(parents=True, exist_ok=True)

import path_utils  # noqa: E402  （必须在这之后才能 import app_main）

_pu = path_utils.path_utils
_pu.get_storage_path = lambda *a: SANDBOX / "storage"
_pu.get_template_path = lambda *a: SANDBOX / "templates"
_pu.get_config_path = lambda *a: SANDBOX / "config"
_pu.get_data_path = lambda *a: SANDBOX / "data"
_pu.get_talk_template_path = lambda *a: SANDBOX / "templates" / "谈话模板"
_pu.get_document_template_path = lambda *a: SANDBOX / "templates" / "文书模板"


@pytest.fixture(scope="session")
def qapp():
    """整个会话共用一个 QApplication（建两个会崩）。"""
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="session")
def window(qapp):
    """MainWindow 构造很重（路径、服务、AI 客户端、组合框数据），整个会话只建一次。

    必须 `show()` 一次：**布局管理器要到窗口显示时才把控件摆到位**，
    没 show 之前读到的还是 `ui_main_window.py` 里那套绝对坐标。
    加 `WA_DontShowOnScreen` 是为了别在跑测试时真弹一个窗口出来。

    需要改状态的用例请用 `fresh_window`，不要直接动这个。
    """
    from PyQt5.QtCore import Qt
    import app_main

    w = app_main.MainWindow()
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.show()
    qapp.processEvents()
    yield w
    w.close()


def reset_window(w):
    """把窗口恢复到「刚启动」的状态：本人角色、两个复选框不勾、表单空白。"""
    w.deathCaseCheckbox.setChecked(False)
    w.personalApplicationCheckbox.setChecked(False)
    w.radioButton.setChecked(True)
    w.comboBox.setCurrentIndex(0)
    w.clear_role_fields()
    w.current_case_id = ""
    w._refresh_evidence_list()


@pytest.fixture
def fresh_window(window):
    """会改界面状态的用例用这个：前后各复位一次。"""
    reset_window(window)
    yield window
    reset_window(window)
