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
# 主界面布局
# ============================================================================

class Test主界面布局:

    def test_单位性质在案本号之下且独占一行(self, win):
        num = win.label_10.geometry()             # 「案本号：」
        lab = win.unit_type_label.geometry()      # 「单位性质：」
        combo = win.unit_type_combo.geometry()

        assert lab.x() == num.x(), "「单位性质：」应与「案本号：」上下对齐"
        assert lab.y() > num.y(), "应在案本号下方"
        assert combo.x() == win.lineEdit_2.x(), "下拉框左缘应与案本号输入框对齐"
        assert combo.width() >= 350, "下拉框应拉长占满一行"
        assert combo.y() + combo.height() <= win.radioButton.y(), \
            "不应压住下方的角色单选"

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

    def test_时间字段不压住证人行(self, win):
        """回归：去掉外框前，时间区与证人编号行重叠 24px"""
        bottom = max(f.geometry().y() + f.geometry().height()
                     for f in (win.apply_time_edit, win.accept_time_edit,
                               win.injury_time_edit, win.visit_time_edit))
        assert win.witness_combo.geometry().y() >= bottom, "证人行被时间字段压住"

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
