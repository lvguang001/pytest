# -*- coding: utf-8 -*-
"""谈话笔录三件事的单元测试（`transcripts.py`）。

- 身份措辞提示（纯）
- 模板占位符数据 / 提示词填充数据（纯，`get_data` 用一个小 lambda 顶替）
- 笔录 docx 渲染（用 python-docx 现造带锚点的模板真跑一遍）

不碰网络、不碰界面、不碰真实模板。
"""

import os

import pytest
from docx import Document

from transcripts import (
    ANCHOR_TEXT, build_unified_template_data, identity_wording_hint,
    prompt_fill_data, render_transcript,
)


def _flat(mapping):
    """顶替 MainWindow.get_data 的扁平键读取器"""
    def get_data(key, default=''):
        return mapping.get(key, default)
    return get_data


# ============================================================================
# 身份措辞提示
# ============================================================================

def test_no_hint_for_ordinary_enterprise():
    """企业案沿用「职工/公司」口径，不需要提示——返回空串时提示词里那行会整行消失。"""
    assert identity_wording_hint("企业", "职工", "本人") == ""
    assert identity_wording_hint("", "职工", "本人") == ""


@pytest.mark.parametrize("unit_type,appellation", [
    ("机关（公务员）", "机关工作人员"),
    ("事业单位", "事业单位工作人员"),
])
def test_non_enterprise_gets_the_appellation(unit_type, appellation):
    hint = identity_wording_hint(unit_type, "公务员", "本人")
    assert unit_type in hint
    assert "公务员" in hint
    assert appellation in hint
    assert "公司职工" in hint          # 明确要它避开的话术


def test_unknown_unit_type_still_produces_a_sentence():
    hint = identity_wording_hint("某新性质", "", "证人")
    assert "某新性质工作人员" in hint
    assert "证人" in hint
    assert "职工" in hint              # identity 空时回退到默认身份


# ============================================================================
# 统一模板字典
# ============================================================================

def _case(**over):
    base = {
        "case_id": "案本2026001", "name": "张三", "gender": "男", "age": "40",
        "id_card": "3301", "phone": "138", "address": "某路1号",
        "position": "电焊工", "labor_unit": "某公司", "employer": "用工公司",
        "site": "某工地", "case_nature": "工伤案件", "applicant_type": "单位申请",
        "proposed_article": "第十四条第（一）项",
        "proposed_article_elements": ["工作时间", "工作场所"],
        "injury_description": "砸伤", "injury_time": "202607201620",
        "visit_time": "202607201700",
    }
    base.update(over)
    return base


def test_unified_data_has_only_cn_keys():
    """★ 只出中文 key，不要再附一份英文的。

    曾经两个都有（注释写着「模板里写中文或英文占位符都能替换」）。但把
    resource/prompts/*.txt 和全部 docx 模板里的 {{占位符}} 抠出来数过：
    **62 个全是中文，英文那份一个用的都没有**。留着只会让人以为两套都得维护。
    """
    d = build_unified_template_data(_case(), username="吕广")
    assert d["本人姓名"] == "张三"
    assert d["案本号"] == "案本2026001"
    assert d["用户名"] == "吕广"
    for en in ("name", "case_id", "case_nature", "labor_unit", "employer", "site",
               "position", "materials", "recorder", "applicant_name", "id_card"):
        assert en not in d, f"英文 key {en} 又回来了——模板和提示词里没人用它"


def test_compact_times_are_formatted():
    d = build_unified_template_data(_case())
    assert d["受伤时间"] == "2026年07月20日16时20分"
    assert d["就诊时间"] == "2026年07月20日17时00分"


def test_elements_are_joined_into_one_line():
    d = build_unified_template_data(_case())
    assert d["法律要件"] == "工作时间 + 工作场所"
    assert build_unified_template_data(_case(proposed_article_elements=[]))["法律要件"] == ""


