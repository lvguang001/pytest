# -*- coding: utf-8 -*-
"""主界面行为回归测试 —— 界面改布局之后，这些「点出来的行为」不能变。

对应的验证方法：改界面之前和之后各跑一遍，输出应当完全一致。
"""

import os


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
    """`company_pane` 四个角色共用，切角色必须按角色回填。

    无条件把它的值写成案件级用人单位，会把证人/家属自己的单位污染成整案的单位，
    全案文书都跟着错 —— 回填逻辑见 `MainWindow._restore_role_unit()`。
    """
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


# ============================================================================
# 保存当前录入（原「数据核对窗」那一步）
# ============================================================================

def test_the_data_review_dialog_is_gone():
    """「案件数据核对」窗 2026-09 删除。

    那是个可编辑的 JSON 窗，拦在「点谈话笔录」和生成之间——你点一下按钮，
    实际是「点 → 核对 → 生成」。现在录入即保存。

    留个守卫：再把模态窗加回去，这条链就又变成三步了。
    （不在这里调 on_talk_button_clicked——真弹窗会让测试挂住，而不是失败。）
    """
    import dialogs
    assert not hasattr(dialogs, "CaseDataReviewDialog")


def test_saving_the_form_persists_the_case(fresh_window):
    """★ 这一步必须落盘：后面的生成流程是**从磁盘读**案件数据的。

    见 _generate_role_transcript —— 它调 _load_cases_data().get(case_id)，
    不先存就会报「未找到该案本号的案件数据」。
    """
    w = fresh_window
    w.name_pane.setText("张三")
    w.idnumer_pane.setText("330102199001011234")
    w.lineEdit_5.setText("电焊工")
    w.lineEdit_2.clear()
    w.current_case_id = ""

    assert w._save_case_from_form() is True

    case_id = w.current_case_id
    assert case_id, "案本号没自动生成"
    assert w.lineEdit_2.text() == case_id, "案本号没回填到界面"

    saved = w._load_cases_data().get(case_id)
    assert saved is not None, "案件没落盘——下一步会读不到"
    assert saved["name"] == "张三"
    assert saved["position"] == "电焊工"
    assert saved["case_id"] == case_id


def test_saving_the_form_keeps_existing_extended_fields(fresh_window):
    """重复保存时不能把 service_flow / conclusion 这些扩展字段冲掉。"""
    w = fresh_window
    w.name_pane.setText("李四")
    w.lineEdit_2.clear()
    w.current_case_id = ""
    assert w._save_case_from_form() is True
    case_id = w.current_case_id

    # 模拟送达流程写入的扩展字段
    cases = w._load_cases_data()
    cases[case_id]["service_flow"] = {"phase": "ask"}
    cases[case_id]["conclusion"] = "予以认定"
    w._save_cases_data(cases)

    # 再存一次（用户又点了一次谈话笔录）
    assert w._save_case_from_form() is True
    again = w._load_cases_data()[case_id]
    assert again.get("service_flow") == {"phase": "ask"}, "service_flow 被冲掉了"
    assert again.get("conclusion") == "予以认定", "conclusion 被冲掉了"
    assert again["name"] == "李四"


def test_loading_a_case_does_not_duplicate_evidence(fresh_window):
    """★ 载入案卷后，面板上不能同时出现「身份证复印件」和「身份证」。

    面板上一半的行是「按条例算出来的该收项」，一半是「案卷里存的实收项」，
    两边靠名字联结（见 material_list.apply_evidence_list）。
    案卷里你录的是「身份证复印件」、条例算的若是「身份证」，同一份东西就会列成两条。
    """
    from case_classifier import evidence_key

    w = fresh_window
    w._apply_case_object({
        "case_id": "C-dup", "name": "莫言", "unit_type": "企业",
        "proposed_article": "第十四条第（一）项",
        # 故意用**旧叫法**：老案卷里存的就是「身份证」，而条例算出来的现在是
        # 「身份证复印件」——只有走别名才认得出是同一件。名字已对齐时靠精确比对
        # 也能过，所以这条用例必须用对不上的名字，否则测不到别名那层。
        "materials": [{"name": "身份证", "notes": ""},
                      {"name": "医院诊断证明书", "notes": "右足跖骨骨折"}]})

    mats = w.material_list.get_materials()
    names = [m["name"] for m in mats]
    keys = [evidence_key(n) for n in names]
    assert len(keys) == len(set(keys)), "同一份证据列了两条：%s" % names
    assert not ("身份证" in names and "身份证复印件" in names), \
        "条例又另补了一条「身份证复印件」——没认出案卷里的「身份证」"

    # 案卷里那两条原样留着（连备注），不因为条例那边换了叫法就被覆盖
    kept = {m["name"]: m for m in mats}
    assert kept["身份证"]["provided"] is True
    assert kept["医院诊断证明书"]["notes"] == "右足跖骨骨折"


