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
        """标签一律用短词——那行要和 电话/岗位 并排，长标签会把输入框挤没。

        完整语义（如「家属这一栏填与死者的关系」）放在 tooltip 里。
        """
        for role, expect in [("本人", "身份："), ("证人", "身份："),
                             ("法人", "身份："), ("家属", "关系：")]:
            win = as_role(role)
            assert win.identity_label.text() == expect

    def test_长标签不压住岗位输入框(self, as_role):
        """回归：原先按文本长度动态加宽标签，会左移盖住岗位输入框"""
        for role in ("本人", "证人", "法人", "家属"):
            win = as_role(role)
            lab = win.identity_label.geometry()
            post = win.lineEdit_5.geometry()
            assert post.x() + post.width() <= lab.x(), f"{role}：标签压住了岗位输入框"

    def test_身份行并入电话岗位行(self, win):
        """身份原独占一行，现应和 电话/岗位 同排，且空出来的行已收掉"""
        ys = [getattr(win, n).geometry().y()
              for n in ("label_5", "label_13", "identity_label")]
        assert len(set(ys)) == 1, "三个标签应在同一行"

        ins = [getattr(win, n).geometry().y()
               for n in ("lineEdit_4", "lineEdit_5", "identity_edit")]
        assert len(set(ins)) == 1, "三个输入框应在同一行"

    def test_电话岗位输入框已缩短(self, win):
        assert win.lineEdit_4.width() < 161, "电话输入应比原来短"
        assert win.lineEdit_5.width() < 111, "岗位输入应比原来短"

    def test_同行三个字段不重叠且不越界(self, win):
        phone, post, ident = (win.lineEdit_4, win.lineEdit_5, win.identity_edit)
        assert phone.x() + phone.width() <= win.label_13.x()
        assert post.x() + post.width() <= win.identity_label.x()
        assert ident.x() + ident.width() <= 441, "应与其它行的右缘 441 对齐"

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


# ============================================================================
# 「信息提示」方框
# ============================================================================

class Test信息提示方框:
    """提示消息统一显示在带边框的方框里，且长消息换行而不是被裁掉。"""

    def test_方框存在且包住消息标签(self, win):
        box = win.status_box.geometry()
        lab = win.label_14.geometry()
        assert box.contains(lab), "消息标签应在方框内"
        assert box.width() > lab.width() and box.height() > lab.height()

    def test_方框有边框样式(self, win):
        css = win.status_box.styleSheet()
        assert "border" in css and "status_box" in css

    def test_长消息自动换行(self, win):
        assert win.label_14.wordWrap() is True

    def test_提示标签与首行对齐(self, win):
        assert win.label_15.y() == win.label_14.y()

    def test_默认消息进方框(self, win):
        win._set_status("数据已核对并保存", "green")
        assert win.label_14.text() == "数据已核对并保存"
        assert "green" in win.label_14.styleSheet()

    def test_身份证提示框常态为空(self, win):
        """原先固定显示「信息提示」四个字，现应是空框"""
        assert win.label_12.text() == ""
        box = win.status_box_id.geometry()
        lab = win.label_12.geometry()
        assert box.contains(lab), "身份证提示标签应在方框内"
        assert "border" in win.status_box_id.styleSheet()

    def test_身份证提示仍是独立的框(self, win):
        """两处提示各自独立：主框的消息不该串到身份证框"""
        win._set_status("数据已保存", "green")
        assert win.label_14.text() == "数据已保存"
        assert win.label_12.text() == ""

    def test_身份证提示显示校验消息(self, win):
        win._set_status("身份证号格式错误", "red", "label_12")
        assert win.label_12.text() == "身份证号格式错误"
        assert "red" in win.label_12.styleSheet()

    def test_清空字段后两处提示都空(self, win):
        """clear_fields 原先会把「信息提示」写回两处，与方框设计冲突"""
        win._set_status("数据已保存", "green")
        win._set_status("此人已经超龄", "red", "label_12")
        win.clear_fields()
        assert win.label_14.text() == ""
        assert win.label_12.text() == ""


# ============================================================================
# 「案件申请陈述」已从数据链路断开
# ============================================================================

class Test案件申请陈述已断开:
    """输入框仍在界面上（待重新设计），但已不再影响案件数据。

    注意：断开的是**界面**，不是数据——`{{受伤经过}}` 出现在告知书、审批表、
    谈话通知书等 5 个文书模板里，必须还有值可取。
    """

    def test_输入框不影响收集到的数据(self, win):
        win.set_data("受伤经过", "", "investigation")
        win.statement_edit.setPlainText("我在框里打的字")
        data, _, _, _ = win._collect_review_data()
        assert data["injury_desc"] == "", "陈述框仍在影响案件数据"

    def test_受伤经过改从数据模型取(self, win):
        win.set_data("受伤经过", "数据模型里的内容", "investigation")
        data, _, _, _ = win._collect_review_data()
        assert data["injury_desc"] == "数据模型里的内容"

    def test_加载案件不回填输入框(self, win):
        win.statement_edit.setPlainText("残留旧字")
        win._apply_case_object({"case_id": "c1",
                                "injury_description": "案卷里的受伤经过"})
        assert win.statement_edit.toPlainText() == "", "陈述框被回填了，会误导用户"
        assert win.get_data("受伤经过", "") == "案卷里的受伤经过"

    def test_受伤经过仍能到达文书模板(self, win):
        """断开界面不等于丢数据——5 个文书模板都靠 {{受伤经过}}"""
        win._apply_case_object({"case_id": "c1", "injury_description": "案卷内容"})
        data, materials, _, _ = win._collect_review_data()
        case = win._build_case_object(data, materials)

        assert case["injury_description"] == "案卷内容"
        assert win._build_unified_template_data(case).get("受伤经过") == "案卷内容"

    def test_存档往返不丢受伤经过(self, win, tmp_path, monkeypatch):
        monkeypatch.setattr(win, "BASE_PATH", str(tmp_path))
        win._apply_case_object({"case_id": "c1", "injury_description": "案卷内容"})
        data, materials, _, _ = win._collect_review_data()
        win._save_cases_data({"c1": win._build_case_object(data, materials)})

        assert win._load_cases_data()["c1"]["injury_description"] == "案卷内容"

    def test_F2预设的陈述仍进数据模型(self, win):
        """预设里的陈述原先靠输入框带入，断开后要改走数据模型"""
        win._cycle_test_data()
        assert win.get_data("受伤经过", "") != "", "F2 预设的陈述没进数据模型"


