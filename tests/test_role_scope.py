# -*- coding: utf-8 -*-
"""角色级数据隔离回归测试。

「用人单位」控件是四个角色共享的，它的 currentTextChanged 会把值写进
当前角色的人记录。这里锁住两件容易复发的事：

1. 非本人角色填自己的单位，不能污染案件级「用人单位」（全案文书共用它）
2. 加载案件时不能把案件级单位写进当前角色的人记录

这两条在本轮开发中各出了一个 bug，所以用测试固定住。
"""
import pytest


def _set_current_unit(win, value):
    """模拟用户在当前角色的「用人单位」控件里输入"""
    win._set_combo_or_type(win.company_pane, value)
    win.update_role_info(win.get_current_role_type())


# ============================================================================
# 案件级「用人单位」不被其它角色污染
# ============================================================================

class Test案件级单位不被污染:

    def test_本人单位即案件级用人单位(self, as_role):
        win = as_role("本人")
        _set_current_unit(win, "温州YY建筑劳务有限公司")
        assert win.get_data("用人单位", "") == "温州YY建筑劳务有限公司"
        assert win.get_data("本人单位名称", "") == "温州YY建筑劳务有限公司"

    @pytest.mark.parametrize("role,kwargs", [
        ("家属", {"unit": "温州XX服装有限公司", "position": "缝纫工", "identity": "夫妻"}),
        ("法人", {"position": "总经理", "identity": "法定代表人"}),
    ])
    def test_非本人角色的单位不污染案件级(self, as_role, role, kwargs):
        win = as_role("本人")
        _set_current_unit(win, "温州YY建筑劳务有限公司")

        win = as_role(role)
        _set_current_unit(win, "该角色自己的单位")

        assert win.get_data("用人单位", "") == "温州YY建筑劳务有限公司", \
            f"{role}的单位串进了案件级「用人单位」"
        from app_main import person_flat_key
        assert win.get_data(person_flat_key(role, "unit"), "") == "该角色自己的单位"

    def test_证人单位不污染案件级(self, as_role):
        win = as_role("本人")
        _set_current_unit(win, "温州YY建筑劳务有限公司")

        win = as_role("证人")
        _set_current_unit(win, "永嘉ZZ物流有限公司")

        assert win.get_data("用人单位", "") == "温州YY建筑劳务有限公司"
        assert win.get_data("证人单位名称", "") == "永嘉ZZ物流有限公司"

    def test_多个证人的单位各自独立(self, as_role):
        win = as_role("证人")
        win.data_model.witnesses = [
            {"role": "证人", "seq": "证人一", "name": "甲",
             "unit": "甲公司", "position": "钢筋工", "identity": "职工"},
            {"role": "证人", "seq": "证人二", "name": "乙",
             "unit": "乙公司", "position": "泥水工", "identity": "职工"},
        ]
        win.data_model.current_witness_index = 0
        win._show_witness_ui()
        assert win.company_pane.currentText() == "甲公司"

        win.witness_combo.setCurrentIndex(1)
        assert win.company_pane.currentText() == "乙公司", "切证人没换单位"
        assert win.get_data("证人单位名称", "") == "乙公司"


# ============================================================================
# 加载案件时的角色隔离
# ============================================================================

