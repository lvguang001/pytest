# -*- coding: utf-8 -*-
"""案件数据磁盘结构的单元测试（`case_store.py`）。

这是 CLAUDE.md 点名「全项目最容易改坏的地方」：内存是 flat、磁盘是分块，
转换只发生在读写两端，下游 80+ 处读取全依赖 flat 形态。

纯函数、不碰 Qt，所以这个文件跑起来最快。
"""

import pytest

from case_store import (
    SCHEMA_VERSION, is_newer_version, migrate_case, pack_case, unpack_case,
    version_tuple,
)


def _flat_case():
    """一个贴近真实的内存 flat 案件对象：本人字段平铺在顶层。"""
    return {
        "case_id": "2026-001",
        # 本人字段（应当进 injured_worker 块）
        "name": "张三", "gender": "男", "age": "40",
        "id_card": "330102198601011234", "address": "温州市某区某路1号",
        "phone": "13800000000", "position": "电焊工", "identity": "职工",
        "unit": "温州某某建筑劳务有限公司",
        "injury_description": "右手食指骨折", "materials": [{"name": "病历资料"}],
        # 案件级字段（应当进 case_info）
        "案件性质": "工伤案件", "申请类型": "单位申请",
        "用人单位": "温州某某建筑劳务有限公司", "unit_type": "企业",
        "service_flow": {"phase": "ask", "done": False},
        # 三个"人块"
        "witnesses": [{"name": "李四"}],
        "legal_reps": [{"name": "王五"}],
        "family_reps": [{"name": "赵六"}],
    }


# ============================================================================
# pack_case
# ============================================================================

def test_pack_separates_worker_and_case_level_fields():
    blk = pack_case(_flat_case())
    assert blk["injured_worker"]["name"] == "张三"
    assert blk["injured_worker"]["materials"] == [{"name": "病历资料"}]
    assert blk["case_info"]["案件性质"] == "工伤案件"
    assert "name" not in blk["case_info"], "本人字段不该同时进 case_info"
    assert "案件性质" not in blk["injured_worker"], "案件级字段不该进 injured_worker"


def test_pack_does_not_repeat_block_names_into_case_info():
    """case_id 和三个名单块已在块顶单独占位，不该在 case_info 里再来一份。"""
    info = pack_case(_flat_case())["case_info"]
    for key in ("case_id", "witnesses", "legal_reps", "family_reps"):
        assert key not in info, f"{key} 被重复塞进了 case_info"


def test_pack_missing_blocks_become_empty_lists():
    """没有证人的案件也要写出空列表，读回来才是 [] 而不是 None。"""
    blk = pack_case({"case_id": "x", "name": "张三"})
    assert blk["witnesses"] == []
    assert blk["legal_reps"] == []
    assert blk["family_reps"] == []
    assert blk["case_info"] == {}


# ============================================================================
# unpack_case
# ============================================================================

def test_pack_then_unpack_round_trips():
    """pack → unpack 应当原样还原（这是 flat 形态能继续给下游用的前提）。"""
    flat = _flat_case()
    assert unpack_case(pack_case(flat)) == flat


def test_unpack_returns_old_flat_structure_as_is():
    """v2 老档（没有 injured_worker 块）原样返回，实现向后兼容。"""
    old = {"case_id": "2025-001", "name": "老李", "案件性质": "工伤案件"}
    assert unpack_case(old) == old


def test_unpack_non_dict_returns_empty():
    for bad in (None, [], "x", 42):
        assert unpack_case(bad) == {}


def test_unpack_worker_block_wins_over_case_info():
    """两边出现同名字段时以 injured_worker 为准（它是本人字段的正主）。"""
    blk = {"case_id": "c1", "case_info": {"name": "错的名字"},
           "injured_worker": {"name": "对的名字"}}
    assert unpack_case(blk)["name"] == "对的名字"


def test_unpack_case_id_falls_back_to_case_info():
    blk = {"case_info": {"case_id": "来自-info"}, "injured_worker": {}}
    assert unpack_case(blk)["case_id"] == "来自-info"


# ============================================================================
# migrate_case —— 老档家属槽位迁移
# ============================================================================

def test_migrate_moves_old_position_into_identity():
    """老档：家属记录里 position 存的是「与死者关系」，没有 identity 键。"""
    flat = {"family_reps": [{"name": "李四", "position": "配偶", "unit": ""}]}
    fr = migrate_case(flat)["family_reps"][0]
    assert fr["identity"] == "配偶"
    assert "position" not in fr, "老值搬走后不该留在原处"


def test_migrate_leaves_new_records_alone():
    """★ 判据是「identity 键**不存在**」，不是「值为空」。

    新口径的人记录由 `_person_from_flat` 生成，PERSON_BASE_FIELDS 的键一律存在
    （可能是空串）。若把判据改成「identity 值为空」，新档里「填了岗位、还没填
    关系」的家属就会被当成老档——岗位被搬进身份栏，而它其实是该家属自己的岗位。
    """
    flat = {"family_reps": [{"name": "张三", "position": "车间主任", "identity": ""}]}
    fr = migrate_case(flat)["family_reps"][0]
    assert fr["position"] == "车间主任", "新档的岗位被误当成「与死者关系」搬走了"
    assert fr["identity"] == "", "关系栏不该被岗位覆盖"
    assert "position" in fr


def test_migrate_is_idempotent():
    """跑两遍结果必须一样（启动时每次读盘都会跑）。"""
    flat = {"family_reps": [{"name": "李四", "position": "配偶"}]}
    once = migrate_case(flat)["family_reps"]
    twice = migrate_case({"family_reps": [dict(r) for r in once]})["family_reps"]
    assert once == twice


def test_migrate_ignores_records_without_position():
    flat = {"family_reps": [{"name": "王五", "unit": "某公司"}]}
    fr = migrate_case(flat)["family_reps"][0]
    assert "identity" not in fr
    assert fr["unit"] == "某公司"


def test_migrate_without_family_reps_returns_input():
    assert migrate_case({}) == {}
    assert migrate_case({"family_reps": []}) == {"family_reps": []}


def test_migrate_tolerates_broken_entries():
    """名单里混进非 dict 也不能炸（旧文件什么形状都可能）。"""
    flat = {"family_reps": ["不是字典", None, {"name": "李四", "position": "配偶"}]}
    reps = migrate_case(flat)["family_reps"]
    assert reps[0] == "不是字典" and reps[1] is None
    assert reps[2]["identity"] == "配偶"


# ============================================================================
# 版本
# ============================================================================

@pytest.mark.parametrize("text,expected", [
    ("3.0", (3, 0)), ("3", (3,)), ("10.2.1", (10, 2, 1)),
])
def test_version_tuple_parses(text, expected):
    assert version_tuple(text) == expected


@pytest.mark.parametrize("bad", [None, "", "三", "3.x", "3..0", {}, []])
def test_version_tuple_returns_none_on_garbage(bad):
    assert version_tuple(bad) is None


def test_is_newer_version_compares_against_this_program():
    assert is_newer_version("99.0") is True
    assert is_newer_version("1.0") is False
    assert is_newer_version(SCHEMA_VERSION) is False
    # 读不出来时不能谎报"更新"——否则会对正常文件刷一堆 ERROR
    assert is_newer_version(None) is False
    assert is_newer_version("乱填") is False
