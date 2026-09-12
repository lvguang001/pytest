# -*- coding: utf-8 -*-
"""cases_data.json 投影层回归测试（pack_case / unpack_case / migrate_case）。

这三个函数守在案卷数据的磁盘边界上：磁盘分块 ↔ 内存 flat。改错一处，
所有人的案件就读不出来了，所以它们是测试的第一优先级。

本文件只测纯函数，不依赖 Qt，跑得很快。
"""
import copy

import pytest

from app_main import (
    _INJURED_WORKER_FIELDS,
    _NAMED_BLOCKS,
    _TOP_LEVEL_KEYS,
    migrate_case,
    pack_case,
    unpack_case,
)


@pytest.fixture
def flat_case():
    """一个字段齐全的 flat 案件对象（内存形态，与 _build_case_object 产出一致）"""
    return {
        "case_id": "20260912001",
        "case_nature": "工亡案件",
        "applicant_type": "个人申请",
        "unit_type": "企业",
        "employer": "永嘉县XX建设工程有限公司",
        "labor_unit": "温州YY建筑劳务有限公司",
        "site": "ZZ新城项目一期工地",
        "apply_time": "20260910",
        "accept_time": "",
        "visit_time": "",
        "injury_time": "20260802",
        "proposed_article": "第十四条第一款第一项",
        "proposed_article_elements": ["工作时间", "工作场所"],
        "recorder": "吕广",
        "applicant_name": "赵六",
        # ── 本人 ──
        "name": "赵六",
        "gender": "男",
        "age": "45",
        "id_card": "330324198111223333",
        "phone": "13600003333",
        "address": "浙江省温州市XX路",
        "position": "钢筋工",
        "identity": "职工",
        "unit": "温州ZZ劳务有限公司",
        "injury_description": "在工地作业时突发疾病…",
        "materials": [{"name": "身份证复印件", "provided": True, "notes": ""}],
        # ── 人块 ──
        "witnesses": [{"role": "证人", "seq": "证人一", "name": "刘大",
                       "position": "钢筋工", "identity": "职工", "unit": "别的公司"}],
        "legal_reps": [{"role": "法人", "name": "王老板", "position": "总经理"}],
        "family_reps": [{"role": "家属", "name": "赵妻", "position": "缝纫工",
                         "identity": "夫妻", "unit": "温州XX服装有限公司"}],
    }


# ============================================================================
# pack / unpack 往返
# ============================================================================

class TestPackUnpack:

    def test_往返无损(self, flat_case):
        assert unpack_case(pack_case(flat_case)) == flat_case

    def test_pack幂等(self, flat_case):
        once = pack_case(flat_case)
        assert pack_case(unpack_case(once)) == once

    def test_case_id只在块顶不重复进case_info(self, flat_case):
        packed = pack_case(flat_case)
        assert packed["case_id"] == "20260912001"
        assert "case_id" not in packed["case_info"]

    def test_本人字段全部进injured_worker(self, flat_case):
        packed = pack_case(flat_case)
        for key in _INJURED_WORKER_FIELDS:
            assert key not in packed["case_info"], f"{key} 不应留在 case_info"
        assert packed["injured_worker"]["name"] == "赵六"
        assert packed["injured_worker"]["unit"] == "温州ZZ劳务有限公司"

    def test_人块不重复进case_info(self, flat_case):
        info = pack_case(flat_case)["case_info"]
        for blk in _NAMED_BLOCKS + _TOP_LEVEL_KEYS:
            assert blk not in info, f"{blk} 不应出现在 case_info"

    def test_案件级字段留在case_info(self, flat_case):
        info = pack_case(flat_case)["case_info"]
        assert info["case_nature"] == "工亡案件"
        assert info["labor_unit"] == "温州YY建筑劳务有限公司"
        assert info["proposed_article_elements"] == ["工作时间", "工作场所"]

    def test_未知扩展键不丢失(self, flat_case):
        """service_flow / folder_name 这类后加的扩展键必须原样往返"""
        flat_case["service_flow"] = {"stage": "ask"}
        flat_case["folder_name"] = "赵六-工亡20260912001"
        flat_case["未来新增的键"] = {"任意": "值"}
        assert unpack_case(pack_case(flat_case)) == flat_case

    def test_空案件不炸(self):
        packed = pack_case({})
        assert packed["case_id"] == ""
        assert packed["case_info"] == {}
        assert unpack_case(packed)["witnesses"] == []

    def test_缺失的人块补成空列表(self):
        packed = pack_case({"case_id": "x"})
        for blk in _NAMED_BLOCKS:
            assert packed[blk] == []

    def test_多证人顺序保持(self, flat_case):
        flat_case["witnesses"] = [
            {"role": "证人", "seq": "证人一", "name": "甲", "unit": "甲公司"},
            {"role": "证人", "seq": "证人二", "name": "乙", "unit": "乙公司"},
            {"role": "证人", "seq": "证人三", "name": "丙", "unit": "丙公司"},
        ]
        back = unpack_case(pack_case(flat_case))
        assert [w["name"] for w in back["witnesses"]] == ["甲", "乙", "丙"]
        assert [w["unit"] for w in back["witnesses"]] == ["甲公司", "乙公司", "丙公司"]

    def test_不修改传入对象(self, flat_case):
        before = copy.deepcopy(flat_case)
        pack_case(flat_case)
        assert flat_case == before


