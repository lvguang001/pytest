# -*- coding: utf-8 -*-
"""案件数据的磁盘结构（v3：案本号下按人分块）。

磁盘上单个案件文件长这样：

    { "version": "3.0", "cases": { "案本号": {
          "case_id":       "案本号",
          "case_info":     { …案件级 + 流程/扩展字段… },
          "injured_worker":{ …本人（受伤职工）… },
          "witnesses":     [ …N 位证人，每人一条人记录… ],
          "legal_reps":    [ …法人… ],
          "family_reps":   [ …家属（工亡）… ],
    }}}

但**内存里仍是「本人字段平铺在顶层」的 flat 形态**——下游有 80+ 处
`case_obj.get('name')` 这样的读取，还有提示词填充、模板渲染、待办看板全依赖它。
转换只发生在持久化的两端：

    _load_cases_data:  磁盘分块 --unpack_case--> flat
    _save_cases_data:  flat --pack_case------->  磁盘分块

**所以改动时不要动下游的读取代码，只在本模块里调整。**

本模块只有纯函数和 schema 常量：不碰文件系统、不依赖 MainWindow、不 import
app_main，因此可以直接单测（`tests/test_case_store.py`）。文件放哪
（`case.json` / `cases_data.json` / 备份目录）是调用方的事，那些常量留在
`app_main.py`。
"""

from typing import Any, Dict, Optional, Tuple

# 归入 "injured_worker" 块的键（其余一律进 "case_info"）
_INJURED_WORKER_FIELDS = ("name", "gender", "age", "id_card", "address", "phone",
                          "position", "identity", "unit",
                          "injury_description", "materials")
# 本身就是独立"人块"的键，不重复进 case_info
_NAMED_BLOCKS = ("witnesses", "legal_reps", "family_reps")
# 已在块顶单独占位的键，同样不重复进 case_info
_TOP_LEVEL_KEYS = ("case_id",)

# 当前磁盘结构版本。读到更高版本时说明是更新版程序写的，不能静默按旧结构处理。
SCHEMA_VERSION = "3.0"


def version_tuple(text) -> Optional[Tuple[int, ...]]:
    """'3.0' → (3, 0)。无法解析（缺失/乱填）返回 None。"""
    try:
        return tuple(int(p) for p in str(text).split('.'))
    except Exception:
        return None


def is_newer_version(disk_version) -> bool:
    """磁盘上的版本是否高于本程序支持的版本"""
    a, b = version_tuple(disk_version), version_tuple(SCHEMA_VERSION)
    return bool(a and b and a > b)


def pack_case(flat: Dict[str, Any]) -> Dict[str, Any]:
    """内存 flat 案件对象 → 磁盘分块结构（v3）"""
    worker = {k: flat[k] for k in _INJURED_WORKER_FIELDS if k in flat}
    _skip = _INJURED_WORKER_FIELDS + _NAMED_BLOCKS + _TOP_LEVEL_KEYS
    info = {k: v for k, v in flat.items() if k not in _skip}
    return {
        "case_id": flat.get("case_id", ""),
        "case_info": info,
        "injured_worker": worker,
        "witnesses": list(flat.get("witnesses") or []),
        "legal_reps": list(flat.get("legal_reps") or []),
        "family_reps": list(flat.get("family_reps") or []),
    }


def unpack_case(block: Dict[str, Any]) -> Dict[str, Any]:
    """磁盘分块结构（v3）→ 内存 flat 案件对象。

    遇 v2 平铺结构（无 injured_worker 块）原样返回，实现老档向后兼容。
    """
    if not isinstance(block, dict):
        return {}
    if "injured_worker" not in block:
        return dict(block)
    flat = dict(block.get("case_info") or {})
    flat["case_id"] = block.get("case_id", "") or flat.get("case_id", "")
    flat.update(block.get("injured_worker") or {})
    for blk in _NAMED_BLOCKS:
        flat[blk] = list(block.get(blk) or [])
    return flat


def migrate_case(flat: Dict[str, Any]) -> Dict[str, Any]:
    """老档兼容（幂等）：家属记录里的 position 过去存的是「与死者关系」。

    现在 position 改存该家属自己的岗位、关系移入 identity，这里把老值搬到新槽位。

    判据是 identity 键**不存在**，而非"值为空"：新口径的人记录由
    _person_from_flat 生成，PERSON_BASE_FIELDS 的键一律存在（可能是空串）。
    若按"值为空"判断，会把新档里"填了岗位、还没填关系"的家属误判成老档，
    把岗位当成关系搬进身份栏。
    """
    reps = flat.get("family_reps")
    if not reps:
        return flat
    migrated = []
    for fr in reps:
        if isinstance(fr, dict) and "identity" not in fr and fr.get("position"):
            fr = dict(fr)
            fr["identity"] = fr.pop("position")
        migrated.append(fr)
    flat["family_reps"] = migrated
    return flat
