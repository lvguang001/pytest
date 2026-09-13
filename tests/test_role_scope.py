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

    def test_提示词对照表已同步(self):
        """AI 靠这张对照表判定条例，留下已删的项会让它返回不存在的条例。

        两处副本都要查：外置的 txt 文件，以及 prompt_manager 里的兜底默认值。
        """
        import io
        import prompt_manager

        path = "resource/prompts/条例分析系统.txt"
        assert "第十四条第（七）项" not in io.open(path, encoding="utf-8").read(), \
            f"{path} 里还留着已删的条例"
        assert "第十四条第（七）项" not in prompt_manager.DEFAULT_PROMPTS["regulation_system"], \
            "prompt_manager 的兜底副本没同步"

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