# ============================================================================
# 老档（v2 平铺）兼容
# ============================================================================

class TestV2兼容:

    def test_v2平铺原样透传(self):
        v2 = {"case_id": "old1", "name": "老张", "labor_unit": "老公司",
              "witnesses": [], "legal_reps": [], "family_reps": []}
        assert unpack_case(v2) == v2

    def test_v2透传不别名原对象(self):
        v2 = {"case_id": "old1", "name": "老张"}
        out = unpack_case(v2)
        out["name"] = "改过了"
        assert v2["name"] == "老张"

    @pytest.mark.parametrize("bad", [None, "字符串", 123, []])
    def test_非字典输入返回空(self, bad):
        assert unpack_case(bad) == {}


# ============================================================================
# 家属老档迁移（position 里的「与死者关系」搬到 identity）
# ============================================================================

class Test家属迁移:

    def test_老记录position搬进identity(self):
        """老档家属记录没有 identity 键，position 存的是关系 → 应搬过去"""
        old = {"name": "王母", "position": "母子", "role": "家属"}
        out = migrate_case({"family_reps": [old]})["family_reps"][0]
        assert out["identity"] == "母子"
        assert "position" not in out
        assert out["name"] == "王母"

    def test_新记录岗位不被误搬(self):
        """回归：新口径记录带 identity 键（可能为空串）。

        若按"identity 值为空"判断，会把"填了岗位、还没填关系"的新档家属
        误判成老档，把岗位当成关系搬进身份栏。
        """
        new = {"name": "赵妻", "position": "缝纫工", "identity": "",
               "unit": "温州XX服装有限公司", "role": "家属"}
        out = migrate_case({"family_reps": [new]})["family_reps"][0]
        assert out["position"] == "缝纫工", "岗位被误搬走了"
        assert out["identity"] == "", "身份栏被写入了岗位"

    def test_已有关系的老记录不被覆盖(self):
        rec = {"name": "赵妻", "position": "", "identity": "夫妻"}
        out = migrate_case({"family_reps": [rec]})["family_reps"][0]
        assert out["identity"] == "夫妻"

    def test_幂等(self):
        flat = {"family_reps": [{"name": "王母", "position": "母子"}]}
        once = migrate_case(flat)
        assert migrate_case(copy.deepcopy(once))["family_reps"][0] == once["family_reps"][0]

    def test_不修改传入的记录(self):
        old = {"name": "王母", "position": "母子", "role": "家属"}
        migrate_case({"family_reps": [old]})
        assert old == {"name": "王母", "position": "母子", "role": "家属"}

    def test_无家属时原样返回(self):
        flat = {"case_id": "x", "witnesses": []}
        assert migrate_case(flat) == {"case_id": "x", "witnesses": []}

    def test_家属列表为空不炸(self):
        assert migrate_case({"family_reps": []}) == {"family_reps": []}

    def test_非字典条目原样保留(self):
        out = migrate_case({"family_reps": [None, "脏数据"]})["family_reps"]
        assert out == [None, "脏数据"]