def test_provided_materials_only_list_the_ticked_ones():
    """★ 未勾选的也列进去，AI 会以为证据已经齐了。"""
    d = build_unified_template_data(_case(materials=[
        {"name": "身份证", "provided": True},
        {"name": "劳动合同", "provided": False},
        {"name": "病历", "provided": True, "notes": "x"},
    ]))
    assert "身份证" in d["已提供材料"]
    assert "病历" in d["已提供材料"]
    assert "劳动合同" not in d["已提供材料"], "没勾选的也列进去了"


def test_provided_materials_carry_their_notes():
    """★ 备注要带给 AI。

    备注里常写着这份材料的问题（用户的例子：「受伤时间不在合同期限内」），
    提示词那条「与已提供证据材料不一致的地方应追问核实」要靠它才落得实。
    """
    d = build_unified_template_data(_case(materials=[
        {"name": "劳动合同", "provided": True, "notes": "受伤时间不在合同期限内"},
        {"name": "身份证", "provided": True, "notes": ""},
    ]))
    assert "劳动合同（备注：受伤时间不在合同期限内）" in d["已提供材料"]
    assert "  · 身份证" in d["已提供材料"]
    assert "身份证（备注" not in d["已提供材料"], "没备注的别加个空括号"


def test_material_lines_are_on_separate_lines():
    """分行列，不用顿号连成一行——**材料名本身就可能带括号**，
    再套一层括号就分不清哪段是备注了。"""
    d = build_unified_template_data(_case(materials=[
        {"name": "近亲属关系证明（户口簿/结婚证等）", "provided": True, "notes": "已收"},
        {"name": "身份证", "provided": True, "notes": ""},
    ]))
    assert d["已提供材料"].count("\n") == 1, "两条材料该占两行"
    assert "近亲属关系证明（户口簿/结婚证等）（备注：已收）" in d["已提供材料"]


def test_no_provided_material_yields_empty_string():
    """全没勾 → 空串。提示词里那一段用 {% if %} 包着，整段不出现（不留光杆标题）。"""
    assert build_unified_template_data(_case(materials=[
        {"name": "身份证", "provided": False}]))["已提供材料"] == ""
    assert build_unified_template_data(_case())["已提供材料"] == ""


def test_recorder_falls_back_to_the_logged_in_user():
    assert build_unified_template_data(_case(), username="吕广")["记录人"] == "吕广"
    assert build_unified_template_data(_case(recorder="王五"), username="吕广")["记录人"] == "王五"


def test_current_period_is_used_when_given():
    d = build_unified_template_data(_case(), current_period="2026年09月15日20时00分")
    assert d["当前时期"] == "2026年09月15日20时00分"
    assert build_unified_template_data(_case())["当前时期"]     # 没给就现算一个


# ============================================================================
# 提示词填充数据
# ============================================================================

def test_prompt_data_for_the_worker_reuses_the_unified_dict():
    d = prompt_fill_data("本人", _case(), _flat({}), username="吕广")
    assert d["本人姓名"] == "张三"
    assert d["身份话术"] == ""          # 企业案不提示


def test_prompt_data_for_witness_prefers_the_record_over_the_flat_key():
    w = {"name": "李四", "id_card": "3302", "position": "工友", "identity": "职工"}
    d = prompt_fill_data("证人", _case(), _flat({"证人姓名": "旧值"}), witness=w)
    assert d["证人姓名"] == "李四"
    assert d["证人身份证号"] == "3302"


def test_prompt_data_for_witness_falls_back_to_the_flat_key():
    d = prompt_fill_data("证人", _case(), _flat({"证人姓名": "李四", "证人岗位": "工友"}),
                         witness={})
    assert d["证人姓名"] == "李四"
    assert d["证人岗位"] == "工友"
    assert d["证人身份"] == "职工"      # 记录和扁平键都没有时的默认身份


def test_prompt_data_for_legal_reads_the_flat_keys():
    d = prompt_fill_data("法人", _case(),
                         _flat({"法人姓名": "王五", "法人职务": "经理", "法人身份": "法定代表人"}))
    assert d["法人姓名"] == "王五"
    assert d["法人职务"] == "经理"
    assert d["法人身份"] == "法定代表人"