def test_the_panel_lists_the_verification_points(fresh_window):
    """★ 材料面板里也要出现「必须核实的事实」。

    那个面板实际上就是「这个案子要备什么、要核什么」的清单——
    条例（二）要核「准备还是收尾」，条例（六）要核「上班还是下班」。
    """
    from case_classifier import regulation_short_to_full

    w = fresh_window
    w.comboBox.setCurrentIndex(
        w.comboBox.findText(regulation_short_to_full("第十四条第（六）项")))
    names = [m["name"] for m in w.material_list.get_materials()]
    assert "是上班途中还是下班途中？" in names
    assert "是否参加工伤保险？" in names
    assert "是否属开工前的准备（或收工后的收尾）工作？" not in names, \
        "换了条例，上一个条例的核实点没清掉"


# ============================================================================
# 证人笔录：按「本案卷里有没有本人笔录」分两条路
# ============================================================================

def _save_a_case(w):
    """在界面上录一个案子存盘，返回 case_id。

    存完把案卷目录清干净（只留 case.json）：沙箱是整个会话共用的，而案本号是按
    姓名算出来的——几条用例录的都是「张三」，落到同一个案卷目录，上一条用例写的
    本人笔录会留到下一条里，用例就不独立了。
    """
    w.radioButton.setChecked(True)
    w.clear_role_fields()
    w.name_pane.setText("张三")
    w.lineEdit_5.setText("电焊工")
    w.lineEdit_2.clear()
    w.current_case_id = ""
    assert w._save_case_from_form() is True
    folder = w._locate_case_dir(w.current_case_id)
    if folder:
        for name in os.listdir(folder):
            if name != 'case.json':
                os.remove(os.path.join(folder, name))
    return w.current_case_id


def _write_transcript(path, lines):
    from docx import Document
    doc = Document()
    for line in lines:
        doc.add_paragraph(line)
    doc.save(str(path))
    return str(path)


def _probe(w, monkeypatch, seen, ai_service=None, save_result=''):
    """把生成的**两条出口**都换成探针：别真调 AI、别真拿 Word 打开文件。

    seen 攒到的：`ai` = 走了 AI 那条路（连带 `prompt` 发给 AI 的提示词）、
    `content` = 本地拼出来的问答、`keep_blank_answers` = 渲染时那个开关。

    `save_result` 给状态栏那几条用例用：返回空串 = 渲染失败，后面的开户与
    状态栏就不跑了（状态栏停在「案件数据已保存」上）。
    """
    monkeypatch.setattr(w, 'ai_service', ai_service, raising=False)

    def fake_start(role, case_id, case_obj, prompt_text):
        seen['ai'] = True
        seen['role'] = role
        seen['prompt'] = prompt_text

    def fake_save(case_obj, content, role='本人', keep_blank_answers=False):
        seen['content'] = content
        seen['keep_blank_answers'] = keep_blank_answers
        seen['role'] = role
        return save_result

    monkeypatch.setattr(w, '_start_transcript_generation', fake_start)
    monkeypatch.setattr(w, '_save_transcript_to_template', fake_save)


def test_witness_transcript_without_a_main_transcript_skips_the_ai(fresh_window, monkeypatch):
    """★ 案卷里没有本人笔录 → 本地拼装，**一个网络请求都不发**。

    同时锁住另一件事：这条路必须在 `if not self.ai_service: return` **之前**。
    这里把 ai_service 置成 None（正是没配 API 密钥时的样子），笔录照样要出得来。
    """
    w = fresh_window
    _save_a_case(w)
    seen = {}
    _probe(w, monkeypatch, seen, ai_service=None)

    w._generate_role_transcript('证人')

    assert 'ai' not in seen, "没有本人笔录却去调 AI 了"
    assert '请介绍一下你的姓名、住址、工作单位以及从事的工作？' in seen['content']
    assert '请你详细陈述一下你知道的张三受伤情况或者你看到的受伤经过？' in seen['content']
    assert seen['keep_blank_answers'] is True, "留白的答行会被 render_transcript 滤掉"
    assert seen['role'] == '证人'


def test_witness_transcript_without_a_main_transcript_says_so(fresh_window, monkeypatch):
    """本地那条路要说明白是「固定套路」，别让人以为是依本人笔录生成的。"""
    w = fresh_window
    _save_a_case(w)
    seen = {}
    _probe(w, monkeypatch, seen, ai_service=None, save_result='假的笔录路径.docx')
    monkeypatch.setattr(w.file_service, 'open_document', lambda path: (True, ''))

    w._generate_role_transcript('证人')

    assert '固定套路' in w.label_14.text()
    assert '本人笔录' in w.label_14.text()