# ============================================================================
# 条例目录
# ============================================================================

class Test条例目录:

    def test_第十四条第七项已移除(self):
        """兜底条款（法律行政法规规定的其他情形）实际极少用到，已整体去掉"""
        from case_classifier import CaseClassifier
        assert "第十四条第（七）项" not in CaseClassifier.REGULATIONS
        assert len(CaseClassifier.REGULATIONS) == 9

    def test_下拉框不含第七项(self, win):
        from case_classifier import CaseClassifier
        texts = [win.comboBox.itemText(i) for i in range(win.comboBox.count())]
        assert not any("第七项" in t for t in texts)
        assert win.comboBox.count() == len(CaseClassifier.REGULATIONS)

    def test_F2预设选中的条例与预期一致(self, win):
        """回归：预设原先用下拉索引定位，删掉中间一项后工亡预设会静默指错"""
        import app_main as A
        for _ in range(len(A.TEST_DATA_PRESETS)):
            win._cycle_test_data()
            # 读「实际被应用」的那一组：F2 索引可能已被前面的用例推进过，
            # 不能假设它从 0 开始
            preset = A.TEST_DATA_PRESETS[win._test_data_index]
            expect = A._regulation_short_to_full(preset["regulation"])
            assert win.comboBox.currentText() == expect, \
                f"{preset['name']} 选中的条例不对"


# ============================================================================
# 证据清单（条例 × 案件性质 × 申请类型）
# ============================================================================

class Test证据清单:

    def _req(self, reg, death, personal, unit="企业"):
        from app_main import compose_evidence
        return [n for n, r in compose_evidence(reg, death, personal, unit) if r]

    def _all(self, reg, death, personal, unit="企业"):
        from app_main import compose_evidence
        return [n for n, _ in compose_evidence(reg, death, personal, unit)]

    def test_通用必要项(self):
        assert self._req("第十四条第（一）项", False, False) == \
            ["身份证", "劳动合同", "医院诊断证明"]

    def test_条例特有项(self):
        """交通事故那一条，「非本人主要责任」靠认定书，属必要"""
        assert "道路交通事故认定书" in self._req("第十四条第（六）项", False, False)
        assert "职业病诊断证明" in self._req("第十四条第（四）项", False, False)

    def test_工亡追加死亡证明(self):
        """回归：工亡 + 第十四条第（一）项 是合法组合。

        原先只按条例取证据，会漏掉死亡证明——工亡案没它办不下去。
        """
        assert "死亡证明" in self._req("第十四条第（一）项", True, False)
        assert "死亡证明" not in self._req("第十四条第（一）项", False, False)

    def test_个人申请追加劳动关系佐证(self):
        allx = self._all("第十四条第（一）项", False, True)
        assert "劳动关系裁决书" in allx
        assert "劳动关系裁决书" not in self._all("第十四条第（一）项", False, False)

    def test_个人工亡追加近亲属关系证明(self):
        """工亡由近亲属代为申请，要证明「有资格申请」"""
        req = self._req("第十五条第（一）项", True, True)
        assert any("近亲属关系证明" in n for n in req)
        assert "申请人身份证" in req
        assert not any("近亲属关系证明" in n
                       for n in self._req("第十五条第（一）项", True, False)), \
            "单位申请的工亡案不该要近亲属关系证明"

    def test_机关公务员换成人事关系证明(self):
        """公务员不是劳动合同关系——劳动合同降为可能，换成录用与在编证明"""
        req = self._req("第十四条第（一）项", False, False, "机关（公务员）")
        assert "劳动合同" not in req
        assert "公务员录用审批文件" in req
        assert "公务员在编证明" in req
        assert "劳动合同" in self._all("第十四条第（一）项", False, False, "机关（公务员）")

    def test_事业单位用聘用合同(self):
        req = self._req("第十四条第（一）项", False, False, "事业单位")
        assert "劳动合同" not in req
        assert "聘用合同" in req
        assert "事业单位在编证明" in req

    def test_企业不受影响(self):
        assert self._req("第十四条第（一）项", False, False, "企业") == \
            ["身份证", "劳动合同", "医院诊断证明"]

    def test_同名只留一条且按必要算(self):
        """死亡证明同时出现在条例层与工亡层，不能列两遍"""
        from app_main import compose_evidence
        items = compose_evidence("第十五条第（一）项", True, False)
        names = [n for n, _ in items]
        assert names.count("死亡证明") == 1
        assert ("死亡证明", True) in items

    def test_必要排在可能之前(self):
        from app_main import compose_evidence
        flags = [r for _, r in compose_evidence("第十五条第（一）项", True, True)]
        assert flags == sorted(flags, reverse=True), "必要项应全部排在可能项前面"