def test_prompt_data_for_family_blanks_the_position_without_a_unit():
    """家属没单位时岗位一并留空——与笔录表头的口径一致。"""
    flat = _flat({"家属姓名": "赵六", "家属身份": "配偶",
                  "家属单位名称": "某厂", "家属岗位": "普工"})
    d = prompt_fill_data("家属", _case(), flat)
    assert d["与死者关系"] == "配偶"
    assert d["家属岗位"] == "普工"

    d2 = prompt_fill_data("家属", _case(),
                          _flat({"家属姓名": "赵六", "家属身份": "配偶", "家属岗位": "普工"}))
    assert d2["家属单位名称"] == ""
    assert d2["家属岗位"] == "", "没单位时有岗位也该留空"


def test_prompt_data_carries_the_wording_hint_for_non_enterprise():
    d = prompt_fill_data("本人", _case(unit_type="事业单位", identity="事业编制工作人员"),
                         _flat({}))
    assert "事业单位" in d["身份话术"]


# ============================================================================
# 笔录 docx 渲染
# ============================================================================

def _template_with_anchor(path):
    """造一份最小模板：前置段落 + 锚点 + 后置样例问答"""
    doc = Document()
    doc.add_paragraph("xx谈话笔录")
    doc.add_paragraph("问：听清楚了吗？")
    doc.add_paragraph(ANCHOR_TEXT + "。")
    doc.add_paragraph("问：模板里的样例问题一？")
    doc.add_paragraph("问：模板里的样例问题二？")
    doc.save(str(path))
    return str(path)


def _texts(path):
    return [p.text for p in Document(path).paragraphs]


def test_render_replaces_the_sample_qa_after_the_anchor(tmp_path):
    """★ 锚点之后的模板样例问答要删掉，否则会和 AI 问答重复。"""
    tpl = _template_with_anchor(tmp_path / "t.docx")
    out = render_transcript(tpl, {}, "问：AI 问题一？\n答：AI 回答一？", str(tmp_path), "张三本人谈话笔录")
    try:
        texts = _texts(out)
        assert "xx谈话笔录" in "".join(texts)                    # 头部保留
        assert any(ANCHOR_TEXT in t for t in texts)              # 锚点保留
        assert not any("模板里的样例问题" in t for t in texts)     # 样例被删
        assert "问：AI 问题一？" in texts and "答：AI 回答一？" in texts
    finally:
        os.remove(out)


def test_render_keeps_the_answer_lines_underlined(tmp_path):
    tpl = _template_with_anchor(tmp_path / "t.docx")
    out = render_transcript(tpl, {}, "问：x", str(tmp_path), "笔录")
    try:
        doc = Document(out)
        lines = [p for p in doc.paragraphs if p.text == "问：x"]
        assert lines and lines[0].runs[0].underline is True
        assert lines[0].runs[0].font.size.pt == 15
    finally:
        os.remove(out)


def test_render_skips_blank_lines_in_the_ai_answer(tmp_path):
    tpl = _template_with_anchor(tmp_path / "t.docx")
    out = render_transcript(tpl, {}, "问：a\n\n\n答：b\n   ", str(tmp_path), "笔录")
    try:
        texts = _texts(out)
        assert "问：a" in texts and "答：b" in texts
        assert "" not in texts[texts.index("答：b") + 1:], "空行不该被插进去"
    finally:
        os.remove(out)


def test_render_returns_empty_when_anchor_is_missing(tmp_path):
    doc = Document()
    doc.add_paragraph("没有锚点的模板")
    tpl = str(tmp_path / "noanchor.docx")
    doc.save(tpl)
    assert render_transcript(tpl, {}, "问：x", str(tmp_path), "笔录") == ""


def test_render_returns_empty_when_template_is_missing(tmp_path):
    assert render_transcript(str(tmp_path / "没有.docx"), {}, "问：x", str(tmp_path), "笔录") == ""


