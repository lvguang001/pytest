# -*- coding: utf-8 -*-
"""`services.py` 里「人记录 schema 与扁平键」那部分的单元测试。

纯函数、不碰 Qt。2026-09 从 app_main.py 搬过来时一并补的。
"""

import pytest

from services import (
    PERSON_BASE_FIELDS, PERSON_CN_SUFFIX, person_flat_key, witness_seq_label,
)


# ============================================================================
# 扁平键命名
# ============================================================================

def test_flat_key_uses_role_prefix_and_cn_suffix():
    assert person_flat_key("本人", "name") == "本人姓名"
    assert person_flat_key("证人", "id_card") == "证人身份证号"
    assert person_flat_key("家属", "unit") == "家属单位名称"


def test_position_suffix_follows_the_role():
    """岗位这一栏的语义随角色变：法人存的是「职务」。"""
    assert person_flat_key("本人", "position") == "本人岗位"
    assert person_flat_key("证人", "position") == "证人岗位"
    assert person_flat_key("法人", "position") == "法人职务"
    assert person_flat_key("家属", "position") == "家属岗位"


def test_every_base_field_has_a_cn_suffix_or_is_position():
    """PERSON_BASE_FIELDS 里的键要么有中文后缀、要么走 position 分派。

    漏一个的话 person_flat_key 会退回英文键名（'本人phone'），
    扁平键就悄悄串味了。
    """
    for field in PERSON_BASE_FIELDS:
        if field == "position":
            continue
        assert field in PERSON_CN_SUFFIX, f"{field} 没配中文后缀"


def test_unknown_field_falls_back_to_its_own_name():
    """未知字段不该炸，退回字段名本身（新增字段忘了配后缀时至少看得见）。"""
    assert person_flat_key("本人", "奇怪字段") == "本人奇怪字段"


def test_flat_keys_are_unique_across_fields():
    """两个字段拼出同一个键就说明后缀配重了——那会让数据互相覆盖。"""
    keys = [person_flat_key("本人", f) for f in PERSON_BASE_FIELDS]
    assert len(keys) == len(set(keys))


# ============================================================================
# 证人编号
# ============================================================================

@pytest.mark.parametrize("n,expected", [
    (0, "证人0"), (1, "证人一"), (2, "证人二"), (9, "证人九"),
    (10, "证人十"), (11, "证人十一"), (19, "证人十九"),
    (20, "证人二十"), (21, "证人二十一"), (30, "证人三十"), (99, "证人九十九"),
])
def test_witness_label_below_hundred(n, expected):
    assert witness_seq_label(n) == expected


@pytest.mark.parametrize("n,expected", [
    (100, "证人一百"), (101, "证人一百零一"), (110, "证人一百一十"),
    (111, "证人一百一十一"), (121, "证人一百二十一"), (999, "证人九百九十九"),
])
def test_witness_label_hundred_and_above(n, expected):
    """★ 回归：这里原先写成 `_CN_DIGITS[n // 10]`，n>=100 时十位取到 10 直接
    IndexError。案子不会有 100 个证人，但崩就是崩。"""
    assert witness_seq_label(n) == expected


def test_witness_label_gives_up_gracefully_above_thousand():
    """再往上不硬凑中文，退回阿拉伯数字——但绝不能抛异常。"""
    for n in (1000, 12345):
        assert witness_seq_label(n) == f"证人{n}"


def test_witness_label_negative_is_not_a_label():
    assert witness_seq_label(-1) == "证人-1"
