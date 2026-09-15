# -*- coding: utf-8 -*-
"""案件数据磁盘结构的单元测试（`case_store.py`）。

这是 CLAUDE.md 点名「全项目最容易改坏的地方」：内存是 flat、磁盘是分块，
转换只发生在读写两端，下游 80+ 处读取全依赖 flat 形态。

纯函数、不碰 Qt，所以这个文件跑起来最快。
"""

import json
import os

import pytest

from case_store import (
    SCHEMA_VERSION, is_newer_version, migrate_case, pack_case, unpack_case,
    version_tuple,
    case_dir, case_file, daily_snapshot, drop_case_files_not_in, iter_case_files,
    load_all, locate_case_dir, migrate_legacy_file, safe_case_dirname, save_all,
    update_case_field, write_case, year_for_case,
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


# ============================================================================
# 磁盘布局与读写（原先是 MainWindow 的方法，2026-09 搬到 case_store 并参数化）
# ============================================================================

# —— 目录名与年份 ——

@pytest.mark.parametrize("raw,expected", [
    ("2026-001", "2026-001"),
    ('a/b\c:d*e?f"g<h>i|j', "a_b_c_d_e_f_g_h_i_j"),
    ("  ..名字..  ", "名字"),
    ("", "未命名案件"),
    (None, "未命名案件"),
])
def test_safe_case_dirname(raw, expected):
    assert safe_case_dirname(raw) == expected


@pytest.mark.parametrize("case_id,expected", [
    ("案本202609071111", "2026"),
    ("工亡202512310001", "2025"),
    ("20260907", "2026"),
])
def test_year_for_case_reads_it_from_the_case_id(case_id, expected):
    assert year_for_case(case_id) == expected


def test_year_for_case_falls_back_to_this_year():
    import datetime
    assert year_for_case("没有日期的案本号") == str(datetime.datetime.now().year)


# —— 布局 ——

def test_new_case_lands_under_a_year_folder(tmp_path):
    base = str(tmp_path)
    assert case_dir(base, "案本202609071111") == os.path.join(
        base, "2026", "案本202609071111")
    assert case_file(base, "案本202609071111") == os.path.join(
        base, "2026", "案本202609071111", "case.json")


def test_existing_case_stays_where_it_is(tmp_path):
    """老布局（分年份之前存的 <BASE>/<案本号>/）命中后要留在原地。

    搬它会把同处一处的文书和数据分开，用户还会以为文件丢了。
    """
    base = str(tmp_path)
    old = os.path.join(base, "案本202500011111")
    os.makedirs(old)
    assert locate_case_dir(base, "案本202500011111") == old
    assert case_dir(base, "案本202500011111") == old


def test_iter_case_files_finds_both_layouts(tmp_path):
    base = str(tmp_path)
    os.makedirs(os.path.join(base, "2026", "新案"))
    open(os.path.join(base, "2026", "新案", "case.json"), "w").close()
    os.makedirs(os.path.join(base, "老案"))
    open(os.path.join(base, "老案", "case.json"), "w").close()
    os.makedirs(os.path.join(base, "2026", "空目录"))      # 没有 case.json
    found = dict(iter_case_files(base))
    assert set(found) == {"新案", "老案"}


def test_iter_case_files_on_missing_dir_is_empty(tmp_path):
    assert list(iter_case_files(str(tmp_path / "不存在"))) == []


# —— 存 / 读 ——

def test_save_then_load_round_trips(tmp_path):
    base = str(tmp_path)
    case = {"case_id": "案本202609070001", "name": "张三", "案件性质": "工伤案件",
            "witnesses": [{"name": "李四"}], "legal_reps": [], "family_reps": []}
    assert save_all(base, {"案本202609070001": case}) is True
    assert load_all(base) == {"案本202609070001": case}


def test_saved_file_carries_the_schema_version(tmp_path):
    base = str(tmp_path)
    save_all(base, {"c1": {"case_id": "c1", "name": "张三"}})
    disk = json.loads(open(case_file(base, "c1"), encoding="utf-8").read())
    assert disk["version"] == SCHEMA_VERSION
    assert disk["injured_worker"]["name"] == "张三"


def test_write_case_is_atomic_and_leaves_no_tmp(tmp_path):
    base = str(tmp_path)
    write_case(base, "c1", {"case_id": "c1"})
    d = os.path.dirname(case_file(base, "c1"))
    assert not [f for f in os.listdir(d) if f.endswith(".tmp")]


def test_write_case_failure_leaves_the_original_intact(tmp_path, monkeypatch):
    """★ 写到一半失败，原文件必须还在——这才是「原子写」的全部意义。

    直接以 'w' 打开会立刻截断：写失败时这个案件就没了。
    （只断言「没留下 .tmp」是不够的——改回截断写法照样通过，是假测试。）
    """
    import case_store as cs

    base = str(tmp_path)
    write_case(base, "c1", {"case_id": "c1", "case_info": {"名字": "原内容"}})
    original = open(case_file(base, "c1"), encoding="utf-8").read()

    def boom(*a, **k):
        raise OSError("磁盘写满")

    monkeypatch.setattr(cs.json, "dump", boom)
    with pytest.raises(OSError):
        write_case(base, "c1", {"case_id": "c1", "case_info": {"名字": "新内容"}})
    assert open(case_file(base, "c1"), encoding="utf-8").read() == original,         "写失败把原文件毁了"


def test_write_case_backs_up_the_previous_content(tmp_path):
    """第二次写之前把旧内容存成 .bak —— 相当于「撤销上一次保存」。"""
    base = str(tmp_path)
    write_case(base, "c1", {"case_id": "c1", "case_info": {"名字": "旧"}})
    write_case(base, "c1", {"case_id": "c1", "case_info": {"名字": "新"}})
    bak = json.loads(open(case_file(base, "c1") + ".bak", encoding="utf-8").read())
    assert bak["case_info"]["名字"] == "旧"


def test_load_all_skips_a_broken_file_without_losing_the_others(tmp_path):
    """从「一个全库文件」改成一案一文件，买的就是这个。"""
    base = str(tmp_path)
    save_all(base, {"good": {"case_id": "good", "name": "张三"}})
    bad_dir = os.path.join(base, "2026", "bad")
    os.makedirs(bad_dir)
    open(os.path.join(bad_dir, "case.json"), "w", encoding="utf-8").write("{ 不是 json")
    loaded = load_all(base)
    assert set(loaded) == {"good"}          # 坏的那个被跳过，好的照常读出来


def test_update_case_field_writes_back(tmp_path):
    base = str(tmp_path)
    save_all(base, {"c1": {"case_id": "c1", "name": "张三"}})
    assert update_case_field(base, "c1", name="李四") is True
    assert load_all(base)["c1"]["name"] == "李四"
    assert update_case_field(base, "不存在", name="x") is False


# —— 删除「这次没保存」的案件数据 ——

def test_drop_removes_data_files_not_kept(tmp_path):
    base = str(tmp_path)
    save_all(base, {"keep": {"case_id": "keep"}, "drop": {"case_id": "drop"}})
    drop_case_files_not_in(base, ["keep"])
    assert set(load_all(base)) == {"keep"}


def test_drop_never_touches_the_documents(tmp_path):
    """★ 只删 case.json，**不动案卷里的文书**——文书是办案成果。"""
    base = str(tmp_path)
    save_all(base, {"drop": {"case_id": "drop"}})
    d = os.path.dirname(case_file(base, "drop"))
    doc = os.path.join(d, "张三谈话笔录.docx")
    open(doc, "w", encoding="utf-8").write("文书内容")
    drop_case_files_not_in(base, [])
    assert not os.path.exists(case_file(base, "drop"))
    assert os.path.exists(doc), "文书被连坐删掉了"


# —— 快照 ——

def test_daily_snapshot_is_written_once_a_day(tmp_path):
    base = str(tmp_path)
    packed = {"c1": {"case_id": "c1"}}
    daily_snapshot(base, packed)
    snap = os.listdir(os.path.join(base, "backups"))
    assert len(snap) == 1 and snap[0].startswith("cases_data_")
    first = open(os.path.join(base, "backups", snap[0]), encoding="utf-8").read()
    daily_snapshot(base, {"c1": {"case_id": "改过了"}})      # 当天第二次
    assert open(os.path.join(base, "backups", snap[0]), encoding="utf-8").read() == first


# —— 老档迁移：一个全库文件 → 一案一文件 ——

def _legacy_file(base, cases, version="2.0"):
    path = os.path.join(base, "cases_data.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": version, "cases": cases}, f, ensure_ascii=False)
    return path