def test_witness_transcript_feeds_the_main_transcript_to_the_ai(fresh_window, monkeypatch):
    """★ 有本人笔录 → 走 AI，且那份笔录的**正文**要进提示词。

    只断言「调了 AI」是不够的：真正要防的是「读到了却没传下去」——
    那样 AI 还是只能编。所以这里在本人笔录里埋一个特征串，去提示词里找它。
    """
    w = fresh_window
    case_id = _save_a_case(w)
    folder = w._locate_case_dir(case_id)
    assert folder, "案子存了却没找到案卷目录"
    _write_transcript(os.path.join(folder, '张三本人谈话笔录.docx'),
                      ['问：你在公司做什么工作？',
                       '答：我是电焊工，上的是晚班，从23点到早上7点。'])

    seen = {}
    _probe(w, monkeypatch, seen, ai_service=object())

    w._generate_role_transcript('证人')

    assert seen.get('ai'), "有本人笔录却没走 AI"
    assert '我是电焊工，上的是晚班，从23点到早上7点。' in seen['prompt'], \
        "本人笔录的正文没进提示词——AI 还是只能靠编"


def test_witness_transcript_prefers_the_newest_main_transcript(fresh_window, monkeypatch):
    """★ 有 (2)(3) 副本时取最新的一份。

    副本是重新生成时留下的，新的才是准的——取错了会把上一版已改掉的陈述再喂给 AI。

    摆**三**份、且最新那份是 (3)，这样挑法写错就会被抓住：按文件名排序拿到的是 (2)
    （`(` 比 `.` 小，所以 `X(2)` 排在 `X.docx` 前面），按 mtime 取最小的拿到的是基础名，
    两种错法都拿不到 (3)。时间用 os.utime 钉死，不靠写入先后（同一秒写完 mtime 会一样）。
    """
    w = fresh_window
    case_id = _save_a_case(w)
    folder = w._locate_case_dir(case_id)
    newest = _write_transcript(os.path.join(folder, '张三本人谈话笔录(3).docx'),
                               ['答：这是最新版陈述，特征是最新版三个字。'])
    middle = _write_transcript(os.path.join(folder, '张三本人谈话笔录(2).docx'),
                               ['答：这是中间版陈述，特征是中间版三个字。'])
    oldest = _write_transcript(os.path.join(folder, '张三本人谈话笔录.docx'),
                               ['答：这是最初版陈述，特征是最初版三个字。'])
    os.utime(oldest, (1_600_000_000, 1_600_000_000))
    os.utime(middle, (1_600_000_100, 1_600_000_100))
    os.utime(newest, (1_600_000_200, 1_600_000_200))

    seen = {}
    _probe(w, monkeypatch, seen, ai_service=object())
    w._generate_role_transcript('证人')

    assert '最新版陈述' in seen['prompt']
    assert '中间版陈述' not in seen['prompt'], "取了 (2) 而不是 (3)"
    assert '最初版陈述' not in seen['prompt'], "取了最初那份"


def test_witness_transcript_falls_back_locally_when_no_ai_is_configured(fresh_window, monkeypatch):
    """★ 有本人笔录但没配 API 密钥 → 退化成本地，并在状态栏说清楚。

    静默退化最坏：用户会以为手上这份是依本人笔录生成的。
    """
    w = fresh_window
    case_id = _save_a_case(w)
    folder = w._locate_case_dir(case_id)
    _write_transcript(os.path.join(folder, '张三本人谈话笔录.docx'),
                      ['答：我是电焊工，上的是晚班。'])

    seen = {}
    _probe(w, monkeypatch, seen, ai_service=None, save_result='假的笔录路径.docx')
    monkeypatch.setattr(w.file_service, 'open_document', lambda path: (True, ''))

    w._generate_role_transcript('证人')

    assert 'ai' not in seen, "没配 AI 却去调 AI 了"
    assert '请介绍一下你的姓名、住址、工作单位以及从事的工作？' in seen['content']
    assert '未配置AI' in w.label_14.text(), "退化成本地却没告诉用户"


def test_an_empty_main_transcript_counts_as_no_transcript(fresh_window, monkeypatch):
    """★ 本人笔录读出来是空的（损坏/空文件）→ 按「没有」处理。

    拿空文本去问 AI，提示词里 `{% if 本人笔录 %}` 就不成立，AI 反而会自己编；
    而且「有笔录」这个判断会让它白等一次网络。
    """
    w = fresh_window
    case_id = _save_a_case(w)
    folder = w._locate_case_dir(case_id)
    _write_transcript(os.path.join(folder, '张三本人谈话笔录.docx'), ['', '   '])

    assert w._find_main_transcript(case_id) == "", "空笔录该被当成「没有」"

    seen = {}
    _probe(w, monkeypatch, seen, ai_service=object())
    w._generate_role_transcript('证人')
    assert 'ai' not in seen, "空笔录却走了 AI"


def test_witness_transcript_only_reads_the_main_transcript(fresh_window, monkeypatch):
    """别的角色的笔录不能当成本人笔录——文件名里带「本人」两字才算。"""
    w = fresh_window
    case_id = _save_a_case(w)
    folder = w._locate_case_dir(case_id)
    _write_transcript(os.path.join(folder, '李四证人谈话笔录.docx'),
                      ['答：这是证人的笔录，不该被当成本人陈述。'])

    assert w._find_main_transcript(case_id) == ""
    assert w._main_transcript_files(case_id) == []