class Test证据清单界面:

    @pytest.fixture(autouse=True)
    def _reset(self, win):
        """两个复选框是共享状态，每个用例先归零"""
        win.death_case_checkbox.setChecked(False)
        win.personal_application_checkbox.setChecked(False)
        win.material_list.clear()
        win._refresh_evidence_list()
        yield

    def _names(self, win):
        return [r["_name_edit"].text() for r in win.material_list._rows]

    def test_启动时按当前条例列出(self, win):
        from app_main import compose_evidence
        expect = [n for n, _ in compose_evidence("第十四条第（一）项", False, False)]
        assert self._names(win) == expect

    def test_切到工亡会补上死亡证明(self, win):
        """回归：apply_evidence_list 曾在删除旧行之前算「已存在」，
        导致刚删掉的名字永远加不回来——死亡证明就是这么丢的"""
        win.death_case_checkbox.setChecked(True)
        assert "死亡证明" in self._names(win)

    def test_切换后原有项不会消失(self, win):
        """回归：apply_evidence_list 曾在删除旧行**之前**算「已存在」这个名字集合，
        于是旧行删掉后同名项被当成「已经有了」跳过、不再加回——
        切一次工亡，9 项基础材料会全部消失。"""
        before = self._names(win)
        win.death_case_checkbox.setChecked(True)
        after = self._names(win)

        missing = [n for n in before if n not in after]
        assert not missing, f"切换后这些项凭空消失了：{missing}"

    def test_关闭再打开工亡仍能补回死亡证明(self, win):
        """移除过再恢复的项也要能加回来"""
        win.death_case_checkbox.setChecked(True)
        win.death_case_checkbox.setChecked(False)     # 此时死亡证明被移除
        win.death_case_checkbox.setChecked(True)      # 必须能补回来
        assert "死亡证明" in self._names(win), "被移除过的项加不回来了"

    def test_切回后多余的项消失(self, win):
        win.death_case_checkbox.setChecked(True)
        assert "死亡证明" in self._names(win)
        win.death_case_checkbox.setChecked(False)
        assert "死亡证明" not in self._names(win)

    def test_换条例时勾选状态保留(self, win):
        row = win.material_list._rows[1]              # 劳动合同
        row["_cb"].setChecked(True)
        row["_note"].setText("复印件")

        win.death_case_checkbox.setChecked(True)
        win.death_case_checkbox.setChecked(False)

        again = [r for r in win.material_list._rows
                 if r["_name_edit"].text() == "劳动合同"][0]
        assert again["_cb"].isChecked(), "换条例把勾好的清掉了"
        assert again["_note"].text() == "复印件", "备注被清掉了"

    def test_手工添加的行不被重建冲掉(self, win):
        win.material_list.add_row("我自己的材料", True, "手工加的")
        win.death_case_checkbox.setChecked(True)

        kept = [r for r in win.material_list._rows
                if r["_name_edit"].text() == "我自己的材料"]
        assert len(kept) == 1 and kept[0]["_cb"].isChecked()

    def test_必要项粉红_可能项黑色(self, win):
        import re
        colors = {}
        for r in win.material_list._rows:
            css = r["_name_edit"].styleSheet()
            colors[r["_name_edit"].text()] = re.search(r"color:\s*(#[0-9a-fA-F]+)", css).group(1)

        assert colors["身份证"] == win.material_list.REQUIRED_COLOR, "必要项应为粉红"
        assert colors["工资发放记录"] == win.material_list.POSSIBLE_COLOR, "可能项应为黑"

    def test_备注栏不跟着变粉(self, win):
        import re
        for r in win.material_list._rows:
            css = r["_note"].styleSheet()
            assert re.search(r"color:\s*(#[0-9a-fA-F]+)", css).group(1) == \
                win.material_list.POSSIBLE_COLOR


# ============================================================================
# 主界面布局
# ============================================================================

