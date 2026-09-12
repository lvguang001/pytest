# -*- coding: utf-8 -*-
"""共享 fixture。

MainWindow 初始化很重（路径/服务/AI 客户端/组合框数据），所以整个会话只建一次，
用例之间靠 fixture 收尾重置状态。BASE_PATH 指向临时目录，避免碰真实案卷。
"""
import pytest


@pytest.fixture(scope="session")
def qapp():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="session")
def win(qapp, tmp_path_factory):
    """会话级 MainWindow，数据目录隔离到临时目录"""
    import app_main as A
    w = A.MainWindow()
    w.BASE_PATH = str(tmp_path_factory.mktemp("cases"))
    return w


@pytest.fixture
def as_role(win):
    """切到指定角色并清空表单（等价于用户点角色单选按钮）。

    用例结束后回到「本人」并清掉案件级「用人单位」，避免用例之间串状态。
    """
    def _switch(role):
        btn = {"本人": win.radioButton, "证人": win.radioButton_2,
               "法人": win.radioButton_3, "家属": win.radioButton_4}[role]
        btn.setChecked(True)
        win.clear_role_fields()
        return win

    yield _switch

    win.radioButton.setChecked(True)
    win.clear_role_fields()
    win.set_data("用人单位", "", "company")
    win._set_combo_or_type(win.company_pane, "")