def test_render_does_not_clobber_an_existing_transcript(tmp_path):
    tpl = _template_with_anchor(tmp_path / "t.docx")
    first = render_transcript(tpl, {}, "问：第一次", str(tmp_path), "张三本人谈话笔录")
    second = render_transcript(tpl, {}, "问：第二次", str(tmp_path), "张三本人谈话笔录")
    try:
        assert first != second
        assert second.endswith("张三本人谈话笔录(2).docx")
        assert "问：第一次" in _texts(first), "第一份不该被覆盖"
    finally:
        os.remove(first)
        os.remove(second)


# ============================================================================
# 真实的提示词 txt（它是个 Jinja 模板，改坏了要立刻发现）
# ============================================================================

def test_the_real_prompt_file_renders_cleanly():
    """把仓库里真正的「本人发送给AI提示词.txt」渲染几遍。

    不是多余的：手工核对时踩过两次 ——
    ① 条件块被拿掉后出现「受伤时间为：，」这种病句；
    ② 只有注释、没有正文的分支输出一个光杆标题。
    这两条都能在这里被抓住。
    """
    from prompt_manager import load_prompt, render_prompt_template

    case = _case(materials=[{"name": "身份证", "provided": True, "notes": "已过期"},
                            {"name": "劳动合同", "provided": True, "notes": ""}])
    for reg in ("第十四条第（一）项", "第十四条第（六）项", "第十五条第（二）项"):
        text = render_prompt_template(
            load_prompt('self_send_to_ai'),
            prompt_fill_data("本人", {**case, "proposed_article": reg}, _flat({})),
            "本人")
        assert "{{" not in text and "}}" not in text, f"{reg}: 有没被替换的占位符"
        assert "{#" not in text, f"{reg}: 注释没被吃掉"
        assert "【案件基本信息】" in text and "【格式要求】" in text
        # 空值/空分支残留的典型症状
        assert "时间为：，" not in text, f"{reg}: 时间没填时出现病句"
        assert "备注：）" not in text, f"{reg}: 空备注带出了空括号"
        assert "：【\n" not in text, f"{reg}: 有条目只有标题没有内容"


def test_the_real_prompt_lists_materials_with_notes():
    """端到端确认：案卷里的备注真的进了提示词。"""
    from prompt_manager import load_prompt, render_prompt_template

    case = _case(materials=[{"name": "劳动合同", "provided": True,
                             "notes": "受伤时间不在合同期限内"}])
    text = render_prompt_template(
        load_prompt('self_send_to_ai'),
        prompt_fill_data("本人", case, _flat({})), "本人")
    assert "劳动合同（备注：受伤时间不在合同期限内）" in text


def test_the_real_prompt_omits_the_material_section_when_nothing_is_ticked():
    """全没勾选时，「已提供证据材料」那一整段（含标题）都不出现。"""
    from prompt_manager import load_prompt, render_prompt_template

    case = _case(materials=[{"name": "身份证", "provided": False, "notes": "未收"}])
    text = render_prompt_template(
        load_prompt('self_send_to_ai'),
        prompt_fill_data("本人", case, _flat({})), "本人")
    assert "已提供证据材料" not in text


# ============================================================================
# 收尾那一行：留给被谈话人亲笔填写
# ============================================================================

#: 一段贴着真实收尾的 AI 输出：最后那个「问：以上笔录…」的答，会由末段那条
#: 空白下划线取代（留给被谈话人亲笔写）
_CLOSING_QA = ("问：你与公司是否签订劳动合同？\n答：签了。\n\n"
               "问：以上笔录你是否看见，是否和你所说的一致？\n答：我看过了，和我说的一样。")


