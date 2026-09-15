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


def test_unified_data_has_both_cn_and_en_keys():
    d = build_unified_template_data(_case(), username="吕广")
    assert d["本人姓名"] == "张三" and d["name"] == "张三"
    assert d["案本号"] == d["case_id"] == "案本2026001"
    assert d["用户名"] == "吕广"


def test_compact_times_are_formatted():
    d = build_unified_template_data(_case())
    assert d["受伤时间"] == "2026年07月20日16时20分"
    assert d["就诊时间"] == "2026年07月20日17时00分"


def test_elements_are_joined_into_one_line():
    d = build_unified_template_data(_case())
    assert d["法律要件"] == "工作时间 + 工作场所"
    assert d["proposed_article_elements"] == "工作时间 + 工作场所"
    assert build_unified_template_data(_case(proposed_article_elements=[]))["法律要件"] == ""


def test_provided_materials_only_list_the_ticked_ones():
    """★ 未勾选的也列进去，AI 会以为证据已经齐了。"""
    d = build_unified_template_data(_case(materials=[
        {"name": "身份证", "provided": True},
        {"name": "劳动合同", "provided": False},
        {"name": "病历", "provided": True, "notes": "x"},
    ]))
    assert d["已提供材料"] == "身份证、病历"
    assert d["materials"] == "身份证、病历"


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