def test_migrate_splits_the_legacy_file_and_keeps_it_as_migrated(tmp_path):
    base = str(tmp_path)
    legacy = _legacy_file(base, {
        "c1": {"case_id": "c1", "name": "张三"},
    })
    migrate_legacy_file(base)
    assert not os.path.exists(legacy), "原文件该改名而不是留在原地"
    assert os.path.exists(legacy + ".migrated")
    assert os.path.exists(legacy + ".v2.bak"), "版本非当前时该留一份旧版程序能读的备份"
    assert load_all(base)["c1"]["name"] == "张三"


def test_migrate_is_idempotent(tmp_path):
    base = str(tmp_path)
    _legacy_file(base, {"c1": {"case_id": "c1", "name": "张三"}})
    migrate_legacy_file(base)
    before = load_all(base)
    migrate_legacy_file(base)          # 再跑一遍：原文件已改名，直接返回
    assert load_all(base) == before


def test_migrate_does_not_overwrite_existing_case_files(tmp_path):
    """中途失败下次启动接着补，不能把已经写好的新数据盖掉。"""
    base = str(tmp_path)
    save_all(base, {"c1": {"case_id": "c1", "name": "新数据"}})
    _legacy_file(base, {"c1": {"case_id": "c1", "name": "老数据"}})
    migrate_legacy_file(base)
    assert load_all(base)["c1"]["name"] == "新数据"


def test_migrate_leaves_a_broken_legacy_file_alone(tmp_path):
    base = str(tmp_path)
    path = os.path.join(base, "cases_data.json")
    open(path, "w", encoding="utf-8").write("{ 不是 json")
    migrate_legacy_file(base)
    assert os.path.exists(path), "读不出来时不该动原文件"
