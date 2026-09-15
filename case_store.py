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

import datetime
import json
import logging
import os
import re
import shutil
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

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


# ============================================================================
# 案件文件的磁盘布局与读写（一案一文件）
# ----------------------------------------------------------------------------
# 每个案件的数据是 <BASE_PATH>/<年份>/<案本号>/case.json，与它生成的文书同处
# 一个案卷文件夹，而不是全部挤在一个 cases_data.json 里。好处：某个案件的文件
# 坏了只影响它自己；数据跟着案卷走，删/拷一个案卷就是删/拷一个案件。
#
# 这组函数原先全是 MainWindow 的方法（用 self.BASE_PATH），2026-09 搬到这里、
# 把 base_path 显式当参数传——它们跟界面无关，拆出来才能不构造窗口直接单测。
# ============================================================================

_CASE_FILE_NAME = "case.json"
_LEGACY_CASES_FILE = "cases_data.json"      # 旧版「一个全库文件」，仅迁移时读

# 保存前的备份层
_BACKUP_DIR = "backups"        # 每日快照目录（与案件目录同级）
_SNAPSHOT_KEEP = 30            # 每日快照保留份数


def legacy_cases_path(base_path: str) -> str:
    """旧版「一个全库文件」的路径（只在迁移时用到）"""
    return os.path.join(base_path, _LEGACY_CASES_FILE)


