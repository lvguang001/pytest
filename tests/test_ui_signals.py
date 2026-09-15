# -*- coding: utf-8 -*-
"""信号连接回归测试。

背景（详见 refactor/CONTROLS.md §4.2）：`ui_main_window.py` 的 `setupUi()`
除了显式 `connect`，末尾还有一句 `QMetaObject.connectSlotsByName(Form)`，
它会把**任何** `on_<子控件名>_<信号>` 形状的方法自动接上——而且
`clicked()` / `clicked(bool)` 两个重载各接一条。

「谈话通知书」原先就是这么中招的：`.ui` 显式 1 条 + 自动 2 条 +
`__init__` 1 条，**点一次跑 4 次**。现在统一「先全部 disconnect、再接一条」。
"""

import inspect
import re

import app_main


def _click_once(window, button, patched_name, replacement):
    """把按钮真正点一下，返回被替换掉的那个函数被调用的次数。"""
    original = getattr(window, patched_name)
    calls = []

    def spy(*a, **k):
        calls.append(1)
        return replacement(*a, **k)

    setattr(window, patched_name, spy)
    try:
        button.click()
    finally:
        setattr(window, patched_name, original)
    return len(calls)


def test_talk_notice_button_fires_slot_once(fresh_window):
    """「谈话通知书」按钮点一次，生成函数只能跑一次。"""
    n = _click_once(fresh_window, fresh_window.pushButton_12,
                    "generate_interview_notice_from_approval", lambda *a, **k: None)
    assert n == 1, f"点一次「谈话通知书」跑了 {n} 次（重复连接又回来了？）"


def test_talk_transcript_button_fires_slot_once(fresh_window):
    """「谈话笔录」按钮同理。"""
    n = _click_once(fresh_window, fresh_window.pushButton,
                    "open_data_review", lambda *a, **k: False)
    assert n == 1, f"点一次「谈话笔录」跑了 {n} 次"


def test_role_buttons_fire_slot_once(fresh_window):
    """四个角色单选在 .ui 里被接过，不能再被 _connect_signals 接第二遍。"""
    n = _click_once(fresh_window, fresh_window.radioButton_3,
                    "_clear_role_data", lambda *a, **k: None)
    assert n == 1, f"点一次角色单选跑了 {n} 次"


def test_no_new_auto_connected_slots(window):
    """守卫：别新增 `on_<子控件名>_<信号>` 形状的方法。

    这种名字会被 `connectSlotsByName` 悄悄自动接上，等你发现时已经多跑了几遍。
    目前只有 `on_pushButton_12_clicked` 命中，它在 `_connect_signals()` 里
    已经「先断开再连」处理过了。真要新增，请一并处理并更新这个清单。
    """
    from PyQt5.QtWidgets import QWidget

    child_names = set()

    def walk(w):
        for c in w.children():
            if isinstance(c, QWidget):
                if c.objectName():
                    child_names.add(c.objectName())
                walk(c)

    walk(window)

    pattern = re.compile(
        r"^on_(.+)_(clicked|changed|finished|toggled|pressed|selected|"
        r"textChanged|stateChanged|currentIndexChanged)$")
    methods = [n for n, _ in inspect.getmembers(app_main.MainWindow, inspect.isfunction)]
    hits = sorted(n for n in methods
                  if (m := pattern.match(n)) and m.group(1) in child_names)

    assert hits == ["on_pushButton_12_clicked"], (
        f"出现了会被 connectSlotsByName 自动连接的方法名：{hits}\n"
        "改个名字，或者照 on_pushButton_12_clicked 那样先 disconnect() 再接。")
