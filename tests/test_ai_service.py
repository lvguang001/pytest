# -*- coding: utf-8 -*-
"""`ai_service.py` 里纯函数部分的单元测试。

目前只有 `parse_ai_result` —— 它原先挂在 MainWindow 上（2026-09 搬到 ai_service），
解析的是提示词里约定的回复格式，所以格式一变就该在这里补用例。
不碰网络、不碰 Qt。
"""

import pytest

from ai_service import parse_ai_result


def test_parses_the_standard_two_section_reply():
    text = (
        "【审查结果】\n"
        "笔录基本完整，但缺少劳动关系证明。\n"
        "【缺失问题列表】\n"
        "□ 1. 问：您与公司是否签订了书面劳动合同？\n"
        "□ 2. 问：事故当天是否有人在场？\n"
    )
    r = parse_ai_result(text)
    assert r["审查结果"] == "笔录基本完整，但缺少劳动关系证明。"
    assert r["缺失问题"] == [
        "问：您与公司是否签订了书面劳动合同？",
        "问：事故当天是否有人在场？",
    ]
    assert r["原始文本"] == text


def test_question_is_unwrapped_from_the_checkbox_and_number():
    """问题行的形状是「□ 1. 问：…」，要去掉方框和序号——但保留「问：」本身。"""
    r = parse_ai_result("【审查结果】\nx\n【缺失问题列表】\n□ 12. 问：是否有人证？")
    assert r["缺失问题"] == ["问：是否有人证？"]


def test_question_without_a_number_still_loses_its_checkbox():
    """★ 上面那条区分不出「去掉方框」单独有没有生效——有序号时，
    切序号那一步顺手把方框也切掉了。这条没有序号，只能靠方框那步。"""
    r = parse_ai_result("【审查结果】\nx\n【缺失问题列表】\n□ 问：是否有人证？")
    assert r["缺失问题"] == ["问：是否有人证？"]


def test_lines_without_a_checkbox_are_not_questions():
    """两个条件缺一不可：既要有「□」也要有「问：」。"""
    r = parse_ai_result(
        "【审查结果】\nx\n【缺失问题列表】\n"
        "以下为补充问题：\n"
        "问：有那个前缀但没有方框\n"
        "□ 有方框但没有那个前缀\n"
    )
    assert r["缺失问题"] == []


def test_reply_without_markers_is_all_review_text():
    r = parse_ai_result("就是一段普通文本，没有任何标记")
    assert r["审查结果"] == "就是一段普通文本，没有任何标记"
    assert r["缺失问题"] == []


def test_reply_with_only_the_review_marker_is_not_split():
    """只有【审查结果】没有【缺失问题列表】时走不通分割，整段原样留下。

    这是现状而不是理想行为——把标记也留在正文里，至少用户看得出异常。
    """
    r = parse_ai_result("【审查结果】只有这个标记")
    assert r["审查结果"] == "【审查结果】只有这个标记"
    assert r["缺失问题"] == []


@pytest.mark.parametrize("bad", [None, "", 123, [], {}])
def test_non_string_input_does_not_crash(bad):
    """★ 回归：None 曾在末尾那句 print 的 `len(result['审查结果'])` 上抛 TypeError。

    `show_ai_review_result` 从 `{"结果": …}` 取值，那个值可能是 None（例如
    接口返回了空内容），这条路径是够得着的。
    """
    r = parse_ai_result(bad)
    assert isinstance(r["审查结果"], str)
    assert isinstance(r["缺失问题"], list)