def _template_with_tail(path):
    """带收尾段的模板：末段是「答：」+ 一串带下划线的空白（四份真实模板都这样）"""
    doc = Document()
    doc.add_paragraph("xx谈话笔录")
    doc.add_paragraph("问：听清楚了吗？")
    doc.add_paragraph(ANCHOR_TEXT + "。")
    doc.add_paragraph("问：模板里的样例问题一？")
    p = doc.add_paragraph()
    r = p.add_run("答：" + " " * 30)
    r.underline = True
    doc.save(str(path))
    return str(path)


def test_the_handwritten_tail_line_is_kept(tmp_path):
    """★ 模板末段（「答：」+ 带下划线的空白）要留着。

    那是被谈话人亲笔写「以上笔录我看过，与我说的一样」并签字的地方，
    不能由 AI 代写。原先渲染时把锚点之后的段落**全删**，包括这一段。
    """
    tpl = _template_with_tail(tmp_path / "t.docx")
    out = render_transcript(tpl, {}, _CLOSING_QA, str(tmp_path), "笔录")
    try:
        texts = [p.text for p in Document(out).paragraphs]
        assert texts[-1].startswith("答：") and not texts[-1][2:].strip(), \
            f"末段不是那条收尾空白行: {texts[-1]!r}"
        assert "模板里的样例问题一" not in "\n".join(texts), "样例问答该删的没删"
        assert "问：你与公司是否签订劳动合同？" in texts
        assert "答：签了。" in texts
    finally:
        os.remove(out)


def test_the_ai_answer_to_the_last_question_is_dropped(tmp_path):
    """★ AI 对最后一个问题写的答要去掉，否则和末段那个「答：」凑成两个。"""
    tpl = _template_with_tail(tmp_path / "t.docx")
    out = render_transcript(tpl, {}, _CLOSING_QA, str(tmp_path), "笔录")
    try:
        texts = [p.text for p in Document(out).paragraphs]
        assert "我看过了" not in "\n".join(texts), "AI 的收尾答还在"
        assert "问：以上笔录你是否看见，是否和你所说的一致？" in texts, "问句不该跟着丢"
        assert "答：签了。" in texts, "前面正常问答不该受影响"
        # 「答：」三条 = 锚点那句 + 正文那答 + 末段那条收尾空白
        assert sum(1 for t in texts if t.startswith("答：")) == 3, "「答：」的条数不对"
        assert texts[-1].startswith("答：") and not texts[-1][2:].strip()
    finally:
        os.remove(out)


def test_without_a_tail_the_ai_lines_are_appended_as_before(tmp_path):
    """模板没有收尾段时退回原行为——别把 AI 的最后一行也丢了。"""
    tpl = _template_with_anchor(tmp_path / "t.docx")   # 末段是「问：…」，不是收尾空白行
    out = render_transcript(tpl, {}, "问：x？\n答：我看过了。", str(tmp_path), "笔录")
    try:
        assert "答：我看过了。" in [p.text for p in Document(out).paragraphs]
    finally:
        os.remove(out)


def test_ai_output_ending_with_a_question_keeps_everything(tmp_path):
    """★ 按提示词第 6 条，AI 的收尾是「问：…」而不是答。

    这时**什么都不该丢**，末段那条空白答行直接接在问句后面。
    （另一条路径——AI 没听话、还是写了答——由
      test_the_ai_answer_to_the_last_question_is_dropped 兜着。）
    """
    tpl = _template_with_tail(tmp_path / "t.docx")
    content = ("问：你与公司是否签订劳动合同？\n答：签了。\n\n"
               "问：以上笔录你是否看过，是否和你所说的一致？")
    out = render_transcript(tpl, {}, content, str(tmp_path), "笔录")
    try:
        texts = [p.text for p in Document(out).paragraphs]
        assert texts[-1].startswith("答：") and not texts[-1][2:].strip(), "末段没接上"
        assert "问：以上笔录你是否看过，是否和你所说的一致？" in texts, "收尾问句丢了"
        assert "答：签了。" in texts, "正文丢了"
        # 「答：」三条 = 锚点那句 + 正文那答 + 末段那条空白
        assert sum(1 for t in texts if t.startswith("答：")) == 3
    finally:
        os.remove(out)
