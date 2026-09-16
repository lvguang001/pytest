# -*- coding: utf-8 -*-
"""证据清单与材料名的单元测试（`case_classifier.py`）。

纯函数、不碰 Qt。这块管两件事：
- 按条例（× 单位性质 × 工亡 × 个人申请）算「该收哪些材料」
- **案卷里手输的材料名怎么和条例算出来的对上** —— 后者出错的症状是
  材料面板上同一份证据列成两条（一条"该收"、一条"已收到"）
"""

import pytest

from case_classifier import (
    REGULATION_OPTIONS, compose_checks, compose_evidence, evidence_key,
)


# ============================================================================
# evidence_key：同一份证据的其它叫法
# ============================================================================

@pytest.mark.parametrize("a,b", [
    ("身份证", "身份证复印件"),
    ("医院诊断证明", "医院诊断证明书"),
    ("诊断证明", "医院诊断证明书"),
    ("劳动合同书", "劳动合同"),
    ("身份证 复印件", "身份证复印件"),                 # 中间的空格不该影响判重
    ("近亲属关系证明", "近亲属关系证明（户口簿/结婚证等）"),
])
def test_alias_forms_are_the_same_evidence(a, b):
    assert evidence_key(a) == evidence_key(b)


@pytest.mark.parametrize("a,b,why", [
    ("死亡证明", "死亡原因证明", "死亡原因证明是另一份材料"),
    ("申请人身份证", "身份证复印件", "申请人是近亲属，不是受伤职工"),
    ("旧伤复发的医疗机构诊断证明", "医院诊断证明书", "不同文书"),
    ("职业病诊断证明", "医院诊断证明书", "不同文书"),
    ("考勤记录", "工资发放记录", "不同材料"),
])
def test_different_evidence_is_not_merged(a, b, why):
    """★ 别名是**精确比对**，不做子串匹配。

    否则「诊断证明」这一条别名会把一堆带「诊断证明」四个字的材料全吃掉
    （职业病诊断证明、旧伤复发的医疗机构诊断证明…），那是不同的文书。
    """
    assert evidence_key(a) != evidence_key(b), why


def test_evidence_key_tolerates_whitespace_and_empty():
    assert evidence_key("  身份证  ") == evidence_key("身份证")
    assert evidence_key("") == ""
    assert evidence_key(None) == ""


# ============================================================================
# 清单本身
# ============================================================================

def test_base_names_use_the_paperwork_wording():
    """★ 通用材料按**实际收件口径**写，不写法条的通用名。

    实际收的都是复印件（原件与复印件的比对在收件时就做完了），归档的是
    「身份证复印件」。写成「身份证」的话，案卷里录的「身份证复印件」和它
    算两样，面板上会列成两条。
    """
    names = [n for n, _ in compose_evidence("第十四条第（一）项", False, False, "企业")]
    assert "身份证复印件" in names
    assert "医院诊断证明书" in names
    assert "身份证" not in names, "又写回法条通用名了"
    assert "医院诊断证明" not in names, "又写回法条通用名了"


def test_every_regulation_lists_the_base_materials_first():
    for reg in REGULATION_OPTIONS:
        items = compose_evidence(reg, False, False, "企业")
        assert items, "%s 清单是空的" % reg
        assert items[0][1] is True, "%s：必要项该排在前头" % reg
        assert "身份证复印件" in [n for n, _ in items], "%s 缺通用材料" % reg


def test_no_combination_produces_the_same_evidence_twice():
    """★ 清单里不能出现「同一份证据、两种叫法」——按别名也算重复。

    这层是合成逻辑（条例 × 工亡 × 个人申请 × 单位性质四层叠加）自身的自洽检查，
    与案卷那边的判重是两回事。
    """
    for reg in REGULATION_OPTIONS:
        for death in (False, True):
            for personal in (False, True):
                for ut in ("企业", "事业单位", "机关（公务员）"):
                    keys = [evidence_key(n)
                            for n, _ in compose_evidence(reg, death, personal, ut)]
                    dupes = {k for k in keys if keys.count(k) > 1}
                    assert not dupes, (
                        "%s（工亡=%s 个人=%s %s）里同一份证据出现了两次: %s"
                        % (reg, death, personal, ut, dupes))


def test_non_enterprise_swaps_the_labour_contract_for_人事证明():
    """机关/事业单位不是劳动合同关系，劳动合同降为「可能」，换成人事关系证明。"""
    req = [n for n, r in compose_evidence("第十四条第（一）项", False, False, "事业单位") if r]
    poss = [n for n, r in compose_evidence("第十四条第（一）项", False, False, "事业单位") if not r]
    assert "劳动合同" not in req and "劳动合同" in poss
    assert "事业单位在编证明" in req and "聘用合同" in req


def test_death_case_and_personal_apply_add_their_own_items():
    death = [n for n, _ in compose_evidence("第十四条第（一）项", True, False, "企业")]
    assert "死亡证明" in death
    kin = [n for n, _ in compose_evidence("第十四条第（一）项", True, True, "企业")]
    assert "近亲属关系证明（户口簿/结婚证等）" in kin
    assert "申请人身份证" in kin


# ============================================================================
# 核实要点（不是材料，是必须问清的事实）
# ============================================================================

def test_checks_carry_the_two_the_user_asked_for():
    """用户点名要的两条：条例（二）问「准备还是收尾」、（六）问「上班还是下班」。"""
    assert "是否属开工前的准备（或收工后的收尾）工作？" in compose_checks("第十四条第（二）项")
    assert "是上班途中还是下班途中？" in compose_checks("第十四条第（六）项")


def test_common_checks_come_last_for_every_regulation():
    """所有案子通用的两条（参保 / 饮酒）排在最后。"""
    for reg in REGULATION_OPTIONS:
        checks = compose_checks(reg)
        assert checks[-2:] == ["是否参加工伤保险？", "事发前是否饮酒？"], \
            "%s 的通用核实要点没排在最后：%s" % (reg, checks)


def test_checks_are_phrased_as_questions():
    """写成问句形态——面板上他列在材料中间，一眼要分得出不是材料。"""
    for reg in REGULATION_OPTIONS:
        for c in compose_checks(reg):
            assert c.endswith("？"), "%s 的核实要点不是问句：%s" % (reg, c)


def test_checks_never_collide_with_evidence_names():
    """核实要点不能和材料同名——同名的话面板判重会把其中一条吃掉。"""
    for reg in REGULATION_OPTIONS:
        ev = {evidence_key(n) for n, _ in compose_evidence(reg, False, False, "企业")}
        for c in compose_checks(reg):
            assert evidence_key(c) not in ev, "%s：核实要点和材料重名了：%s" % (reg, c)