def safe_case_dirname(case_id: str) -> str:
    """案本号 → 可当 Windows 目录名：换掉非法字符、去掉首尾的点和空格；空了给个占位名"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', str(case_id or '')).strip(' .')
    return name or '未命名案件'


def year_for_case(case_id: str) -> str:
    """归档年份：优先取案本号里的立案日期（那本来就是立案时的系统时间），
    取不到再用当前系统年份。"""
    text = str(case_id or '')
    m = (re.search(r'(?:案本|工亡)(20\d{2})\d{4}', text)
         or re.search(r'(20\d{2})\d{2}\d{2}', text))
    return m.group(1) if m else str(datetime.datetime.now().year)


def locate_case_dir(base_path: str, case_id: str) -> str:
    """找已存在的案卷目录，找不到返回空串。

    两种布局都认：<BASE>/<年份>/<案本号>（分年份之后）与 <BASE>/<案本号>
    （分年份之前存的）。老案卷命中后**留在原地**——搬它就会把同处一处的
    文书和数据分开，也会让用户以为文件丢了。
    """
    name = safe_case_dirname(case_id)
    plain = os.path.join(base_path, name)
    if os.path.isdir(plain):
        return plain
    try:
        for entry in sorted(os.listdir(base_path)):
            cand = os.path.join(base_path, entry, name)
            if os.path.isdir(cand):
                return cand
    except OSError:
        pass
    return ''


def case_dir(base_path: str, case_id: str) -> str:
    """某个案件的案卷目录（案件数据与它生成的文书同处一处）

    已存在的按原位返回；新案件落到 <BASE_PATH>/<年份>/<案本号>/。年份只在
    第一次落盘时定下（写在目录名上），之后靠“找得到就用原来的”保证不会被搬走。
    """
    found = locate_case_dir(base_path, case_id)
    if found:
        return found
    return os.path.join(base_path, year_for_case(case_id), safe_case_dirname(case_id))


def case_file(base_path: str, case_id: str) -> str:
    """某个案件的数据文件"""
    return os.path.join(case_dir(base_path, case_id), _CASE_FILE_NAME)


def iter_case_files(base_path: str):
    """遍历现有案件数据文件，产出 (案本号, 文件路径)

    两种布局都认：<BASE>/<年份>/<案本号>/case.json 与 <BASE>/<案本号>/case.json。
    案本号是定位依据——新布局里它是年份目录的下一层，老布局里就是第一层。
    """
    try:
        entries = sorted(os.listdir(base_path))
    except OSError:
        return
    for entry in entries:
        top = os.path.join(base_path, entry)
        if not os.path.isdir(top):
            continue
        path = os.path.join(top, _CASE_FILE_NAME)
        if os.path.isfile(path):
            yield entry, path                      # 老布局：<BASE>/<案本号>/
            continue
        try:
            for name in sorted(os.listdir(top)):
                p = os.path.join(top, name, _CASE_FILE_NAME)
                if os.path.isfile(p):
                    yield name, p                  # 新布局：<BASE>/<年份>/<案本号>/
        except OSError:
            pass


def load_all(base_path: str) -> Dict[str, Any]:
    """加载全部案件，返回 {case_id: case_obj}

    单个案件的文件读不出来，只跳过它自己并记 ERROR，不影响其它案件——这正是从
    「一个全库文件」改成一案一文件要买的东西。版本高于本程序时仍照旧读出来，只记 ERROR。
    """
    migrate_legacy_file(base_path)
    out: Dict[str, Any] = {}
    for dirname, path in iter_case_files(base_path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                blk = json.load(f)
        except Exception as e:
            logger.error("❌ 跳过读不出来的案件文件 %s: %s", path, e)
            continue
        if not isinstance(blk, dict):
            logger.error("❌ 跳过结构异常的案件文件（顶层不是对象）: %s", path)
            continue
        disk_version = blk.get('version')
        if is_newer_version(disk_version):
            logger.error(
                "❌ 案件数据版本(%s)高于本程序支持的(%s)——应是更新版程序写的。"
                "保存时会先自动备份原文件，但请尽快改用新版程序打开，"
                "否则新版本新增的字段可能在这里丢失。",
                disk_version, SCHEMA_VERSION)
        if str(blk.get('case_id', '') or '').strip() not in ('', dirname):
            logger.warning("⚠️ 案件文件里的 case_id(%s) 与目录名(%s) 不一致，以目录名为准",
                           blk.get('case_id'), dirname)
        flat = migrate_case(unpack_case(blk))
        flat['case_id'] = dirname          # 目录名是定位依据，文件里的只作校验
        out[dirname] = flat
    return out


def write_case(base_path: str, case_id: str, block: Dict[str, Any]) -> str:
    """原子写单个案件文件；写前把旧内容转存 .bak（每次刷新 = 「撤销上一次保存」）

    直接以 'w' 打开会立刻截断，写到一半崩溃或磁盘写满，这个案件就没了，
    所以先写 .tmp 再 os.replace。
    """
    path = case_file(base_path, case_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({"version": SCHEMA_VERSION, **block},
                  f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    # 备份放在「新内容已经写进 .tmp 但还没替换」这一步，而不是开头：
    # 写失败时不该把上一次的回滚点也覆盖掉，否则一次失败的保存会白丢一个还原点。
    if os.path.exists(path):
        try:
            shutil.copy2(path, path + '.bak')
        except Exception as e:
            logger.warning("⚠️ 备份案件文件失败（继续）: %s", e)
    os.replace(tmp, path)
    return path


def drop_case_files_not_in(base_path: str, keep_ids) -> None:
    """删掉「磁盘上有、这次却没保存」的案件数据文件（维持「写全量」的语义）

    只删 case.json / case.json.bak，**不动案卷里的文书**——文书是办案成果，
    不该因为数据里没有这个案子就被清掉。
    """
    keep = {safe_case_dirname(cid) for cid in keep_ids}
    for dirname, path in list(iter_case_files(base_path)):
        if dirname in keep:
            continue
        for p in (path, path + '.bak'):
            try:
                if os.path.exists(p):
                    os.remove(p)
                    logger.info("🗑️ 已删除不再存在的案件数据: %s", p)
            except Exception as e:
                logger.warning("⚠️ 删除案件数据失败 %s: %s", p, e)


def daily_snapshot(base_path: str, packed: Dict[str, Any]) -> None:
    """当天第一份快照（仍是「一份全库」，与改造前同名同语义）；顺带清掉过老的快照"""
    backup_dir = os.path.join(base_path, _BACKUP_DIR)
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    dest = os.path.join(backup_dir, f"cases_data_{stamp}.json")
    if os.path.exists(dest):
        return
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump({"version": SCHEMA_VERSION, "cases": packed},
                  f, ensure_ascii=False, indent=2)
    logger.info("📦 已生成当日案件数据快照: %s", dest)
    stale = sorted(f for f in os.listdir(backup_dir)
                   if f.startswith("cases_data_") and f.endswith(".json"))
    for old in stale[:-_SNAPSHOT_KEEP]:
        try:
            os.remove(os.path.join(backup_dir, old))
        except Exception:
            pass


def save_all(base_path: str, cases: Dict[str, Any]) -> bool:
    """保存全部案件（一案一文件；内存 flat → 磁盘分块 v3）

    每个文件各自原子写：某个案件写失败只影响它自己，不会连带丢掉别的案件。代价是
    N 个文件做不到「跨案件全有全无」——中途失败会留下部分已写，下次保存会再刷一遍
    （幂等），所以这里只把失败如实返回 False。
    传入的字典是「全部真相」：磁盘上有、字典里没有的案件数据文件会被删掉（只删数据，不动文书）。
    """
    try:
        os.makedirs(base_path, exist_ok=True)
        packed = {cid: pack_case(c) for cid, c in cases.items()}
        for cid, blk in packed.items():
            write_case(base_path, cid, blk)
        drop_case_files_not_in(base_path, packed)
        daily_snapshot(base_path, packed)
        print(f"✅ 案件数据已保存: {len(packed)} 个案件（一案一文件）")
        return True
    except Exception as e:
        logger.error("❌ 保存案件数据失败: %s", e)
        return False


def migrate_legacy_file(base_path: str) -> None:
    """把旧的「一个全库文件」拆成一案一文件（幂等、可回退）

    - 触发：<BASE_PATH>/cases_data.json 还在（迁移成功后它被改名 .migrated，不再触发）
    - 原文件只改名不删除：cases_data.json → cases_data.json.migrated，便于切回旧版程序
    - 版本非当前时另留一份 cases_data.json.v2.bak（原样备份，旧版程序能读）
    - 已存在的案件文件不覆盖：中途失败下次启动接着补，不会把新数据盖掉
    - 任何失败都不动原文件，下次启动重试
    """
    legacy = legacy_cases_path(base_path)
    if not os.path.exists(legacy):
        return
    try:
        with open(legacy, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.error("❌ 旧案件数据读不出来，跳过迁移: %s", e)
        return
    if not (isinstance(data, dict) and isinstance(data.get('cases'), dict)):
        logger.error("❌ 旧案件数据结构异常（顶层缺少 cases 对象），跳过迁移: %s", legacy)
        return

    legacy_version = data.get('version')
    try:
        written = skipped = 0
        for cid, blk in data['cases'].items():
            if os.path.exists(case_file(base_path, cid)):
                skipped += 1
                continue
            flat = migrate_case(unpack_case(blk))
            flat['case_id'] = cid          # 旧文件里案件字典的键就是案本号
            write_case(base_path, cid, pack_case(flat))
            written += 1
        # 全部写成功后才动原文件
        if legacy_version not in ('', SCHEMA_VERSION):
            bak = legacy + '.v2.bak'
            if not os.path.exists(bak):
                shutil.copy2(legacy, bak)
                print(f"📦 已保留升级前的原案件数据: {bak}")
        os.replace(legacy, legacy + '.migrated')
        logger.info("📦 已迁移为一案一文件：新写 %d 个、跳过已存在 %d 个", written, skipped)
        print(f"📦 案件数据已拆成一案一文件（新写 {written} 个），"
              f"原文件改名 {_LEGACY_CASES_FILE}.migrated 留底")
    except Exception as e:
        logger.error("❌ 迁移为一案一文件失败（原文件保持不动，下次启动重试）: %s", e)


def update_case_field(base_path: str, case_number: str, **fields) -> bool:
    """把字段写回指定案件的数据文件"""
    try:
        cases = load_all(base_path)
        case_obj = cases.get(case_number)
        if case_obj is None:
            return False
        case_obj.update(fields)
        save_all(base_path, cases)
        return True
    except Exception as e:
        logger.warning(f"⚠️ 更新案件字段失败（非致命）: {e}")
        return False
