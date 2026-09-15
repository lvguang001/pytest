# -*- coding: utf-8 -*-
"""主界面行为回归测试 —— 界面改布局之后，这些「点出来的行为」不能变。

对应的验证方法：改界面之前和之后各跑一遍，输出应当完全一致。
"""


def test_role_switch_updates_identity_label(fresh_window):
    """「身份」这一栏的标签随角色变；家属那一栏填的是与死者关系。"""
    w = fresh_window
    for rb, expected in ((w.radioButton, "身份："),
                         (w.radioButton_2, "身份："),
                         (w.radioButton_3, "身份："),
                         (w.radioButton_4, "关系：")):
        rb.setChecked(True)
        w.clear_role_fields()
        assert w.identity_label.text() == expected, (
            f"{w.get_current_role_type()} 角色下标签是 {w.identity_label.text()!r}")


def test_case_unit_does_not_leak_into_other_roles(fresh_window):
    """`company_pane` 四个角色共用，切角色必须按角色回填 —— 见 CONTROLS.md 的坑点说明。"""
    w = fresh_window
    w.company_pane.setCurrentText("测试用人单位")
    assert w.get_data('用人单位') == "测试用人单位"

    w.radioButton_2.setChecked(True)          # 切到证人
    w.clear_role_fields()
    assert "测试用人单位" not in w.company_pane.currentText(), (
        "案件级用人单位串到证人角色上了")

    w.radioButton.setChecked(True)            # 切回本人
    w.clear_role_fields()
    assert w.company_pane.currentText() == "测试用人单位", "切回本人没把案件级用人单位还原"


def test_witness_row_is_always_visible(fresh_window):
    """证人编号行改成常显了，不要加回「随角色隐藏」。"""
    w = fresh_window
    for rb in (w.radioButton, w.radioButton_2, w.radioButton_3, w.radioButton_4):
        rb.setChecked(True)
        w.clear_role_fields()
        for name in ("witness_label", "witness_combo", "add_witness_btn"):
            assert not getattr(w, name).isHidden(), (
                f"{w.get_current_role_type()} 角色下 {name} 被藏起来了")


def test_evidence_list_is_filled_on_startup(fresh_window):
    assert len(fresh_window.material_list.get_materials()) > 0


def test_evidence_list_follows_case_type(fresh_window):
    """切到工亡案件，「死亡证明」应当出现在材料清单里。"""
    w = fresh_window
    w.deathCaseCheckbox.setChecked(True)
    names = [m["name"] for m in w.material_list.get_materials()]
    assert any("死亡" in n for n in names), f"工亡案件的材料清单里没有死亡相关项: {names}"

    w.deathCaseCheckbox.setChecked(False)
    names = [m["name"] for m in w.material_list.get_materials()]
    assert not any("死亡" in n for n in names), "切回工伤案件后死亡证明没被清掉"


def test_evidence_list_follows_regulation(fresh_window):
    w = fresh_window
    w.comboBox.setCurrentIndex(0)
    first = {m["name"] for m in w.material_list.get_materials()}
    w.comboBox.setCurrentIndex(len(w.comboBox) - 1)
    last = {m["name"] for m in w.material_list.get_materials()}
    assert first != last, "换了拟用条例，材料清单没跟着变"


def test_overlays_toggle(fresh_window):
    """两个浮层默认收起，点开有、再点收。"""
    w = fresh_window
    assert w.api_group.isHidden() and w.todo_board.isHidden()

    w._toggle_config_panel()
    assert not w.api_group.isHidden()
    w._toggle_config_panel()
    assert w.api_group.isHidden()

    w._toggle_todo_panel()
    assert not w.todo_board.isHidden()
    w._toggle_todo_panel()
    assert w.todo_board.isHidden()


def test_f2_cycles_test_data(fresh_window):
    """F2 轮换测试数据：索引推进、表单被填上。"""
    w = fresh_window
    before = w._test_data_index
    w.keyPressEvent(_f2_event())
    assert w._test_data_index != before or len(w.TEST_DATA_PRESETS) == 1
    assert w.name_pane.text(), "F2 之后姓名是空的，测试数据没填进去"


def test_clear_role_fields_empties_the_form(fresh_window):
    w = fresh_window
    w.name_pane.setText("张三")
    w.idnumer_pane.setText("330102199001011234")
    w.clear_role_fields()
    assert w.name_pane.text() == ""
    assert w.idnumer_pane.text() == ""


def _f2_event():
    from PyQt5.QtCore import QEvent, Qt
    from PyQt5.QtGui import QKeyEvent
    return QKeyEvent(QEvent.KeyPress, Qt.Key_F2, Qt.NoModifier)