class Test主界面布局:

    def test_单位性质在案本号之下(self, win):
        num = win.label_10.geometry()             # 「案本号：」
        lab = win.unit_type_label.geometry()      # 「单位性质：」
        combo = win.unit_type_combo.geometry()

        assert lab.x() == num.x(), "「单位性质：」应与「案本号：」上下对齐"
        assert lab.y() > num.y(), "应在案本号下方"
        assert combo.x() == win.lineEdit_2.x(), "下拉框左缘应与案本号输入框对齐"
        assert combo.y() + combo.height() <= win.radioButton.y(), \
            "不应压住下方的角色单选"

    def test_证人编号行并入单位性质行(self, win):
        """证人那组原在左栏最底部，现并到单位性质右边；下拉也让位缩窄了"""
        combo = win.unit_type_combo.geometry()
        wlab = win.witness_label.geometry()
        wcombo = win.witness_combo.geometry()
        wbtn = win.add_witness_btn.geometry()

        assert wcombo.y() == combo.y() and wbtn.y() == combo.y(), \
            "应与单位性质下拉同排"
        assert combo.x() + combo.width() < wlab.x() < wcombo.x() < wbtn.x(), \
            "顺序应为 单位性质 → 证人编号 → 下拉 → 添加证人"
        assert combo.width() < 360, "单位性质下拉应为证人那组让位而缩窄"
        assert wbtn.x() + wbtn.width() <= 441, "不应越出表单右缘"

    def test_证人编号行常显(self, as_role):
        """原先只在证人角色下出现，现应始终可见"""
        for role in ("本人", "证人", "法人", "家属"):
            win = as_role(role)
            for name in ("witness_label", "witness_combo", "add_witness_btn"):
                assert not getattr(win, name).isHidden(), f"{role} 角色下 {name} 被隐藏了"

    def test_插入单位性质行后左栏不越界(self, win):
        from PyQt5.QtWidgets import QWidget
        bottom = max(c.geometry().y() + c.geometry().height()
                     for c in win.children()
                     if isinstance(c, QWidget) and c.geometry().x() < 478)
        assert bottom <= win.height(), f"左栏内容到 {bottom}，超出窗口高 {win.height()}"

    def test_时间字段两两并排(self, win):
        """原为四行，现为 申请｜受理 一行、受伤｜就诊 一行"""
        apply, accept = win.apply_time_edit.geometry(), win.accept_time_edit.geometry()
        injury, visit = win.injury_time_edit.geometry(), win.visit_time_edit.geometry()

        assert apply.y() == accept.y(), "申请时间与受理时间应在同一行"
        assert injury.y() == visit.y(), "受伤时间与就诊时间应在同一行"
        assert apply.y() < injury.y(), "受伤/就诊应在第二行"
        assert apply.x() + apply.width() <= accept.x(), "同行两个输入框不应重叠"
        assert accept.x() + accept.width() == 441, "应与表单右缘 441 对齐"

    def test_时间字段无外框(self, win):
        """原先外面套着「申请 / 受理 / 受伤 / 就诊时间」的 GroupBox，现已去掉"""
        from PyQt5.QtWidgets import QGroupBox
        assert not hasattr(win, "date_group"), "GroupBox 容器应已移除"
        titles = [c.title() for c in win.children() if isinstance(c, QGroupBox)]
        assert not any("时间" in t for t in titles), f"仍有时间分组框：{titles}"

    def test_时间字段与表单对齐(self, win):
        """去掉外框后应和 姓名/住址 等行一样：左列标签从 x=11 起"""
        labels = [c for c in win.children()
                  if c.__class__.__name__ == "QLabel" and "时间：" in c.text()
                  and c.geometry().x() < 100]
        assert len(labels) == 2, "应有两个左列标签"
        assert all(c.geometry().x() == 11 for c in labels)

    def test_时间字段不设占位提示(self, win):
        """输入框缩窄后占位文字会被裁掉，索性不要了；格式说明留在 tooltip"""
        for f in (win.apply_time_edit, win.accept_time_edit,
                  win.injury_time_edit, win.visit_time_edit):
            assert f.placeholderText() == ""
            assert f.toolTip(), "去掉占位提示后，格式说明必须留在 tooltip 里"

    def test_时间字段在拟用条例下方(self, win):
        """原在左栏最底部（按钮行之下），现紧跟拟用条例"""
        cb = win.comboBox.geometry()               # 拟用条例
        apply = win.apply_time_edit.geometry()
        injury = win.injury_time_edit.geometry()
        id_in = win.pushButton_4.geometry()        # 身份证导入

        assert apply.y() >= cb.y() + cb.height(), "申请时间应在拟用条例下方"
        assert apply.y() < injury.y(), "两行顺序：申请/受理 在 受伤/就诊 之上"
        assert id_in.y() > injury.y() + injury.height(), "身份证导入行应在时间字段之下"

    def test_时间字段左缘与拟用条例对齐(self, win):
        assert win.lbl_apply.geometry().x() == win.label_6.geometry().x(), \
            "左列标签应与「拟用条例：」左对齐"

    def test_证人行与时间字段不重叠(self, win):
        """证人行原先压在时间区上（重叠 24px），上移后应彻底分开"""
        wg = win.witness_combo.geometry()
        wtop, wbottom = wg.y(), wg.y() + wg.height()
        for f in (win.apply_time_edit, win.accept_time_edit,
                  win.injury_time_edit, win.visit_time_edit):
            g = f.geometry()
            assert g.y() + g.height() <= wtop or g.y() >= wbottom, \
                "证人行与时间字段重叠了"

    def test_底部三个按钮并成一组(self, win):
        """原「谈话通知书」前面有 105px 空档，看着像分成了两组"""
        a = win.pushButton_11.geometry()   # 案件审批表
        b = win.pushButton_12.geometry()   # 谈话通知书
        c = win.pushButton_7.geometry()    # 工伤告知书

        assert len({a.y(), b.y(), c.y()}) == 1, "三个按钮应在同一行"
        assert a.x() + a.width() <= b.x(), "谈话通知书应在案件审批表之后"
        assert b.x() + b.width() <= c.x(), "工伤告知书应在谈话通知书之后"

        gap1 = b.x() - (a.x() + a.width())
        gap2 = c.x() - (b.x() + b.width())
        assert gap1 == gap2, f"三个按钮间距应一致，实为 {gap1} / {gap2}"
        assert gap1 < 105, "原先的大空档应已消除"

    def _left_rows(self, win):
        """按 y 邻近把左栏控件归成「行」（与 _uniform_row_spacing 同一口径）"""
        from PyQt5.QtWidgets import QWidget
        items = sorted((c for c in win.children()
                        if isinstance(c, QWidget) and c is not win
                        and c.geometry().x() < 478 and c.geometry().y() >= 40),
                       key=lambda c: c.geometry().y())
        groups, cur = [], [items[0]]
        for c in items[1:]:
            if c.geometry().y() - cur[-1].geometry().y() <= 12:
                cur.append(c)
            else:
                groups.append(cur)
                cur = [c]
        groups.append(cur)
        return [(min(c.geometry().y() for c in g),
                 max(c.geometry().y() + c.geometry().height() for c in g))
                for g in groups]

    def test_左栏各行间距统一(self, win):
        """原间距从 3px 到 70px 不等，现应全部一致"""
        rows = self._left_rows(win)
        gaps = [rows[i + 1][0] - rows[i][1] for i in range(len(rows) - 1)]
        assert len(set(gaps)) == 1, f"行距不统一：{gaps}"

    def test_统一行距后不越界(self, win):
        """行距失配时内容会被越推越低——这条能兜住那种回归"""
        bottom = self._left_rows(win)[-1][1]
        assert bottom <= win.height(), f"左栏内容到 {bottom}，超出窗口高 {win.height()}"

    # ── 右栏两个框 ──

    def _left_bottom(self, win):
        from PyQt5.QtWidgets import QWidget
        rows = [c for c in win.children()
                if isinstance(c, QWidget) and c is not win and not c.isHidden()
                and c.geometry().x() < 478 and c.geometry().y() >= 40]
        return max(c.geometry().y() + c.geometry().height() for c in rows)

    def test_右栏两框底边与左栏齐平(self, win):
        box = win.material_group.geometry()
        assert box.y() + box.height() == self._left_bottom(win), \
            "材料分类框的底边应与左栏最后一行齐平"

    def test_右栏两框已加高(self, win):
        """原先两框下面空着 171px，现在应占满"""
        assert win.statement_group.height() > 350, "案件申请陈述框未加高"
        assert win.material_group.height() > 235, "材料分类框未加高"

    def test_右栏框内主体随之撑大(self, win):
        """框加高后里面的文本区/列表也要长高，否则只是多出空白"""
        from PyQt5.QtWidgets import QWidget, QPushButton
        for group, floor in ((win.statement_group, 300), (win.material_group, 185)):
            kids = [c for c in group.children()
                    if isinstance(c, QWidget) and c.parent() is group]
            body = [c for c in kids if not isinstance(c, QPushButton)][0]
            buttons = [c for c in kids if isinstance(c, QPushButton)]

            assert body.height() > floor, f"{group.title()} 内主体未撑大"
            assert body.y() + body.height() <= buttons[0].y(), \
                f"{group.title()} 内主体压住了按钮"

    def test_两框不重叠且留缝(self, win):
        g1 = win.statement_group.geometry()
        g2 = win.material_group.geometry()
        assert g1.y() + g1.height() <= g2.y(), "两框重叠了"
        assert g2.x() == g1.x() and g2.width() == g1.width(), "两框应左右对齐"

    def test_四个角色单选排成一行(self, win):
        """家属原独占第二行且缩进在最左，现应与本人/证人/法人齐平"""
        names = ("radioButton", "radioButton_2", "radioButton_3", "radioButton_4")
        rects = [getattr(win, n).geometry() for n in names]

        assert len({g.y() for g in rects}) == 1, "四个角色单选应在同一行"
        xs = [g.x() for g in rects]
        assert xs == sorted(xs), "顺序应是 本人 → 证人 → 法人 → 家属"
        assert rects[-1].x() + rects[-1].width() <= 478, "不要越出左侧栏"

    def test_谈话笔录移到身份证导入之后(self, win):
        id_in = win.pushButton_4.geometry()          # 身份证导入
        talk = win.pushButton.geometry()             # 谈话笔录
        ai = win.pushButton_ai_review.geometry()     # AI审查

        assert talk.y() == id_in.y(), "谈话笔录应与身份证导入同一行"
        assert talk.x() > id_in.x() + id_in.width(), "谈话笔录应在身份证导入右侧"
        assert ai.x() > talk.x() + talk.width(), "AI审查应顺延，不与谈话笔录重叠"

    def test_按钮不超出左栏(self, win):
        right = max(win.pushButton_ai_review.x() + win.pushButton_ai_review.width(),
                    win.pushButton.x() + win.pushButton.width())
        assert right <= 478, "左侧栏右边界是 478，按钮不能越界"