class Test加载案件不串角色:

    @pytest.fixture
    def case_obj(self):
        return {
            "case_id": "20260912001",
            "name": "赵六",
            "labor_unit": "温州YY建筑劳务有限公司",
            "employer": "永嘉县XX建设工程有限公司",
            "site": "ZZ新城项目一期工地",
            "case_nature": "工亡案件",
            "applicant_type": "个人申请",
            "unit_type": "企业",
            "family_reps": [{"role": "家属", "name": "赵妻",
                             "identity": "夫妻", "position": "缝纫工",
                             "unit": "温州XX服装有限公司"}],
        }

    def test_家属角色加载案件单位各就各位(self, as_role, case_obj):
        win = as_role("家属")
        win._apply_case_object(case_obj)
        assert win.get_data("用人单位", "") == "温州YY建筑劳务有限公司"
        assert win.company_pane.currentText() == "温州XX服装有限公司", \
            "家属角色下控件应显示家属自己的单位"
        assert win.get_data("家属身份", "") == "夫妻"

    def test_老档家属无单位时控件为空不被案件级填充(self, as_role):
        """老档 family_reps 没有 unit 键：控件必须为空，而不是显示死者单位"""
        win = as_role("家属")
        win._apply_case_object({
            "case_id": "old", "name": "王五",
            "labor_unit": "温州YY建筑劳务有限公司",
            "family_reps": [{"role": "家属", "name": "王母", "identity": "母子"}],
        })
        assert win.company_pane.currentText() == "", "家属单位被案件级值填充了"
        assert win.get_data("家属单位名称", "") == ""
        assert win.get_data("用人单位", "") == "温州YY建筑劳务有限公司"

    def test_本人角色加载案件控件显示案件级单位(self, as_role, case_obj):
        win = as_role("本人")
        win._apply_case_object(case_obj)
        assert win.company_pane.currentText() == "温州YY建筑劳务有限公司"

    def test_切换角色时单位控件不残留他人单位(self, as_role):
        win = as_role("本人")
        _set_current_unit(win, "温州YY建筑劳务有限公司")

        win = as_role("家属")
        assert win.company_pane.currentText() == "", "家属角色下残留了案件级单位"

        win = as_role("本人")
        assert win.company_pane.currentText() == "温州YY建筑劳务有限公司", \
            "切回本人没恢复案件级用人单位"


# ============================================================================
# 家属字段语义
# ============================================================================

class Test家属字段语义:

    def test_身份行标签随角色变化(self, as_role):
        for role, expect in [("本人", "本人身份："), ("证人", "证人身份："),
                             ("法人", "法人身份："), ("家属", "与死者关系：")]:
            win = as_role(role)
            assert win.identity_label.text() == expect

    def test_家属三个字段各归其位(self, as_role):
        win = as_role("家属")
        win._set_combo_or_type(win.company_pane, "温州XX服装有限公司")
        win.lineEdit_5.setText("缝纫工")
        win.identity_edit.setText("夫妻")
        win.update_role_info("家属")

        assert win.get_data("家属单位名称", "") == "温州XX服装有限公司"
        assert win.get_data("家属岗位", "") == "缝纫工"
        assert win.get_data("家属身份", "") == "夫妻"

    def test_不再把关系写进岗位槽(self, as_role):
        win = as_role("家属")
        win.identity_edit.setText("母子")
        win.update_role_info("家属")
        assert win.get_data("家属岗位", "") == "", "关系串进了岗位槽"
        assert win.get_data("家属身份", "") == "母子"

    def test_模板表头三字段(self, as_role):
        win = as_role("家属")
        win._set_combo_or_type(win.company_pane, "温州XX服装有限公司")
        win.lineEdit_5.setText("缝纫工")
        win.identity_edit.setText("夫妻")
        win.update_role_info("家属")

        tpl = win._build_family_template_data({"unit_type": "企业"})
        assert tpl["家属单位名称"] == "温州XX服装有限公司"
        assert tpl["家属岗位"] == "缝纫工"
        assert tpl["与死者关系"] == "夫妻"

    def test_家属无单位时岗位一并留空(self, as_role):
        win = as_role("家属")
        win._set_combo_or_type(win.company_pane, "")
        win.lineEdit_5.setText("缝纫工")
        win.identity_edit.setText("母子")
        win.update_role_info("家属")

        tpl = win._build_family_template_data({"unit_type": "企业"})
        assert tpl["家属单位名称"] == ""
        assert tpl["家属岗位"] == "", "家属没单位时岗位应留空"
        assert tpl["与死者关系"] == "母子"

    def test_家属身份不兜底成职工(self, as_role):
        """家属这一栏是与死者关系，不该像其它角色那样兜底成「职工」"""
        win = as_role("家属")
        win.identity_edit.clear()
        win.update_role_info("家属")

        tpl = win._build_family_template_data({"unit_type": "企业"})
        assert tpl["与死者关系"] == ""
        assert tpl["家属身份"] == ""
        assert win.identity_edit.placeholderText() == ""

    def test_本人身份仍兜底职工(self, as_role):
        """回归：非家属角色保持原有兜底行为"""
        win = as_role("本人")
        assert win.identity_edit.placeholderText() == "职工"