# ============================================================================
# 提示词里的条件块（写在 resource/prompts/*.txt，用 {% if %} 控制）
# ============================================================================

class Test模板条件块:
    """两个条件块已从代码搬进 txt 模板，条件也交给模板——这里盯住条件本身的真值表。

    块搬进模板后最容易出的错不是「抛异常」而是「条件写错 → 整块无声消失」，
    所以本人那条走真实入口 _build_prompt_for_role 验，别的角色直接渲模板比对。
    """

    TIME_JA = '【时间核对补充要求】'
    ADDR = '【补充问项】'
    BOTH_TIMES = {'injury_time': '202607201620', 'visit_time': '202607201900'}
    NO_TIMES = {'injury_time': '', 'visit_time': ''}
    PROMPT_KEY = {'本人': 'self_send_to_ai', '证人': 'witness_send_to_ai',
                  '法人': 'legal_send_to_ai', '家属': 'family_send_to_ai'}

    def _本人(self, win, article, times):
        case = {'case_id': 'T-条件块', 'proposed_article': article}
        case.update(times)
        return win._build_prompt_for_role('本人', case)

    def test_第六项且时间齐_两个块都在且时间块在前(self, win):
        out = self._本人(win, '第十四条第（六）项', self.BOTH_TIMES)
        assert self.TIME_JA in out and self.ADDR in out
        assert out.index(self.TIME_JA) < out.index(self.ADDR), "时间核对块应在现住址块之前"

    def test_第六项但时间不齐_只留现住址块(self, win):
        out = self._本人(win, '第十四条第（六）项', self.NO_TIMES)
        assert self.ADDR in out and '现住址' in out
        assert self.TIME_JA not in out

    def test_第一项且时间齐_只留时间块(self, win):
        out = self._本人(win, '第十四条第（一）项', self.BOTH_TIMES)
        assert self.TIME_JA in out
        assert self.ADDR not in out
        assert '现住址' not in out

    def test_第一项且时间不齐_两个块都不在(self, win):
        out = self._本人(win, '第十四条第（一）项', self.NO_TIMES)
        assert self.TIME_JA not in out and self.ADDR not in out
        # 模板本身的内容完好，且提示词就止于【必问要求】最后一句，末尾不挂东西
        for keep in ['【角色设定】', '【案件基本信息】', '【格式要求】', '【必问要求】']:
            assert keep in out, f"模板里的 {keep} 丢了"
        assert out.rstrip('\n').endswith('你清楚吗？'), "末尾多出了内容"

    def test_只填受伤时间不算时间齐(self, win):
        # 只填一个没法比间隔，条件要求两个都填
        out = self._本人(win, '第十四条第（六）项',
                         {'injury_time': '202607201620', 'visit_time': ''})
        assert self.TIME_JA not in out

    @pytest.mark.parametrize('role', ['本人', '证人', '法人', '家属'])
    def test_四个角色都有时间核对块(self, role):
        out = self._render_template(role, 受伤时间='2026年07月20日16时20分',
                                    就诊时间='2026年07月20日19时00分',
                                    拟用条例='第十四条第（六）项')
        assert self.TIME_JA in out

    @pytest.mark.parametrize('role', ['证人', '法人', '家属'])
    def test_只有本人有现住址块(self, role):
        out = self._render_template(role, 受伤时间='T', 就诊时间='V',
                                    拟用条例='第十四条第（六）项')
        assert self.ADDR not in out
        assert '现住址' not in out, f"{role} 模板里不该有现住址那一问"

    def _render_template(self, role, **over):
        """直接用模板渲染，绕开 MainWindow（条件里用到的键都必须给全，缺了会报错）"""
        from app_main import render_prompt_template
        from prompt_manager import load_prompt
        data = {'申请类型': '单位申请', '案件性质': '工伤案件', '拟用条例': '',
                '受伤时间': '', '就诊时间': ''}
        data.update(over)
        return render_prompt_template(load_prompt(self.PROMPT_KEY[role]), data, role)

    def test_四份模板的if都配对(self):
        """if/endif 数不匹配会渲染报错，先在文件层面钉住（注释里的示例不算）"""
        import io
        import re
        for role in self.PROMPT_KEY:
            path = 'resource/prompts/%s发送给AI提示词.txt' % role
            text = io.open(path, encoding='utf-8-sig').read()
            code = re.sub(r'\{#.*?#\}', '', text, flags=re.S)   # 去掉 {# 注释 #}
            assert code.count('{% if ') == code.count('{% endif %}'), f'{role} 模板 if/endif 不配对'
            assert code.count('{% if 受伤时间 and 就诊时间 %}') == 1, \
                f'{role} 模板的时间核对块条件缺失或重复'


# ============================================================================
# 提示词瘦身（本人那份：删无用字段、按案件裁掉无关分支、不把未交材料说成已提供）
# ============================================================================

class Test提示词精简:
    """这几条盯的不是字数，而是「AI 拿到的信息与本案是否自洽」。

    给它一份和本案无关的申请类型说明、或把没交的材料列成「已提供」，都会让它问错问题、
    误判证据齐备 —— 精简的目的首先是不误导。
    """

    def _本人(self, win, **case_over):
        case = {'case_id': 'T-精简', 'proposed_article': '第十四条第（一）项',
                'applicant_type': '单位申请',
                'materials': [{'name': '身份证复印件', 'provided': True, 'notes': ''},
                              {'name': '考勤记录', 'provided': False, 'notes': ''}]}
        case.update(case_over)
        return win._build_prompt_for_role('本人', case)

    def test_申请时间受理时间不进提示词(self, win):
        """这两个字段被 5 份 docx 文书模板用着，但格式要求不许笔录里出现询问时间"""
        out = self._本人(win, apply_time='2026年07月21日', accept_time='2026年07月22日')
        assert '申请时间' not in out
        assert '受理时间' not in out

    def test_申请情形适配只出相关的一条(self, win):
        danwei = self._本人(win, applicant_type='单位申请')
        geren = self._本人(win, applicant_type='个人申请')
        assert '单位已（拟）提出申请' in danwei
        assert '由职工本人自行提出申请' not in danwei, "单位申请案里混进了个人申请的说明"
        assert '由职工本人自行提出申请' in geren
        assert '单位已（拟）提出申请' not in geren, "个人申请案里混进了单位申请的说明"

    def test_已提供材料只列勾选的(self, win):
        out = self._本人(win)
        assert '身份证复印件' in out
        assert '考勤记录' not in out, "未勾选的材料被当成已提供了"

    def test_一件材料都没勾时该行消失(self, win):
        out = self._本人(win, materials=[{'name': '身份证复印件', 'provided': False, 'notes': ''}])
        # 只看「- 已提供证据材料：」这个字段行；【重要要求】里那句「与已提供证据材料不一致」
        # 是规则文字，不受影响
        assert '- 已提供证据材料：' not in out, "没勾任何材料时不该留一条空壳行"


class Test案件情形适配裁剪:
    """证人/法人/家属 的【案件情形适配】按「案件性质 × 申请类型」裁掉无关分支。

    证人/法人 是**两个维度**（可能同时是工亡 + 个人申请），两段不是二选一；
    家属笔录本身只做工亡，只有一个维度。最极端的「工伤 + 单位申请」两段都不适用，
    整段应当消失——这正是最常见的那种案子。
    """

    KEYS = {'本人': 'self_send_to_ai', '证人': 'witness_send_to_ai',
            '法人': 'legal_send_to_ai', '家属': 'family_send_to_ai'}
    HEAD = '【案件情形适配'
    # (角色, 工亡那段里的特征词, 个人申请那段里的特征词)
    ROLES = [('证人', '向证人核实死亡', '可请证人（尤其是工友）佐证'),
             ('法人', '核实职工死亡时间', '强调单位是否已为职工参加工伤保险')]

    def _render(self, role, nature, applicant):
        from app_main import render_prompt_template
        from prompt_manager import load_prompt
        return render_prompt_template(
            load_prompt(self.KEYS[role]),
            {'案件性质': nature, '申请类型': applicant, '拟用条例': '第十四条第（一）项',
             '受伤时间': '', '就诊时间': ''}, role)

    @pytest.mark.parametrize('role,death,geren', ROLES)
    def test_工伤单位申请_整段消失(self, role, death, geren):
        out = self._render(role, '工伤案件', '单位申请')
        assert self.HEAD not in out, "两段都不适用时整段该消失"
        assert death not in out and geren not in out

    @pytest.mark.parametrize('role,death,geren', ROLES)
    def test_工伤个人申请_只留个人那段(self, role, death, geren):
        out = self._render(role, '工伤案件', '个人申请')
        assert self.HEAD in out and geren in out
        assert death not in out, "工伤案里混进了工亡那段"

    @pytest.mark.parametrize('role,death,geren', ROLES)
    def test_工亡单位申请_只留工亡那段(self, role, death, geren):
        out = self._render(role, '工亡案件', '单位申请')
        assert death in out
        assert geren not in out, "单位申请案里混进了个人申请那段"

    @pytest.mark.parametrize('role,death,geren', ROLES)
    def test_工亡个人申请_两段都在(self, role, death, geren):
        out = self._render(role, '工亡案件', '个人申请')
        assert death in out and geren in out, "两个维度都命中时两段都该在"

    def test_家属只有申请类型一个维度(self):
        danwei = self._render('家属', '工亡案件', '单位申请')
        geren = self._render('家属', '工亡案件', '个人申请')
        assert '由用人单位提出' in danwei
        assert '由死者近亲属提出' not in danwei
        assert '由死者近亲属提出' in geren
        assert '由用人单位提出' not in geren

    def test_家属的工亡正文不被裁掉(self):
        """家属笔录只做工亡，那段正文与申请类型无关，任何组合都该在"""
        for applicant in ('单位申请', '个人申请'):
            out = self._render('家属', '工亡案件', applicant)
            assert '本次系对死者近亲属的询问' in out
            assert '48小时内抢救无效死亡' in out


# ============================================================================
# 提示词文件缺失/为空：直接报错，不再静默回退
# ============================================================================