# ============================================================================
# 清空输入框必须真正清掉数据
# ============================================================================

class Test清空字段生效:
    """回归：清空输入框后旧值必须从扁平键里消失。

    原先 _person_to_flat / _mirror_witness_to_flat 只写非空值，
    于是"清空"传不进去——办错字改回去、删掉不该有的岗位，下次保存
    仍会把旧值写进笔录与文书。
    """

    def test_清空家属岗位(self, as_role):
        win = as_role("家属")
        win.lineEdit_5.setText("缝纫工")
        win.update_role_info("家属")
        assert win.get_data("家属岗位", "") == "缝纫工"

        win.lineEdit_5.clear()
        win.update_role_info("家属")
        assert win.get_data("家属岗位", "") == "", "清空后旧值仍在"

    def test_清空家属关系(self, as_role):
        win = as_role("家属")
        win.identity_edit.setText("夫妻")
        win.update_role_info("家属")
        assert win.get_data("家属身份", "") == "夫妻"

        win.identity_edit.clear()
        win.update_role_info("家属")
        assert win.get_data("家属身份", "") == ""

    def test_清空家属单位后模板岗位一并清空(self, as_role):
        """端到端：清掉单位后，笔录表头不该再出现旧单位和旧岗位"""
        win = as_role("家属")
        win._set_combo_or_type(win.company_pane, "温州XX服装有限公司")
        win.lineEdit_5.setText("缝纫工")
        win.update_role_info("家属")

        win._set_combo_or_type(win.company_pane, "")
        win.lineEdit_5.clear()
        win.update_role_info("家属")

        tpl = win._build_family_template_data({"unit_type": "企业"})
        assert tpl["家属单位名称"] == ""
        assert tpl["家属岗位"] == ""

    def test_清空本人姓名(self, as_role):
        win = as_role("本人")
        win.name_pane.setText("张三")
        win.update_role_info("本人")
        assert win.get_data("本人姓名", "") == "张三"

        win.name_pane.clear()
        win.update_role_info("本人")
        assert win.get_data("本人姓名", "") == "", "清空后旧值仍在"

    def test_清空本人岗位(self, as_role):
        win = as_role("本人")
        win.lineEdit_5.setText("泥水工")
        win.update_role_info("本人")
        assert win.get_data("本人岗位", "") == "泥水工"

        win.lineEdit_5.clear()
        win.update_role_info("本人")
        assert win.get_data("本人岗位", "") == ""

    def test_清空证人字段(self, as_role):
        win = as_role("证人")
        win.data_model.witnesses = [{"role": "证人", "seq": "证人一", "name": "李四"}]
        win.data_model.current_witness_index = 0

        win.name_pane.setText("李四")
        win.lineEdit_5.setText("钢筋工")
        win._sync_form_to_current_witness()
        assert win.get_data("证人岗位", "") == "钢筋工"

        win.lineEdit_5.clear()
        win._sync_form_to_current_witness()
        assert win.get_data("证人岗位", "") == "", "清空后旧值仍在"

    def test_清空法人职务(self, as_role):
        win = as_role("法人")
        win.lineEdit_5.setText("总经理")
        win.update_role_info("法人")
        assert win.get_data("法人职务", "") == "总经理"

        win.lineEdit_5.clear()
        win.update_role_info("法人")
        assert win.get_data("法人职务", "") == ""