class Test提示词文件缺失要报错:
    """txt 是唯一事实源：缺失/为空一律抛 PromptError，不回退到内置副本。

    以前会静默拿一份很旧的提示词继续生成（缺字段、无条件块），界面上完全看不出来。
    更要紧的是异常不能漏给 Qt：槽函数里漏出去会被 qFatal 中止进程，用户只看到闪退。
    """

    def _patch_dir(self, tmp_path, monkeypatch):
        import prompt_manager as pm
        monkeypatch.setattr(pm, "_prompts_dir", lambda: str(tmp_path))
        return pm

    def test_文件不存在抛PromptError(self, tmp_path, monkeypatch):
        from prompt_manager import PromptError
        pm = self._patch_dir(tmp_path, monkeypatch)
        with pytest.raises(PromptError, match="不存在"):
            pm.load_prompt('self_send_to_ai')

    def test_文件是空的抛PromptError(self, tmp_path, monkeypatch):
        from prompt_manager import PromptError
        pm = self._patch_dir(tmp_path, monkeypatch)
        (tmp_path / pm.PROMPT_FILES['self_send_to_ai']).write_text('  \n\n ', encoding='utf-8')
        with pytest.raises(PromptError, match="空的"):
            pm.load_prompt('self_send_to_ai')

    def test_未知key抛KeyError(self):
        import prompt_manager as pm
        with pytest.raises(KeyError):
            pm.load_prompt('这个key不存在')

    def test_读得出来就返回全文并剥掉BOM与空白(self, tmp_path, monkeypatch):
        pm = self._patch_dir(tmp_path, monkeypatch)
        name = pm.PROMPT_FILES['self_send_to_ai']
        (tmp_path / name).write_text('  姓名：{{本人姓名}}  ', encoding='utf-8-sig')
        assert pm.load_prompt('self_send_to_ai') == '姓名：{{本人姓名}}'

    def test_界面层兜住异常并提示用户(self, win, monkeypatch):
        """_build_prompt_or_warn 必须吞掉 PromptError：否则 qFatal 直接把程序打断"""
        import prompt_manager as pm
        from PyQt5.QtWidgets import QMessageBox
        from prompt_manager import PromptError

        def boom(key):
            raise PromptError('假的：提示词文件不存在')

        monkeypatch.setattr(pm, 'load_prompt', boom)
        seen = {}
        monkeypatch.setattr(QMessageBox, 'critical', lambda *a, **k: seen.update(args=a))
        monkeypatch.setattr(win, '_set_status', lambda *a, **k: seen.update(status=a))

        assert win._build_prompt_or_warn('本人', {'case_id': 'T-缺文件'}) is None
        assert seen.get('args'), "没给用户任何提示"
        assert seen.get('status'), "状态栏没提示"

    def test_提示词都在(self):
        """9 份提示词文件都得在——这是「不回退」之后最容易踩的坑"""
        import io
        import prompt_manager as pm
        for key, name in pm.PROMPT_FILES.items():
            path = io.open('resource/prompts/%s' % name, encoding='utf-8-sig').read().strip()
            assert path, f"{name}（{key}）缺失或为空"


# ============================================================================
# 谈话笔录按钮：四个角色同一条路
# ============================================================================

class Test谈话笔录生成路径:
    """本人过去比别的角色多跑一次 AI「条例判断 + 一致性比对」，那套已删。

    现在四个角色都是：数据核对 → 拼提示词 → 生成。这条用例钉住「本人不再走额外
    那一次调用」——不然很容易被重新加回来（多一次调用、多一段等待、多一份没依据的建议）。
    """

    @pytest.mark.parametrize('role', ['本人', '证人', '法人', '家属'])
    def test_按钮都走同一个生成入口(self, win, as_role, monkeypatch, role):
        win = as_role(role)
        calls = []
        monkeypatch.setattr(win, 'open_data_review', lambda: True)
        monkeypatch.setattr(win, '_generate_role_transcript', lambda r: calls.append(r))

        win.on_talk_button_clicked()

        assert calls == [role], f"{role} 没走统一的生成入口，实际：{calls}"

    def test_没有条例判断那套残留(self):
        """删掉的入口不该再被引用回来"""
        import app_main
        import prompt_manager as pm
        for gone in ('_analyze_case_with_ai', '_on_analysis_finished',
                     '_show_regulation_analysis', '_apply_regulation_change'):
            assert not hasattr(app_main.MainWindow, gone), f'MainWindow.{gone} 又回来了'
        assert not hasattr(app_main, 'RegulationAnalyzeWorker'), '条例判断工作线程又回来了'
        assert 'regulation_system' not in pm.PROMPT_FILES
        assert 'regulation_user' not in pm.PROMPT_FILES


# ============================================================================
# 本人笔录已存在：先问要不要覆盖
# ============================================================================

class Test本人笔录覆盖确认:
    """本人笔录已存在时先问「是否覆盖」，点「是」删掉旧的再按主界面当前数据重新生成。

    以前重生一路加序号，盘上堆出 (2)(3)；用户要的是覆盖。只动旧的本人笔录文件——
    案卷目录与案件数据都不碰（数据随后由数据核对用主界面当前值重建）。
    """

    CID = "莫言-案本202609071111"

    def _case_dir(self, win, tmp_path, monkeypatch):
        """造一个案的案卷目录，并把窗口的数据根指到临时目录"""
        monkeypatch.setattr(win, "BASE_PATH", str(tmp_path))
        d = tmp_path / "2026" / self.CID
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _fake_msgbox(self, monkeypatch, answer):
        """假 QMessageBox：addButton 造按钮、clickedButton 返回被点的那个（与真 Qt 同语义）"""
        import app_main as A

        class FakeMsg:
            # 代码里用到的几个类常量，真 Qt 有，假的也得有
            Question = 'question'
            YesRole = 'yes'
            NoRole = 'no'

            def __init__(self, *a, **k):
                self._clicked = None

            def setWindowTitle(self, *a): pass
            def setIcon(self, *a): pass
            def setText(self, *a): pass
            def setInformativeText(self, *a): pass
            def setDefaultButton(self, *a): pass
            def exec_(self): pass

            def addButton(self, text, role=None):
                btn = object()
                if text == answer:
                    self._clicked = btn
                return btn

            def clickedButton(self):
                return self._clicked

        monkeypatch.setattr(A, 'QMessageBox', FakeMsg)

    def test_没有旧笔录就直接放行(self, win, tmp_path, monkeypatch):
        """不 mock 弹窗：这条同时证明「没旧笔录就不会弹」——真弹出来会卡住"""
        monkeypatch.setattr(win, "BASE_PATH", str(tmp_path))
        assert win._main_transcript_files(self.CID) == []
        assert win._confirm_overwrite_main_transcript(self.CID) is True

    def test_找得出基础名与副本且不误伤其它角色(self, win, tmp_path, monkeypatch):
        import os
        d = self._case_dir(win, tmp_path, monkeypatch)
        for name in ("莫言本人谈话笔录.docx", "莫言本人谈话笔录(2).docx",
                     "莫言证人谈话笔录.docx", "case.json"):
            (d / name).write_bytes(b"x")

        found = [os.path.basename(p) for p in win._main_transcript_files(self.CID)]
        assert found == ["莫言本人谈话笔录(2).docx", "莫言本人谈话笔录.docx"], found

    @pytest.mark.parametrize('answer,expect_left', [('是', 0), ('否', 2)])
    def test_点是删掉旧的是否就一个都不动(self, win, tmp_path, monkeypatch,
                                          answer, expect_left):
        d = self._case_dir(win, tmp_path, monkeypatch)
        for name in ("莫言本人谈话笔录.docx", "莫言本人谈话笔录(2).docx"):
            (d / name).write_bytes(b"x")
        self._fake_msgbox(monkeypatch, answer)

        ok = win._confirm_overwrite_main_transcript(self.CID)

        assert ok is (answer == '是')
        assert len(win._main_transcript_files(self.CID)) == expect_left, \
            "点「%s」之后的剩余笔录份数不对" % answer

    def test_删不掉就挡住并提示(self, win, tmp_path, monkeypatch):
        """笔录正被 Word 打开时删不掉：要提示用户并返回假，不能继续生成又堆一个"""
        import app_main as A
        d = self._case_dir(win, tmp_path, monkeypatch)
        f = d / "莫言本人谈话笔录.docx"
        f.write_bytes(b"x")
        warned = {}
        monkeypatch.setattr(A.QMessageBox, "warning", lambda *a, **k: warned.update(args=a))

        def boom(p):
            raise OSError("文件被占用")

        monkeypatch.setattr(A.os, "remove", boom)
        assert win._delete_main_transcripts([str(f)]) is False
        assert warned, "删不掉必须提示用户"
        assert f.exists(), "删不掉的文件不该被当成删掉了"

    @pytest.mark.parametrize('confirm,expect', [(True, ['review', '本人']), (False, [])])
    def test_流程_确认通过才继续(self, win, as_role, monkeypatch, confirm, expect):
        win = as_role('本人')
        calls = []
        monkeypatch.setattr(win, 'open_data_review',
                            lambda: (calls.append('review'), True)[1])
        monkeypatch.setattr(win, '_generate_role_transcript', lambda r: calls.append(r))
        monkeypatch.setattr(win, '_confirm_overwrite_main_transcript',
                            lambda cid: confirm)
        monkeypatch.setattr(win, '_set_status', lambda *a, **k: None)

        win.on_talk_button_clicked()

        assert calls == expect, \
            "点「否」时连数据核对都不该跑（open_data_review 自己会写案件数据）"

    @pytest.mark.parametrize('role', ['证人', '法人', '家属'])
    def test_其它角色不弹这个确认(self, win, as_role, monkeypatch, role):
        win = as_role(role)
        asked = []
        monkeypatch.setattr(win, '_confirm_overwrite_main_transcript',
                            lambda cid: asked.append(cid) or True)
        monkeypatch.setattr(win, 'open_data_review', lambda: True)
        monkeypatch.setattr(win, '_generate_role_transcript', lambda r: None)

        win.on_talk_button_clicked()

        assert asked == [], f"{role} 不该弹「本人笔录覆盖」确认"


# ============================================================================
# 拟用条例以下拉框为准
# ============================================================================

class Test拟用条例以下拉框为准:
    """改了「拟用条例」下拉框，保存与生成必须用新值。

    下拉框的改动从不写回数据模型，而数据核对/材料清单原先都是「数据模型优先」——
    于是改了下拉框，保存的、生成的、材料清单用的全是旧条例，下拉框还会被刷回旧值。
    这是实测踩到的场景：先生成一份第（一）项笔录，改成第（六）项再生成，出来的
    还是第（一）项，而且下拉框自己跳回第（一）项。
    """

    J1 = '第十四条第（一）项'
    J6 = '第十四条第（六）项'

    def _select(self, win, full_name_part):
        """在下拉框里选中含某关键词的条例"""
        for i in range(win.comboBox.count()):
            if full_name_part in win.comboBox.itemText(i):
                win.comboBox.setCurrentIndex(i)
                return win.comboBox.currentText()
        raise AssertionError('下拉框里没找到 %s' % full_name_part)

    def test_改下拉框后保存用的是新条例(self, win, as_role):
        win = as_role('本人')
        win._apply_regulation(self.J1)              # 模拟案子里已存的是第（一）项
        self._select(win, '第一款第六项')            # 用户改成第（六）项

        data, *_ = win._collect_review_data()

        assert data['regulation'] == self.J6, \
            '数据核对会按旧条例保存（下拉框已经改成第（六）项）'

    def test_改下拉框后条例不会被刷回旧值(self, win, as_role):
        """「下拉框自动跳回第一条」那个现象：保存回写时不该把框刷回旧值"""
        win = as_role('本人')
        win._apply_regulation(self.J1)
        self._select(win, '第一款第六项')

        data, *_ = win._collect_review_data()
        win._apply_regulation(data['regulation'])   # 数据核对保存后回写主界面

        assert '第六项' in win.comboBox.currentText(), '下拉框被刷回旧条例了'
        assert data['regulation'] == self.J6

    def test_改下拉框后材料清单跟着变(self, win, as_role):
        """同一个根因的另一面：材料清单的自动项也按旧条例算"""
        win = as_role('本人')
        win._apply_regulation(self.J1)
        self._select(win, '第一款第六项')            # 会触发 _refresh_evidence_list

        names = [m.get('name', '') for m in win.material_list.get_materials()]
        assert '道路交通事故认定书' in names, \
            '第（六）项该带出「道路交通事故认定书」，实际清单：%s' % names

    def test_清空条例时下拉框也跟着清(self, win, as_role):
        """加载一个没填条例的案子时，框里不该留着上一个案子的条例"""
        win = as_role('本人')
        win._apply_regulation(self.J1)
        assert win.comboBox.currentText()

        win._apply_regulation('')

        assert win.comboBox.currentText() == '', '下拉框里残留了上一个案子的条例'
        assert win.get_data('拟用条例', '') == ''
