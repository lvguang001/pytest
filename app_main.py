import os
import json
import re
import shutil
import datetime
import logging
from typing import Dict, List, Any, Tuple, Optional
from ctypes import windll, byref, create_string_buffer, c_int32, c_uint
import pandas as pd
from jinja2 import Environment, StrictUndefined
from docx import Document
from docxtpl import DocxTemplate
from PyQt5.Qt import *
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QApplication, QWidget, QMessageBox, QDialog, QVBoxLayout,
    QLabel, QTextEdit, QPushButton, QHBoxLayout, QInputDialog,
    QLineEdit, QCompleter, QCheckBox, QProgressDialog,
    QTableWidget, QTableWidgetItem,
)
from PyQt5.QtGui import QFont
from ui_main_build import MainWindowUI
# 控件类搬到了 material_list.py；这里保留一份再导出，外部 `from app_main import
# MaterialListWidget` 的老写法仍然可用
from material_list import MaterialListWidget  # noqa: F401
from services import FileService, DataService, TemplateVariableManager
from ai_service import AIService
from case_classifier import CaseClassifier
from config_service import ConfigService
from path_utils import path_utils
import log_utils
from main import UserManager
from service_flow import (derive, initial_sf, confirm_delivery, revert_to_ask,
                          delivered_again, finish_flow, DOC_LABEL, today_iso)
from todo_board import (DeliveryConfirmDialog, PostalTrackingDialog,
                        DecisionConfirmDialog)

logger = logging.getLogger(__name__)

# 设置日志级别
logging.getLogger('config_service').setLevel(logging.WARNING)


# ============================================================================
# 工具函数
# ============================================================================

def _date_now() -> str:
    """当前日期，格式：2025年01月01日"""
    import datetime as _dt
    return _dt.datetime.now().strftime('%Y年%m月%d日')


def _time_now() -> str:
    """当前时间，格式：14时30分"""
    import datetime as _dt
    return _dt.datetime.now().strftime('%H时%M分')


def _timestamp_now() -> str:
    """时间戳，格式：20250101_143000"""
    import datetime as _dt
    return _dt.datetime.now().strftime('%Y%m%d_%H%M%S')


_CN_DIGITS = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九']


def _witness_label(n: int) -> str:
    """把序号转成中文证人编号：1→证人一, 2→证人二, 10→证人十, 11→证人十一, 21→证人二十一"""
    if n <= 0:
        return f"证人{n}"

    if n <= 10:
        body = _CN_DIGITS[n] if n < 10 else "十"
    elif n < 20:
        body = "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    else:
        tens = n // 10
        ones = n % 10
        body = _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")

    return f"证人{body}"


# ============================================================================
# 统一"人记录"schema(本人/证人/法人共用一份字段结构)
# ============================================================================

# 一份"人记录"的规范英文键。case_obj 顶层的 本人 即 role=本人 的记录；
# 证人 / 法人 数组元素 = 同样这些字段 + role(+ 可选 seq / materials)。
# unit = 该人自己的工作单位，各自独立（证人/家属不必与案件用人单位相同）。
PERSON_BASE_FIELDS = ("name", "gender", "age", "id_card", "address", "phone",
                      "position", "identity", "unit")

# 案件级：用人单位性质（企业 / 机关（公务员） / 事业单位），默认企业
UNIT_TYPES = ["企业", "机关（公务员）", "事业单位"]
DEFAULT_UNIT_TYPE = "企业"
DEFAULT_IDENTITY = "职工"

# 单位性质 → 该单位人员的中文称谓（用于给 AI 的「称谓提示」）。
# 下拉框的标签（如「机关（公务员）」）带括号，不能直接拼进句子里，故单独映射；企业档不提示。
UNIT_TYPE_APPELLATION = {
    "机关（公务员）": "机关工作人员",
    "事业单位": "事业单位工作人员",
}

# canonical 英文键 → 中文后缀（用于拼 本人姓名/证人姓名/… 兼容扁平键）
PERSON_CN_SUFFIX = {
    "name": "姓名",
    "gender": "性别",
    "age": "年龄",
    "id_card": "身份证号",
    "address": "身份证地址",
    "phone": "手机号",
    "identity": "身份",
    "unit": "单位名称",
}
# position 语义随角色：扁平兼容键 本人岗位/证人岗位/法人职务/家属岗位
_FLAT_POSITION_SUFFIX = {"本人": "岗位", "证人": "岗位", "法人": "职务", "家属": "岗位"}

# 「身份」输入行在各角色下的语义：家属填的是与死者的关系，其余填用工身份。
# 标签一律用短词（那行要和 电话/岗位 并排，长标签会把输入框挤没），
# 完整语义放在 tooltip 里说明。
_ROLE_IDENTITY_LABEL = {"本人": "身份：", "证人": "身份：",
                        "法人": "身份：", "家属": "关系："}
_ROLE_IDENTITY_HINT = {"本人": "本人身份：职工 / 公务员 / 事业编制工作人员 等",
                       "证人": "该谈话人身份：职工 / 公务员 / 事业编制工作人员 等",
                       "法人": "该谈话人身份：法定代表人 / 负责人 等",
                       "家属": "家属与死者的关系：配偶 / 子女 / 父母 等"}


def person_flat_key(role: str, field: str) -> str:
    """canonical 字段 → 角色前缀中文兼容扁平键，如 ('本人','name')→'本人姓名'、('法人','position')→'法人职务'"""
    if field == "position":
        return f"{role}{_FLAT_POSITION_SUFFIX.get(role, '岗位')}"
    return f"{role}{PERSON_CN_SUFFIX.get(field, field)}"


# ============================================================================
# cases_data.json 磁盘结构（v3：案本号下按人分块）
# ----------------------------------------------------------------------------
#   { "version": "3.0", "cases": { "案本号": {
#         "case_id":       "案本号",
#         "case_info":     { …案件级 + 流程/扩展字段… },
#         "injured_worker":{ …本人（受伤职工）… },
#         "witnesses":     [ …N 位证人，每人一条人记录… ],
#         "legal_reps":    [ …法人… ],
#         "family_reps":   [ …家属（工亡）… ],
#   }}}
#
# 内存里仍沿用"本人字段平铺在顶层"的 flat 形态（下游 80+ 处 case_obj.get('name')
# 等读取、提示词填充、模板渲染都不用动），只在读写磁盘的两个函数里做投影：
#   _load_cases_data: 磁盘分块 --unpack_case--> flat
#   _save_cases_data: flat --pack_case--> 磁盘分块
# ============================================================================

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

# 案件数据的磁盘布局：一案一文件，<BASE_PATH>/<案本号>/case.json（与生成的文书同处一个案卷文件夹）
_CASE_FILE_NAME = "case.json"
_LEGACY_CASES_FILE = "cases_data.json"      # 旧版「一个全库文件」，仅迁移时读

# 保存前的备份层
_BACKUP_DIR = "backups"        # 每日快照目录（与案件目录同级）
_SNAPSHOT_KEEP = 30            # 每日快照保留份数


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


# 角色 → 谈话笔录生成配置（提示词 key / 笔录 docx 模板 / 模板占位符数据方法）——单一事实源
ROLE_TALK = {
    '本人': {
        'ai_prompt': 'self_send_to_ai',
        'talk_template': '本人谈话笔录（普通工伤案件）.docx',
        'docx_data': '_build_unified_template_data',
    },
    '证人': {
        'ai_prompt': 'witness_send_to_ai',
        'talk_template': '证人谈话笔录（普通工伤案件）.docx',
        'docx_data': '_build_witness_template_data',
    },
    '法人': {
        'ai_prompt': 'legal_send_to_ai',
        'talk_template': '法人谈话笔录（普通工伤案件）.docx',
        'docx_data': '_build_legal_template_data',
    },
    '家属': {
        'ai_prompt': 'family_send_to_ai',
        'talk_template': '家属谈话笔录（普通工伤案件）.docx',
        'docx_data': '_build_family_template_data',
    },
}


def format_compact_time(value: str) -> str:
    """把受伤/就诊时间的紧凑格式 YYYYMMDDHHMM 变成「2026年07月20日16时20分」。

    长度不是 12 位、或含非数字时原样返回（不猜、不截断）；空值返回空串。
    月/日/时/分补零，与 _resolve_date_input 处理 申请/受理时间 的口径一致。
    """
    s = str(value or '').strip()
    if len(s) != 12 or not s.isdigit():
        return s
    return (f"{s[0:4]}年{s[4:6]}月{s[6:8]}日{s[8:10]}时{s[10:12]}分")


class _PromptUndefined(StrictUndefined):
    """提示词里没拿到值的占位符。

    输出位置（`{{某某}}`）原样渲染成 `{{某某}}`——跟以前一样留在提示词里，并触发
    残留占位符告警，好一眼看出「模板加了占位符但代码没填」。
    但用在 `{% if %}` / `{% for %}` 里会直接报错：条件里把变量名写错，宁可当场失败，
    也不要静默当成空/假、让整块提示词悄悄消失。
    """

    def __str__(self):
        return '{{%s}}' % self._undefined_name


# 提示词模板引擎。trim_blocks + lstrip_blocks：{% if %} 独占一行时连那行一起消失，
# 不做这两项会留下空行。autoescape 关掉（纯文本，不是 HTML）。
_PROMPT_ENV = Environment(
    undefined=_PromptUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    # 用默认的 newline_sequence='\n'：提示词文件虽是 CRLF，但 load_prompt 以文本模式读，
    # CRLF 已被 Python 归一成 LF，渲染结果也就该是 LF（跟改造前 str.replace 一致）。
)


def render_prompt_template(template: str, data: Dict[str, Any], label: str = '') -> str:
    """渲染提示词模板：`{{key}}` 填值，`{% if %}` 按条件决定整块要不要。

    值为空串、且占位符独占一行（形如「- 标签：{{占位符}}」）时，整行删掉——否则会留下
    「- 称谓提示：」这种只有标签、没有内容的空壳行。删行只认原模板的形状（该占位符独占
    一行），与别的 key 取什么值、data 的遍历顺序都无关；行内的占位符为空时只替成空串。

    值为 None 表示「代码没给这个 key 填值」：不喂给引擎，于是它在输出位置保持
    `{{key}}` 原样并告警（而不是渲染成字符串 "None"）。

    渲染后仍有残留 {{…}} 则告警（防止模板加了新占位符而代码未填）。
    """
    # 第一遍：空值占位符独占的整行删掉（在原模板上做，与遍历顺序无关）
    for key, val in data.items():
        if val is None or str(val) != '':
            continue
        token = '{{%s}}' % key
        template = re.sub(r'^[^\n{}]*' + re.escape(token) + r'[ \t]*(?:\r?\n|$)',
                          '', template, flags=re.M)
    # 第二遍：交给模板引擎（None 的键不提供，让它按「未填」处理）
    provided = {k: v for k, v in data.items() if v is not None}
    rendered = _PROMPT_ENV.from_string(template).render(**provided)
    leftovers = sorted(set(re.findall(r'\{\{([^}]*)\}\}', rendered)))
    if leftovers:
        logger.warning(f"⚠️ 提示词「{label}」仍有未替换占位符: {leftovers}")
    return rendered


# ============================================================================
# 拟用条例 选项与格式互转（case_classifier 为单一事实源）
# ============================================================================

# 从条例目录派生：顺序 = 下拉框顺序；要素与 case_classifier 同源
_REGULATION_CATALOG = CaseClassifier.REGULATIONS
REGULATION_OPTIONS = list(_REGULATION_CATALOG.keys())
REGULATION_ELEMENTS = {
    key: list(reg.get('elements', []))
    for key, reg in _REGULATION_CATALOG.items()
}


def _regulation_elements(short: str) -> list:
    """返回拟用条例对应的法律要件列表；未知条例返回空列表"""
    if not short:
        return []
    return list(REGULATION_ELEMENTS.get(short.strip(), []))




# ============================================================================
# 书面证据清单：条例 × 案件性质 × 申请类型 三层叠加
# ----------------------------------------------------------------------------
# 单看条例不够——同一个条例下，工亡案件还要死亡证明，个人申请还要劳动关系的
# 补充证明，工亡由近亲属代为申请还要关系证明。见 compose_evidence()。
# 「是否已提供」由材料面板的复选框承载，不在这里判断。
# ============================================================================

# L1 《条例》第18条要求的通用材料（所有工伤认定申请都要）
_BASE_EVIDENCE_REQUIRED = ("身份证", "劳动合同", "医院诊断证明")

# L3 工亡案件追加
_DEATH_EVIDENCE_REQUIRED = ("死亡证明",)
_DEATH_EVIDENCE_POSSIBLE = ("抢救病历（含抢救时间记录）", "死亡原因证明")

# L4 个人申请追加（单位不配合时劳动关系往往要靠这些佐证；劳动合同与
# 劳动关系裁决书都列出，勾哪个就说明是哪种情况）
_SELF_APPLY_EVIDENCE_POSSIBLE = ("劳动关系裁决书", "单位未在规定时限内申报的证明")

# L2.5 单位性质修饰：机关（公务员）/事业单位 的人员不是劳动合同关系，而是人事
# 关系——「劳动合同」降为可能，换成各自的人事关系证明与在编证明。
_NON_ENTERPRISE_EVIDENCE_REQUIRED = {
    "机关（公务员）": ("公务员录用审批文件", "公务员在编证明"),
    "事业单位": ("聘用合同", "事业单位在编证明"),
}

# L5 个人申请 + 工亡：申请人是近亲属，还要证明「有资格申请」
_KIN_APPLY_EVIDENCE_REQUIRED = ("近亲属关系证明（户口簿/结婚证等）", "申请人身份证")
_KIN_APPLY_EVIDENCE_POSSIBLE = ("其他近亲属授权委托书或放弃声明",)


def compose_evidence(regulation_short: str, is_death_case: bool,
                     is_personal_apply: bool,
                     unit_type: str = DEFAULT_UNIT_TYPE) -> List[Tuple[str, bool]]:
    """合成证据清单，返回 [(材料名称, 是否必要), ...]。

    四个信号：拟用条例 × 单位性质 × 案件性质（工亡）× 申请类型（个人）。
    顺序：必要在前、可能在后；同名只留一条（在两层都出现时按「必要」算）。
    """
    required = list(_BASE_EVIDENCE_REQUIRED)
    possible: List[str] = []

    # 非企业单位：劳动合同降为可能，换成人事关系与在编证明
    unit_extra = _NON_ENTERPRISE_EVIDENCE_REQUIRED.get((unit_type or "").strip())
    if unit_extra:
        required.remove("劳动合同")
        possible.insert(0, "劳动合同")
        required += list(unit_extra)

    reg = _REGULATION_CATALOG.get((regulation_short or "").strip(), {})
    ev = reg.get("evidence") or {}
    required += list(ev.get("required") or [])
    possible += list(ev.get("possible") or [])

    if is_death_case:
        required += list(_DEATH_EVIDENCE_REQUIRED)
        possible += list(_DEATH_EVIDENCE_POSSIBLE)

    if is_personal_apply:
        possible += list(_SELF_APPLY_EVIDENCE_POSSIBLE)
        if is_death_case:                      # 工亡由近亲属代为申请
            required += list(_KIN_APPLY_EVIDENCE_REQUIRED)
            possible += list(_KIN_APPLY_EVIDENCE_POSSIBLE)

    out: List[Tuple[str, bool]] = []
    seen = set()
    for name in required:
        if name and name not in seen:
            seen.add(name)
            out.append((name, True))
    for name in possible:
        if name and name not in seen:
            seen.add(name)
            out.append((name, False))
    return out


_REG_CN_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _cn_num_to_int(text: str):
    """中文数字转整数：一→1，六→6，十四→14，十五→15"""
    if not text:
        return None
    if text in _REG_CN_DIGITS:
        return _REG_CN_DIGITS[text]
    if text.startswith("十"):
        tail = text[1:]
        return 10 + (_REG_CN_DIGITS.get(tail, 0) if tail else 0)
    if "十" in text:
        tens, ones = text.split("十", 1)
        return _REG_CN_DIGITS.get(tens, 0) * 10 + (_REG_CN_DIGITS.get(ones, 0) if ones else 0)
    return None


_INT_TO_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
              6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}


def _int_to_cn_num(n) -> str:
    """整数转中文数字：1→一，6→六，14→十四，15→十五"""
    if not n:
        return ""
    if n <= 10:
        return _INT_TO_CN[n]
    if n < 20:
        tail = n - 10
        return "十" + (_INT_TO_CN[tail] if tail else "")
    tens = n // 10
    ones = n % 10
    return _INT_TO_CN[tens] + "十" + (_INT_TO_CN[ones] if ones else "")


def _regulation_full_to_short(text: str) -> str:
    """《工伤保险条例》第十四条第一款第一项 → 第十四条第（一）项"""
    import re
    if not text:
        return ""
    m = re.search(
        r"第([一二三四五六七八九十]+)条第[一二三四五六七八九十]+款第([一二三四五六七八九十]+)项",
        text,
    )
    if m:
        return f"第{m.group(1)}条第（{m.group(2)}）项"
    return text


def _regulation_short_to_full(short: str) -> str:
    """第十四条第（一）项 → 《工伤保险条例》第十四条第一款第一项"""
    import re
    if not short:
        return ""
    m = re.match(r"^第([一二三四五六七八九十]+)条第（([一二三四五六七八九十]+)）项$", short)
    if m:
        art = _cn_num_to_int(m.group(1))
        item = _cn_num_to_int(m.group(2))
        if art and item:
            return f"《工伤保险条例》第{_int_to_cn_num(art)}条第一款第{_int_to_cn_num(item)}项"
    return short


def _unit_is_non_enterprise(unit_type: str) -> bool:
    """单位性质是否为 机关（公务员）/事业单位（非企业）。"""
    return (unit_type or "").strip() not in ("", DEFAULT_UNIT_TYPE)


def _regulation_full_for_unit(unit_type: str, short: str) -> str:
    """审批表“引用条例”表述：机关/事业单位案件在《工伤保险条例》前加“参照”。"""
    full = _regulation_short_to_full(short or "")
    if not full:
        return full
    return ("参照" + full) if _unit_is_non_enterprise(unit_type) else full


def _person_affiliation(case_obj: dict) -> str:
    """受伤职工“所属表述”：企业→「{单位}职工」；机关（公务员）/事业单位→按身份表述。"""
    ut = str(case_obj.get('unit_type', DEFAULT_UNIT_TYPE) or DEFAULT_UNIT_TYPE)
    unit = str(case_obj.get('labor_unit', '') or '')
    ident = str(case_obj.get('identity', '') or '').strip() or DEFAULT_IDENTITY
    if ut == "机关（公务员）":
        return f"{unit}（机关）{ident}" if unit else f"机关（公务员）{ident}"
    if ut == "事业单位":
        return f"{unit}（事业单位）{ident}" if unit else f"事业单位{ident}"
    return f"{unit}职工" if unit else "用人单位职工"


def _notice_basis_sentence(case_obj: dict, deny: bool = False) -> str:
    """工伤认定告知书的结论依据句（数据驱动；机关/事业单位加“参照”）。"""
    full = _regulation_full_for_unit(
        str(case_obj.get('unit_type', '') or ''),
        str(case_obj.get('proposed_article', '') or ''))
    if not full:
        return ""
    if deny:
        return f"不符合{full}认定工伤之规定，拟不予认定为工伤。"
    return f"符合{full}认定工伤之规定，现拟决定认定为工伤。"


def _filter_provided(materials):
    """只保留「已提供（勾选）」的材料，转成 {name, notes} 格式"""
    if not isinstance(materials, list):
        return []
    return [
        {"name": m.get('name', ''), "notes": m.get('notes', '')}
        for m in materials
        if isinstance(m, dict) and m.get('provided') and m.get('name')
    ]


def _to_full_materials(provided_materials):
    """把 JSON 里的 {name, notes} 材料转回界面用的 {name, provided:True, notes}"""
    if not isinstance(provided_materials, list):
        return []
    return [
        {"name": m.get('name', ''), "provided": True, "notes": m.get('notes', '')}
        for m in provided_materials
        if isinstance(m, dict) and m.get('name')
    ]


# ============================================================================
# F2 测试数据预设（按 F2 轮换）
# ============================================================================

# F2 循环键。目前只留一条（本人·莫言）用于试生成笔录；原先的
# 工亡/证人/法人/机关公务员/事业单位几条预设已按需删除，需要时从 git 历史取回。
TEST_DATA_PRESETS = [{'name': '单位申请×工伤 本人(莫言)',
  'role': '本人',
  'deathCaseCheckbox': False,
  'personalApplicationCheckbox': False,
  'name_pane': '莫言',
  'idnumer_pane': '330324199003151234',
  'textEdit': '浙江省永嘉县瓯北街道XX路88号',
  'lineEdit_4': '13888880001',
  'lineEdit_5': '泥水工',
  'injured_worker': '莫言',
  'regulation': '第十四条第（一）项',
  'company_pane': '温州YY建筑劳务有限公司',
  'construction_company': '永嘉县XX建设工程有限公司',
  'construction_plant': 'ZZ新城项目一期工地',
  'statement_edit': '我单位职工莫言，男，1990年3月15日出生，身份证号330324199003151234。2026年7月20日16时20分许，莫言在工地3号楼5层搬运水泥时被滑落的水泥袋砸伤右脚，诊断为右足跖骨骨折。属工作时间工作场所因工作原因受伤，单位申请认定工伤。',
  'materials': [{'name': '身份证复印件', 'provided': True, 'notes': ''},
                {'name': '医院诊断证明书', 'provided': True, 'notes': '右足跖骨骨折'},
                {'name': '劳动合同', 'provided': True, 'notes': ''},
                {'name': '考勤记录', 'provided': False, 'notes': ''}]}]


# ============================================================================
# AIWorker
# ============================================================================

class AIWorker(QThread):
    """AI工作线程"""
    finished = pyqtSignal(dict)  # 发送完成信号
    error = pyqtSignal(str)  # 发送错误信号
    progress = pyqtSignal(str, int)  # 发送进度信号 (消息, 进度百分比)

    def __init__(self, ai_service, file_path):
        super().__init__()
        self.ai_service = ai_service
        self.file_path = file_path

    def run(self):
        """线程运行的主函数"""
        try:
            # 第一步：提取文本
            self.progress.emit("正在提取文档文本...", 20)
            document_text = self.ai_service.extract_text_from_docx(self.file_path)

            # 第二步：AI分析
            self.progress.emit("正在调用DeepSeek API进行分析...", 50)
            result = self.ai_service.analyze_legal_document(document_text)

            # 第三步：完成
            self.progress.emit("分析完成，正在生成报告...", 90)
            self.finished.emit(result)

        except Exception as e:
            self.error.emit(str(e))

class TranscriptFromTemplateWorker(QThread):
    """把发给 AI 的提示词文本转发给 AIService 生成谈话笔录的后台线程（本人/证人/法人共用）"""
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, ai_service, full_text):
        super().__init__()
        self.ai_service = ai_service
        self.full_text = full_text

    def run(self):
        try:
            result = self.ai_service.generate_transcript_from_text(self.full_text)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

class CaseDataModel:
    """案件数据模型 - 统一管理所有案件数据"""

    def __init__(self):
        self.basic_info: Dict[str, Any] = {}  # 基础个人信息
        self.company_info: Dict[str, Any] = {}  # 公司相关信息
        self.case_info: Dict[str, Any] = {}  # 案件信息
        self.investigation: Dict[str, Any] = {}  # 调查信息
        self.output_config: Dict[str, Any] = {}  # 输出配置
        self.witnesses: List[Dict[str, Any]] = []  # 多证人数据，每项含 序号/姓名/身份证号/身份证地址/手机号/岗位/性别/年龄
        self.current_witness_index: int = -1  # 当前正在编辑的证人下标，-1 表示无
        self._init_default_values()

    def _init_default_values(self):
        """初始化默认值"""
        self.case_info.update({
            '案件性质': '工伤案件',
            '申请类型': '单位申请'
        })
        self.output_config.update({
            '当前日期': _date_now(),
            '当前时间': _time_now()
        })

    def to_template_dict(self) -> Dict[str, Any]:
        """转换为模板渲染用的字典"""
        template_dict = {}

        # 确保日期时间是最新的
        self.output_config.update({
            '当前日期': _date_now(),
            '当前时间': _time_now()
        })

        # 按优先级合并
        template_dict.update(self.basic_info)
        template_dict.update(self.company_info)
        template_dict.update(self.case_info)
        template_dict.update(self.investigation)
        template_dict.update(self.output_config)

        return template_dict

    def update_basic_info(self, role: str, data: Dict[str, Any]):
        """更新基础信息"""
        prefixed_data = {}
        for key, value in data.items():
            if not key.startswith(role):
                new_key = f"{role}{key}" if key != "姓名" else f"{role}姓名"
            else:
                new_key = key
            prefixed_data[new_key] = value

        self.basic_info.update(prefixed_data)

    def clear_role_data(self, role: str):
        """清除特定角色的数据"""
        role_prefix = role if role in ["本人", "证人", "法人", "家属"] else ""
        if not role_prefix:
            return

        keys_to_remove = [
            key for key in self.basic_info.keys()
            if key.startswith(role_prefix)
        ]

        for key in keys_to_remove:
            self.basic_info.pop(key, None)


# ============================================================================
# CaseDataReviewDialog — 数据核对窗口（以 JSON 文本形式显示并可编辑）
# ============================================================================

class CaseDataReviewDialog(QDialog):
    """案件数据核对窗口。

    以 JSON 文本形式展示完整案件数据，用户可直接编辑；
    点击「保存并关闭」时解析 JSON（通过 get_case_obj() 读取），格式错误则提示且不关闭。
    """

    def __init__(self, case_obj: Dict[str, Any], parent=None):
        super().__init__(parent)
        self._case_obj: Optional[Dict[str, Any]] = None
        self._build_ui(case_obj)

    def _build_ui(self, case_obj):
        self.setWindowTitle("🔍 案件数据核对")
        self.resize(720, 800)
        self.setMinimumSize(640, 660)

        root = QVBoxLayout(self)

        title = QLabel("请核对并修改案件数据（JSON 格式），改完后点「保存并关闭」")
        title.setStyleSheet("font-size: 13px; font-weight: bold; padding: 4px;")
        root.addWidget(title)

        self.json_edit = QTextEdit()
        self.json_edit.setFont(QFont("Consolas", 10))
        # 展示与存盘一致的 v3 分块结构（case_info / injured_worker / witnesses / …）
        self.json_edit.setPlainText(json.dumps(pack_case(case_obj), ensure_ascii=False, indent=2))
        root.addWidget(self.json_edit, 1)

        btns = QHBoxLayout()
        btns.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)

        save_btn = QPushButton("保存并关闭")
        save_btn.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; "
            "padding: 6px 24px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #219150; }"
        )
        save_btn.clicked.connect(self._on_save)
        btns.addWidget(save_btn)

        root.addLayout(btns)

    def _on_save(self):
        text = self.json_edit.toPlainText().strip()
        try:
            obj = json.loads(text)
            if not isinstance(obj, dict):
                raise ValueError("JSON 顶层必须是对象 {…}")
            # 分块结构 → 内存 flat（用户把块删了则按原样透传，不阻断）
            self._case_obj = unpack_case(obj)
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "JSON 格式错误", f"无法解析 JSON：\n{str(e)}\n\n请修正后再保存。")

    def get_case_obj(self) -> Optional[Dict[str, Any]]:
        return self._case_obj


class ApprovalDecisionDialog(QDialog):
    """案件审批表 AI 分析结果对话框：认定工伤 / 不予认定工伤 / 保存"""

    def __init__(self, analysis: dict, parent=None):
        super().__init__(parent)
        self.choice = "保存"
        self._build_ui(analysis)

    def _build_ui(self, analysis: dict):
        self.setWindowTitle("🔍 AI 分析结果")
        self.resize(760, 560)
        self.setMinimumSize(640, 480)

        layout = QVBoxLayout(self)

        bias = analysis.get("偏向", "")
        bias_label = QLabel(f"AI 倾向：{bias}" if bias else "AI 倾向：未知")
        bias_label.setStyleSheet("font-size: 15px; font-weight: bold; padding: 4px;")
        layout.addWidget(bias_label)

        # 页签：综合分析 / 不予认定理由 / 诊断结论
        tab_widget = QTabWidget()

        tab1 = QWidget()
        t1 = QVBoxLayout(tab1)
        analysis_edit = QTextEdit()
        analysis_edit.setReadOnly(True)
        analysis_edit.setPlainText(analysis.get("分析", ""))
        t1.addWidget(analysis_edit)
        tab_widget.addTab(tab1, "综合分析")

        tab2 = QWidget()
        t2 = QVBoxLayout(tab2)
        reason_edit = QTextEdit()
        reason_edit.setReadOnly(True)
        reasons = analysis.get("关键理由", []) or []
        reason_edit.setPlainText("\n".join(f"• {r}" for r in reasons))
        t2.addWidget(reason_edit)
        tab_widget.addTab(tab2, "不予认定理由")

        tab3 = QWidget()
        t3 = QVBoxLayout(tab3)
        diag_edit = QTextEdit()
        diag_edit.setReadOnly(True)
        diag_edit.setPlainText(analysis.get("诊断结论", ""))
        t3.addWidget(diag_edit)
        tab_widget.addTab(tab3, "诊断结论")

        layout.addWidget(tab_widget, 1)

        # 三个按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        save_btn = QPushButton("保存")
        save_btn.clicked.connect(lambda: self._done("保存"))
        btn_row.addWidget(save_btn)

        no_btn = QPushButton("不予认定工伤")
        no_btn.setStyleSheet(
            "QPushButton { background-color: #e74c3c; color: white; font-weight: bold; "
            "padding: 6px 20px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #c0392b; }"
        )
        no_btn.clicked.connect(lambda: self._done("不予认定"))
        btn_row.addWidget(no_btn)

        yes_btn = QPushButton("认定工伤")
        yes_btn.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; "
            "padding: 6px 20px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #219150; }"
        )
        yes_btn.clicked.connect(lambda: self._done("认定"))
        btn_row.addWidget(yes_btn)

        layout.addLayout(btn_row)

    def _done(self, choice: str):
        self.choice = choice
        self.accept()

    def get_choice(self) -> str:
        return self.choice


class MainWindow(MainWindowUI):
    """主界面业务逻辑。

    界面构建（建控件 + 用布局管理器摆位置）在 ui_main_build.MainWindowUI，
    这里只剩业务；信号统一在 _connect_signals() 里接。
    """

    def __init__(self, parent=None, *args, **kwargs):
        # 建界面 + 用布局管理器摆好位置（见 ui_main_build.MainWindowUI）
        super().__init__(parent, *args, **kwargs)

        # .ui 里是驼峰名，这里补两个下划线别名给业务代码用
        self.death_case_checkbox = self.findChild(QCheckBox, "deathCaseCheckbox")
        self.personal_application_checkbox = self.findChild(QCheckBox, "personalApplicationCheckbox")

        # 信号统一在这里接（老代码散在 __init__ 内联、_setup_api_config_ui、
        # _setup_witness_ui、_setup_radio_connections、_install_todo_kanban 五处）。
        # 必须早接：api_user_combo、company_pane 那几条会在下面的初始化过程中
        # 就触发一次，晚接会漏掉这次触发。
        self._connect_signals()

        self._test_data_index = -1  # F2 测试数据轮换索引
        self.current_case_id = ""  # 当前案件案本号（跨角色/跨步骤保持同案关联）

        # lineEdit_2 改为案本号显示
        self.label_10.setText("案本号：")
        self.lineEdit_2.setPlaceholderText("输入本人姓名后自动生成")

        # 创建用户管理器
        self.user_manager = UserManager()
        self._load_saved_user_config()

        # 第一步：统一设置所有路径（必须在所有服务初始化之前）
        print("=" * 50)
        print("🚀 开始初始化 MainWindow")
        print("=" * 50)

        self._setup_paths()  # 统一使用 path_utils 设置路径

        # 第二步：初始化配置服务（但禁用其路径管理功能）
        self.config_service = ConfigService()
        # 禁用config_service的路径检查，避免干扰
        self.config_service.set('system.startup_check_disk_space', False)
        self.config_service.set('template.base_path', self.TEMPLATE_PATH)

        # 第三步：初始化组合框数据（必须在路径设置之后）
        self.init_combobox_data()

        # 第四步：初始化其他核心服务（使用正确的路径）
        self.file_service = FileService(self.BASE_PATH)
        self.data_service = DataService()

        # 第五步：更新所有服务的路径
        self._update_services_paths()

        # 第六步：初始化数据模型
        self.data_model = CaseDataModel()
        self.var_manager = TemplateVariableManager(self.data_model)
        self.data_model.output_config['用户名'] = self._get_current_username()

        # 简化日志系统
        self.setup_logging()

        # 保持向后兼容
        self._template_dict = self.data_model.to_template_dict()
        self.current_case_folder = None
        self.current_person_name = ""

        # 初始化数据
        case_config = self.config_service.get_case_config()
        self.set_data('案件性质', case_config.default_case_type, 'case')
        self.set_data('申请类型', case_config.default_application_type, 'case')

        # 初始化案件类型下拉框（全部条例情形，来自 case_classifier）
        for _short in REGULATION_OPTIONS:
            self.comboBox.addItem(_regulation_short_to_full(_short))

        # 初始化组合框（必须在 init_combobox_data 之后）
        self.init_comboboxes()

        # 应用UI设置
        self._apply_ui_settings()

        # 初始化AI服务（使用传入的api_config）
        self.ai_service = None  # 先初始化为None
        self.init_ai_service()

        # 初始状态设为可用
        self.pushButton.setEnabled(True)

        # 单位性质 / 身份 的选项与文案。界面构建类不引用本文件的常量，故在这里补；
        # 必须等数据模型建好——填入选项会触发一次证据清单重算。
        self.unit_type_combo.addItems(UNIT_TYPES)
        self.unit_type_combo.setCurrentText(DEFAULT_UNIT_TYPE)
        self.identity_label.setText(_ROLE_IDENTITY_LABEL["本人"])
        self.identity_edit.setPlaceholderText(DEFAULT_IDENTITY)

        # 验证模板路径（使用已获取的路径）
        if os.path.exists(self.TEMPLATE_PATH):
            print(f"✅ 模板路径可访问: {self.TEMPLATE_PATH}")
            print(f"✅ 谈话模板路径: {self.TALK_TEMPLATE_PATH}")
            print(f"✅ 文书模板路径: {self.DOCUMENT_TEMPLATE_PATH}")
        else:
            logger.error(f"❌ 模板路径不存在: {self.TEMPLATE_PATH}")

        self._install_todo_kanban()  # 待办事项看板（含 60 分钟定时刷新）

        # 证据清单：启动时先按当前条例/案件性质/申请类型列一遍，
        # 之后三者任一变化都跟着重算（那几个信号已在 _connect_signals() 接好）
        self._refresh_evidence_list()

        print("=" * 50)
        print("🎉 MainWindow 初始化完成")
        print("=" * 50)

    def _connect_signals(self):
        """所有信号连接集中在这里。

        `ui_main_window.Ui_Form.setupUi()` 自己接过一批（身份证号 editingFinished、
        身份证导入、三个保存按钮、四个角色单选、谈话通知书按钮），那份是生成代码，
        本次不动；这里只接原先散在 MainWindow 各处的那些。
        """
        # —— 共享人字段表单 ——
        self.name_pane.editingFinished.connect(self._on_name_pane_changed)

        # —— 单位 / 工地（三个可编辑下拉）——
        self.company_pane.currentTextChanged.connect(self.company)
        self.construction_company.currentTextChanged.connect(self.sync_employer_to_dict)
        self.construction_plant.currentTextChanged.connect(self.c_plant)

        # —— 案件类型与条例 ——
        self.death_case_checkbox.stateChanged.connect(self.on_case_type_changed)
        self.personal_application_checkbox.stateChanged.connect(self.on_case_type_changed)
        self.comboBox.currentIndexChanged.connect(self._refresh_evidence_list)
        self.death_case_checkbox.stateChanged.connect(self._refresh_evidence_list)
        self.personal_application_checkbox.stateChanged.connect(self._refresh_evidence_list)
        self.unit_type_combo.currentTextChanged.connect(self._refresh_evidence_list)

        # —— 四个时间字段 ——
        for edit in (self.apply_time_edit, self.accept_time_edit,
                     self.injury_time_edit, self.visit_time_edit):
            edit.editingFinished.connect(self._save_date_inputs)

        # —— 证人编号 ——
        self.witness_combo.currentIndexChanged.connect(self._on_witness_selected)
        self.add_witness_btn.clicked.connect(self._add_witness)

        # —— 顶栏与两个浮层 ——
        self.config_toggle_btn.clicked.connect(self._toggle_config_panel)
        self.todo_btn.clicked.connect(self._toggle_todo_panel)
        self.todo_board.taskClicked.connect(self._on_board_task_click)
        self.api_user_combo.currentTextChanged.connect(self._on_user_combo_changed)
        self.api_key_input.editingFinished.connect(self._on_api_edited)
        try:
            self.api_user_combo.lineEdit().editingFinished.connect(self._on_api_edited)
        except Exception:
            pass

        # —— 主操作按钮 ——
        self.pushButton_6.clicked.connect(self.smart_search_cases)
        self.pushButton_7.clicked.connect(self.generate_injury_notice)
        self.pushButton_11.clicked.connect(self.approve)   # 原 _reconnect_approval_button()
        self.pushButton_ai_review.clicked.connect(self.ai_review_document)
        # 下面两条在 .ui 里也各接过一次，**必须先断开再接**，否则同一个槽会挂两遍、
        # 点一次跑两次（谈话通知书原先就是这样）。
        for btn, slot in ((self.pushButton_12, self.on_pushButton_12_clicked),
                          (self.pushButton, self.on_talk_button_clicked)):
            try:
                btn.clicked.disconnect()
            except TypeError:
                pass          # 没有旧连接时 PyQt 抛 TypeError，忽略
            btn.clicked.connect(slot)

        # —— 右栏两个面板里的按钮（原先都是匿名控件 + lambda）——
        self.stmt_copy_btn.clicked.connect(self._copy_statement)
        self.stmt_clear_btn.clicked.connect(self.statement_edit.clear)
        self.mat_copy_btn.clicked.connect(self._copy_material)
        self.mat_clear_btn.clicked.connect(self.material_list.clear)
        self.mat_add_btn.clicked.connect(self.material_list.add_row)

    def on_talk_button_clicked(self):
        """谈话笔录按钮点击事件处理 — 数据核对 + 按角色生成笔录"""
        try:
            # 证人/法人/家属：核对前先把表单里新录的数据写回数据模型（open_data_review 保存后会把表单回填成本人数据）
            current_role = self.get_current_role_type()
            if current_role == "证人":
                self._sync_form_to_current_witness()
            elif current_role == "法人":
                self.update_role_info('法人')
            elif current_role == "家属":
                self.update_role_info('家属')

            # 工亡案件：职工本人已故，不能制作本人谈话笔录 → 提示改用 家属/证人/法人
            if current_role == "本人" and self.death_case_checkbox.isChecked():
                QMessageBox.warning(
                    self, "提示",
                    "工亡案件职工本人已故，无法制作本人谈话笔录。\n请改选『家属』（或证人/法人）作为被谈话人。"
                )
                return

            # 本人：已生成过笔录就先问要不要覆盖。
            # 放在数据核对之前——open_data_review 自己会把案件数据写回磁盘，先核对再问的话，
            # 点「否」也白跑了一轮核对与保存。第一次点按钮时案本号可能还没生成，那时也不会有
            # 旧笔录（_main_transcript_files 拿不到目录就返回空），自然放行。
            if current_role == "本人":
                case_id = self.current_case_id or self.lineEdit_2.text().strip()
                if not self._confirm_overwrite_main_transcript(case_id):
                    self._set_status('已取消', 'black')
                    return

            # ── 第一步：弹出数据核对窗口，逐项核对并允许修改 ──
            if not self.open_data_review():
                self._set_status('已取消', 'black')
                return

            # ── 第二步：按角色生成（同一套代码路径，仅提示词/模板按角色区分）──
            role = self.get_current_role_type()
            if role == "证人":
                # 数据核对会把表单回填成本人数据，这里把表单切回当前证人显示
                self._sync_current_witness_to_form()
                # 证人：直接生成证人谈话笔录
                self._generate_role_transcript('证人')
            elif role == "法人":
                # 数据核对会把表单回填成本人数据，这里把表单切回法人显示
                self._sync_legal_to_form()
                # 法人：直接生成法人谈话笔录
                self._generate_role_transcript('法人')
            elif role == "家属":
                # 数据核对会把表单回填成本人数据，这里把表单切回家属显示
                self._sync_family_to_form()
                # 家属：直接生成家属谈话笔录（工亡案）
                self._generate_role_transcript('家属')
            else:
                # 本人：与其他角色同一条路——直接拼提示词生成（不再先跑一次条例判断）
                self._generate_role_transcript('本人')
        except Exception as e:
            print(f"谈话笔录按钮点击异常: {e}")
            import traceback
            traceback.print_exc()

    # ========================================================================
    # 数据核对（第一步）相关方法
    # ========================================================================

    def open_data_review(self) -> bool:
        """弹出案件数据核对窗口（JSON 文本形式）。

        点击时先自动生成案本号填入 case_id；用户可编辑 JSON；
        保存时解析、落盘并回写主界面。
        """
        data, materials, _, _ = self._collect_review_data()

        # ── 点击即自动生成案本号，填入 case_id ──
        case_id = str(data.get('case_id', '')).strip()
        if not case_id:
            case_id = self._auto_generate_case_number(
                data.get('name', ''), data.get('id_card', '')
            )
            data['case_id'] = case_id
        self.current_case_id = case_id

        case_obj = self._build_case_object(data, materials)

        dlg = CaseDataReviewDialog(case_obj, self)
        if dlg.exec_() != QDialog.Accepted:
            self._set_status('已取消数据核对', 'black')
            return False

        case_obj = dlg.get_case_obj()
        if not case_obj:
            return False

        # 以最终 JSON 里的 case_id 为准（用户可能在文本框里改过）
        case_id = str(case_obj.get('case_id', '')).strip() or case_id
        case_obj['case_id'] = case_id
        self.current_case_id = case_id

        # ── 回写主界面与数据模型 ──
        self._apply_case_object(case_obj)

        # ── 保存到 cases_data.json ──
        cases = self._load_cases_data()
        old = cases.get(case_id)
        if old:
            # 整体覆盖前保留既有扩展字段（service_flow/folder_name/transcript_file/
            # analysis_result/conclusion 等），避免重复核对保存时丢失
            for k, v in old.items():
                case_obj.setdefault(k, v)
        cases[case_id] = case_obj
        self._save_cases_data(cases)

        self._set_status(f'数据已核对并保存（案本号：{case_id}）', 'green')
        print(f"✅ 数据核对完成并保存到 cases_data.json（案本号：{case_id}）")
        return True

    def _collect_review_data(self):
        """从主界面与数据模型收集当前案件的全部字段（供核对窗口展示）"""
        # 本人字段以数据模型优先，避免证人/法人切换后 name_pane 串数据
        regulation_full = self.comboBox.currentText().strip()
        # 「拟用条例」例外：以下拉框为准。下拉框的改动不写回数据模型，数据模型里可能是
        # 旧值——曾经因此「改了下拉框却按旧条例保存并生成」，下拉框还会被刷回旧值。
        regulation_short = (_regulation_full_to_short(regulation_full)
                            or self.get_data('拟用条例', ''))

        # 本人单位＝案件级用人单位。仅当前角色是「本人」时才采信「用人单位」控件里
        # 尚未保存的手输值——其余角色下该控件代表的是那个人自己的工作单位。
        self_unit = self.get_data('用人单位', '') or (
            self.company_pane.currentText().strip()
            if self.get_current_role_type() == '本人' else '')

        data = {
            'case_id': self.lineEdit_2.text().strip() or self.get_data('案本号', '') or self.current_case_id,
            'case_nature': '工亡案件' if self.death_case_checkbox.isChecked() else '工伤案件',
            'applicant_type': '个人申请' if self.personal_application_checkbox.isChecked() else '单位申请',
            'regulation': regulation_short,
            'apply_time': self._resolve_date_input(self.apply_time_edit.text()) if hasattr(self, 'apply_time_edit') else '',
            'accept_time': self._resolve_date_input(self.accept_time_edit.text()) if hasattr(self, 'accept_time_edit') else '',
            'visit_time': self._normalize_compact_time(self.visit_time_edit.text()) if hasattr(self, 'visit_time_edit') else '',
            'injury_time': self._normalize_compact_time(self.injury_time_edit.text()) if hasattr(self, 'injury_time_edit') else '',
            'name': self.get_data('本人姓名', '') or self.name_pane.text().strip(),
            'gender': self.get_data('本人性别', '') or self.lineEdit.text().strip(),
            'age': str(self.get_data('本人年龄', '') or self.age_pane.text().strip()),
            'id_card': self.get_data('本人身份证号', '') or self.idnumer_pane.text().strip(),
            'phone': self.get_data('本人手机号', '') or self.lineEdit_4.text().strip(),
            'address': self.get_data('本人身份证地址', '') or self.textEdit.toPlainText().strip(),
            'position': self.get_data('本人岗位', '') or self.lineEdit_5.text().strip(),
            'identity': (self.get_data('本人身份', '')
                         or (self.identity_edit.text().strip() if hasattr(self, 'identity_edit') else '')
                         or DEFAULT_IDENTITY),
            'unit_type': (self.unit_type_combo.currentText().strip() if hasattr(self, 'unit_type_combo')
                          else '') or DEFAULT_UNIT_TYPE,
            'employer': self.get_data('用工单位', '') or self.construction_company.currentText().strip(),
            'labor_unit': self_unit,
            'unit': self_unit,
            'site': self.get_data('工地名称', '') or self.construction_plant.currentText().strip(),
            # 「案件申请陈述」输入框已停用，受伤经过改从数据模型取——
            # 界面上那个框填什么都不再影响文书（值仍由 _apply_case_object 写入）
            'injury_desc': self.get_data('受伤经过', ''),
        }
        materials = (self.material_list.get_materials() if hasattr(self, 'material_list') else []) \
            or self.data_model.investigation.get('本人材料', [])
        witnesses = list(self.data_model.witnesses)
        legal_reps = self._collect_legal_reps()
        return data, materials, witnesses, legal_reps

    def _collect_legal_reps(self) -> List[Dict[str, Any]]:
        """收集法人信息（当前系统法人是单条，统一为规范人记录；兼容未来多条）"""
        person = self._person_from_flat('法人')
        if not person.get('name'):
            return []
        person['role'] = '法人'
        person['materials'] = self.data_model.investigation.get('法人材料', [])
        return [person]

    def _collect_family_reps(self) -> List[Dict[str, Any]]:
        """收集家属（近亲属）信息（工亡案单条，统一为规范人记录）"""
        person = self._person_from_flat('家属')
        if not person.get('name'):
            return []
        person['role'] = '家属'
        return [person]

    def _set_combo_or_type(self, combobox, text):
        """设置下拉框的值：存在则选中，否则输入（可编辑）或新增项（不可编辑）"""
        text = text or ""
        if not text:
            combobox.setCurrentIndex(-1)
            return
        idx = combobox.findText(text)
        if idx >= 0:
            combobox.setCurrentIndex(idx)
        elif combobox.isEditable():
            combobox.setEditText(text)
        else:
            combobox.addItem(text)
            combobox.setCurrentIndex(combobox.count() - 1)

    def _apply_regulation(self, short: str):
        """把「拟用条例」写回下拉框与数据模型

        下拉框**总是**跟着走（包括清空）：它才是这一个字段的权威来源。原先只有
        非空时才刷下拉框，于是加载一个没填条例的案子时，框里会留着上一个案子的条例。
        """
        short = (short or "").strip()
        self.set_data('拟用条例', short, 'case')
        full = _regulation_short_to_full(short)
        self.set_data('引用条例', full, 'case')
        self._set_combo_or_type(self.comboBox, full)

    def _apply_case_object(self, case_obj: Dict[str, Any]):
        """把核对后的案件 JSON 对象回写到主界面控件与数据模型"""
        # 案件基本信息
        self.lineEdit_2.setText(str(case_obj.get('case_id', '')))
        self.set_data('案本号', case_obj.get('case_id', ''), 'case')

        self.death_case_checkbox.setChecked(case_obj.get('case_nature', '') == '工亡案件')
        self.personal_application_checkbox.setChecked(case_obj.get('applicant_type', '') == '个人申请')
        self.on_case_type_changed()

        # 单位性质（案件级）
        if hasattr(self, 'unit_type_combo'):
            ut = str(case_obj.get('unit_type', DEFAULT_UNIT_TYPE)) or DEFAULT_UNIT_TYPE
            self._set_combo_or_type(self.unit_type_combo, ut)
            self.set_data('单位性质', ut, 'case')

        self._apply_regulation(case_obj.get('proposed_article', ''))

        if hasattr(self, 'apply_time_edit'):
            self.apply_time_edit.setText(str(case_obj.get('apply_time', '')))
        if hasattr(self, 'accept_time_edit'):
            self.accept_time_edit.setText(str(case_obj.get('accept_time', '')))
        if hasattr(self, 'injury_time_edit'):
            self.injury_time_edit.setText(str(case_obj.get('injury_time', '')))
        if hasattr(self, 'visit_time_edit'):
            self.visit_time_edit.setText(str(case_obj.get('visit_time', '')))
        self._save_date_inputs()

        # 本人信息
        self.name_pane.setText(str(case_obj.get('name', '')))
        self.set_data('本人姓名', case_obj.get('name', ''), 'basic')
        self.lineEdit.setText(str(case_obj.get('gender', '')))
        self.set_data('本人性别', case_obj.get('gender', ''), 'basic')
        self.age_pane.setText(str(case_obj.get('age', '')))
        self.set_data('本人年龄', case_obj.get('age', ''), 'basic')
        self.idnumer_pane.setText(str(case_obj.get('id_card', '')))
        self.set_data('本人身份证号', case_obj.get('id_card', ''), 'basic')
        self.lineEdit_4.setText(str(case_obj.get('phone', '')))
        self.set_data('本人手机号', case_obj.get('phone', ''), 'basic')
        self.lineEdit_5.setText(str(case_obj.get('position', '')))
        self.set_data('本人岗位', case_obj.get('position', ''), 'basic')
        if hasattr(self, 'identity_edit'):
            self.identity_edit.setText(str(case_obj.get('identity', DEFAULT_IDENTITY)) or DEFAULT_IDENTITY)
        self.set_data('本人身份', str(case_obj.get('identity', DEFAULT_IDENTITY)) or DEFAULT_IDENTITY, 'basic')
        self.set_data('本人单位名称', case_obj.get('unit', ''), 'basic')
        self.textEdit.setPlainText(str(case_obj.get('address', '')))
        self.set_data('本人身份证地址', case_obj.get('address', ''), 'basic')

        # 单位信息（company_pane=用人单位，construction_company=用工单位）
        # 只写数据模型，不直接推控件：company_pane 是共享控件，其 currentTextChanged
        # 会把值写进*当前角色*的 unit 槽，非本人角色下推案件级值会污染该人的单位。
        # 控件由方法末尾的 _restore_role_unit() 按角色回填。
        self.set_data('用人单位', case_obj.get('labor_unit', ''), 'company')
        self._set_combo_or_type(self.construction_company, case_obj.get('employer', ''))
        self.set_data('用工单位', case_obj.get('employer', ''), 'company')
        self._set_combo_or_type(self.construction_plant, case_obj.get('site', ''))
        self.set_data('工地名称', case_obj.get('site', ''), 'company')

        # 受伤经过：只写数据模型，不再回填到「案件申请陈述」框
        # （该框已停用，回填会让用户以为改了有效——见 ui_main_build 里
        #  _create_right_panel_widgets 的说明）
        self.set_data('受伤经过', case_obj.get('injury_description', ''), 'investigation')
        if hasattr(self, 'statement_edit'):
            self.statement_edit.clear()   # 清掉残留，免得看着像本案陈述

        # 材料清单（本人）—— JSON 里只存了「已提供」，转回界面格式
        materials_full = _to_full_materials(case_obj.get('materials', []))
        if hasattr(self, 'material_list'):
            self.material_list.set_materials(materials_full)
            # 载入案件自带材料后，再补上按条例/性质应备而清单里没有的
            self._refresh_evidence_list()
        self.data_model.investigation['本人材料'] = materials_full

        # 法人信息回写（统一规范人记录 → 法人* 扁平兼容键，含性别/年龄）
        legal_reps = case_obj.get('legal_reps', [])
        if legal_reps:
            lr = legal_reps[0]
            for field in PERSON_BASE_FIELDS:
                val = lr.get(field)
                if val:
                    self.set_data(person_flat_key('法人', field), val, 'basic')
            self.data_model.investigation['法人材料'] = lr.get('materials', [])

        # 家属信息回写（工亡案单条 → 家属* 扁平兼容键，含 与死者关系/单位/岗位）
        family_reps = case_obj.get('family_reps', [])
        if family_reps:
            fr = family_reps[0]
            for field in PERSON_BASE_FIELDS:
                val = fr.get(field)
                if val:
                    self.set_data(person_flat_key('家属', field), val, 'basic')

        # 上面把「用人单位」写成了案件级值；若当前不在本人角色，要换回该角色自己的单位
        self._restore_role_unit()

        # 刷新模板字典缓存
        self._template_dict = self.data_model.to_template_dict()
        print("✅ 核对数据已回写主界面与数据模型")

    # ========================================================================
    # 案件 JSON 持久化（案本号为键）
    # ========================================================================

    # ---- 案件数据的磁盘布局：一案一文件 ----
    # 每个案件的数据是 <BASE_PATH>/<案本号>/case.json，与它生成的文书同处一个案卷文件夹，
    # 而不是全部挤在一个 cases_data.json 里。好处：某个案件的文件坏了只影响它自己；
    # 数据跟着案卷走，删/拷一个案卷就是删/拷一个案件。

    def _legacy_cases_path(self) -> str:
        """旧版「一个全库文件」的路径（只在迁移时用到）"""
        return os.path.join(self.BASE_PATH, _LEGACY_CASES_FILE)

    @staticmethod
    def _safe_case_dirname(case_id: str) -> str:
        """案本号 → 可当 Windows 目录名：换掉非法字符、去掉首尾的点和空格；空了给个占位名"""
        name = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', str(case_id or '')).strip(' .')
        return name or '未命名案件'

    @staticmethod
    def _year_for_case(case_id: str) -> str:
        """归档年份：优先取案本号里的立案日期（那本来就是立案时的系统时间），
        取不到再用当前系统年份。"""
        text = str(case_id or '')
        m = (re.search(r'(?:案本|工亡)(20\d{2})\d{4}', text)
             or re.search(r'(20\d{2})\d{2}\d{2}', text))
        return m.group(1) if m else str(datetime.datetime.now().year)

    def _locate_case_dir(self, case_id: str) -> str:
        """找已存在的案卷目录，找不到返回空串。

        两种布局都认：<BASE>/<年份>/<案本号>（分年份之后）与 <BASE>/<案本号>
        （分年份之前存的）。老案卷命中后**留在原地**——搬它就会把同处一处的
        文书和数据分开，也会让用户以为文件丢了。
        """
        name = self._safe_case_dirname(case_id)
        plain = os.path.join(self.BASE_PATH, name)
        if os.path.isdir(plain):
            return plain
        try:
            for entry in sorted(os.listdir(self.BASE_PATH)):
                cand = os.path.join(self.BASE_PATH, entry, name)
                if os.path.isdir(cand):
                    return cand
        except OSError:
            pass
        return ''

    def _case_dir(self, case_id: str) -> str:
        """某个案件的案卷目录（案件数据与它生成的文书同处一处）

        已存在的按原位返回；新案件落到 <BASE_PATH>/<年份>/<案本号>/。年份只在
        第一次落盘时定下（写在目录名上），之后靠"找得到就用原来的"保证不会被搬走。
        """
        found = self._locate_case_dir(case_id)
        if found:
            return found
        return os.path.join(self.BASE_PATH, self._year_for_case(case_id),
                            self._safe_case_dirname(case_id))

    def _case_file(self, case_id: str) -> str:
        """某个案件的数据文件"""
        return os.path.join(self._case_dir(case_id), _CASE_FILE_NAME)

    def _iter_case_files(self):
        """遍历现有案件数据文件，产出 (案本号, 文件路径)

        两种布局都认：<BASE>/<年份>/<案本号>/case.json 与 <BASE>/<案本号>/case.json。
        案本号是定位依据——新布局里它是年份目录的下一层，老布局里就是第一层。
        """
        try:
            entries = sorted(os.listdir(self.BASE_PATH))
        except OSError:
            return
        for entry in entries:
            top = os.path.join(self.BASE_PATH, entry)
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

    def _load_cases_data(self) -> Dict[str, Any]:
        """加载全部案件，返回 {case_id: case_obj}

        单个案件的文件读不出来，只跳过它自己并记 ERROR，不影响其它案件——这正是从
        「一个全库文件」改成一案一文件要买的东西。版本高于本程序时仍照旧读出来，只记 ERROR。
        """
        self._migrate_legacy_cases_file()
        out: Dict[str, Any] = {}
        for dirname, path in self._iter_case_files():
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

    @staticmethod
    def _peek_version(path: str) -> str:
        """只读文件开头取 version，避免为一行版本号解析整份案卷"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                head = f.read(256)
            m = re.search(r'"version"\s*:\s*"([^"]*)"', head)
            return m.group(1) if m else ''
        except Exception:
            return ''

    def _write_case_file(self, case_id: str, block: Dict[str, Any]) -> str:
        """原子写单个案件文件；写前把旧内容转存 .bak（每次刷新 = 「撤销上一次保存」）

        直接以 'w' 打开会立刻截断，写到一半崩溃或磁盘写满，这个案件就没了，
        所以先写 .tmp 再 os.replace。
        """
        path = self._case_file(case_id)
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

    def _drop_case_files_not_in(self, keep_ids) -> None:
        """删掉「磁盘上有、这次却没保存」的案件数据文件（维持「写全量」的语义）

        只删 case.json / case.json.bak，**不动案卷里的文书**——文书是办案成果，
        不该因为数据里没有这个案子就被清掉。
        """
        keep = {self._safe_case_dirname(cid) for cid in keep_ids}
        for dirname, path in list(self._iter_case_files()):
            if dirname in keep:
                continue
            for p in (path, path + '.bak'):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                        logger.info("🗑️ 已删除不再存在的案件数据: %s", p)
                except Exception as e:
                    logger.warning("⚠️ 删除案件数据失败 %s: %s", p, e)

    def _daily_snapshot(self, packed: Dict[str, Any]) -> None:
        """当天第一份快照（仍是「一份全库」，与改造前同名同语义）；顺带清掉过老的快照"""
        backup_dir = os.path.join(self.BASE_PATH, _BACKUP_DIR)
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

    def _save_cases_data(self, cases: Dict[str, Any]) -> bool:
        """保存全部案件（一案一文件；内存 flat → 磁盘分块 v3）

        每个文件各自原子写：某个案件写失败只影响它自己，不会连带丢掉别的案件。代价是
        N 个文件做不到「跨案件全有全无」——中途失败会留下部分已写，下次保存会再刷一遍
        （幂等），所以这里只把失败如实返回 False。
        传入的字典是「全部真相」：磁盘上有、字典里没有的案件数据文件会被删掉（只删数据，不动文书）。
        """
        try:
            os.makedirs(self.BASE_PATH, exist_ok=True)
            packed = {cid: pack_case(c) for cid, c in cases.items()}
            for cid, blk in packed.items():
                self._write_case_file(cid, blk)
            self._drop_case_files_not_in(packed)
            self._daily_snapshot(packed)
            print(f"✅ 案件数据已保存: {len(packed)} 个案件（一案一文件）")
            return True
        except Exception as e:
            logger.error("❌ 保存案件数据失败: %s", e)
            return False

    def _migrate_legacy_cases_file(self) -> None:
        """把旧的「一个全库文件」拆成一案一文件（幂等、可回退）

        - 触发：<BASE_PATH>/cases_data.json 还在（迁移成功后它被改名 .migrated，不再触发）
        - 原文件只改名不删除：cases_data.json → cases_data.json.migrated，便于切回旧版程序
        - 版本非当前时另留一份 cases_data.json.v2.bak（原样备份，旧版程序能读）
        - 已存在的案件文件不覆盖：中途失败下次启动接着补，不会把新数据盖掉
        - 任何失败都不动原文件，下次启动重试
        """
        legacy = self._legacy_cases_path()
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
                if os.path.exists(self._case_file(cid)):
                    skipped += 1
                    continue
                flat = migrate_case(unpack_case(blk))
                flat['case_id'] = cid          # 旧文件里案件字典的键就是案本号
                self._write_case_file(cid, pack_case(flat))
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

    def _update_case_field(self, case_number: str, **fields) -> bool:
        """把字段写回 cases_data.json 的指定案件"""
        try:
            cases = self._load_cases_data()
            case_obj = cases.get(case_number)
            if case_obj is None:
                return False
            case_obj.update(fields)
            self._save_cases_data(cases)
            return True
        except Exception as e:
            logger.warning(f"⚠️ 更新案件字段失败（非致命）: {e}")
            return False

    def _refresh_evidence_list(self, *_ignored):
        """按当前的 拟用条例 / 工亡案件 / 个人案件 重算材料清单。

        下拉框与两个复选框任一变化都会重算（都接到了这里，故用 *_ignored 吞掉
        Qt 传来的 index/state 参数）。

        案件自带和手工添加的材料不受影响，同名项也不会重复
        （见 MaterialListWidget.apply_evidence_list）。
        """
        if not hasattr(self, "material_list"):
            return
        # 和 _collect_review_data 同一口径：以下拉框为准，数据模型里的可能是旧值
        short = (_regulation_full_to_short(self.comboBox.currentText().strip())
                 or self.get_data('拟用条例', ''))
        unit_type = (self.unit_type_combo.currentText().strip()
                     if hasattr(self, 'unit_type_combo') else DEFAULT_UNIT_TYPE)
        items = compose_evidence(short,
                                 self.death_case_checkbox.isChecked(),
                                 self.personal_application_checkbox.isChecked(),
                                 unit_type)
        self.material_list.apply_evidence_list(items)

    # ========================================================================
    # 待办事项看板：文书送达流程（个人申请工伤案）
    # ========================================================================

    def _install_todo_kanban(self):
        """待办看板的定时刷新：每小时按“今天”重算各案件节点/剩余天数。

        按钮与看板控件本身（todo_btn / todo_board）由 MainWindowUI 建好并摆位，
        它们的信号在 _connect_signals() 里接；这里只管定时器与首次刷新。
        """
        self._todo_open = False
        # 定时刷新（任务文字/倒计时/到期流转）—— 每小时一次
        self._todo_timer = QTimer(self)
        self._todo_timer.setInterval(60 * 60 * 1000)
        self._todo_timer.timeout.connect(self._refresh_todo_board)
        self._todo_timer.start()
        self._refresh_todo_board()

    def _toggle_todo_panel(self):
        """待办事项菜单按钮：展开/收起下拉看板。"""
        self._todo_open = not self._todo_open
        self._refresh_todo_board()  # 展开前确保任务/计数最新
        self.todo_board.setVisible(self._todo_open)
        if self._todo_open:
            self.todo_board.raise_()

    def _collect_todo_rows(self):
        """遍历全部案件，返回「个人申请 + 已建卡 + 未结束」案件的 (case_id, derive结果)。"""
        try:
            cases = self._load_cases_data()
        except Exception:
            cases = {}
        today = today_iso()
        rows = []
        for cid, cobj in cases.items():
            try:
                if str(cobj.get('applicant_type', '')) != '个人申请':
                    continue
                sf = cobj.get('service_flow')
                if not sf or sf.get('done'):
                    continue
                d = derive(sf, today)
                if d.get('phase') in ('none', 'done'):
                    continue
                rows.append((cid, d))
            except Exception:
                continue
        return rows

    def _refresh_todo_board(self):
        """刷新下拉看板内容与顶栏按钮上的任务计数。"""
        if not getattr(self, 'todo_board', None) or not getattr(self, 'todo_btn', None):
            return
        rows = self._collect_todo_rows()
        self.todo_board.set_tasks(rows)
        self.todo_btn.setText(f"待办事项({len(rows)})")

    def _ensure_service_flow_started(self, case_id: str, case_obj: dict = None, role: str = ""):
        """个人申请案：录入第一份谈话笔录成功后自动建卡（幂等；老案件不补建）。
        建卡口径：目录中「谈话笔录」docx 份数 <=1（即功能上线后首次产生笔录）。"""
        try:
            cases = self._load_cases_data()
        except Exception:
            return
        stored = cases.get(case_id)
        if not stored:
            if not case_obj:
                return
            stored = case_obj
        if str(stored.get('applicant_type', '')) != '个人申请':
            return
        if stored.get('service_flow'):
            return
        # 老案件（此前已有笔录）不补建
        try:
            folder = self._case_dir(case_id)
            cnt = 0
            if os.path.isdir(folder):
                cnt = sum(1 for f in os.listdir(folder)
                          if f.lower().endswith('.docx') and '谈话笔录' in f)
            if cnt > 1:
                return
        except Exception:
            pass
        stored['service_flow'] = initial_sf(case_id, role=role)
        cases[case_id] = stored
        try:
            self._save_cases_data(cases)
        except Exception:
            return
        self._set_status(f'已建立文书送达待办：{case_id}', 'green')
        self._refresh_todo_board()

    def _persist_service_flow(self, case_id: str, nsf: dict) -> bool:
        """把新的 service_flow 写回案件 JSON 并刷新看板。"""
        try:
            cases = self._load_cases_data()
            if case_id not in cases:
                return False
            cases[case_id]['service_flow'] = nsf
            ok = self._save_cases_data(cases)
            self._refresh_todo_board()
            return ok
        except Exception:
            return False

    def _on_board_task_click(self, case_id: str):
        """看板任务点击：先收起下拉，再重新读盘并按当前节点派生 → 打开对应弹窗。"""
        if getattr(self, 'todo_board', None):
            self.todo_board.hide()
            self._todo_open = False
        try:
            cases = self._load_cases_data()
        except Exception:
            return
        cobj = cases.get(case_id)
        if not cobj or not cobj.get('service_flow'):
            return
        sf = cobj['service_flow']
        d = derive(sf, today_iso())
        action = d.get('action')
        if action == 'delivery_confirm':
            self._open_delivery_confirm(case_id, d.get('doc'),
                                        prefill=bool(d.get('prefill', False)))
        elif action == 'postal_tracking':
            self._open_postal_tracking(case_id, d.get('doc'))
        elif action == 'decision':
            self._on_decision_make(case_id)
        else:
            self._refresh_todo_board()

    def _open_delivery_confirm(self, case_id: str, doc: str, prefill: bool = False):
        """送达确认窗（举证/告知书复用）。保存 → confirm_delivery 落盘重算。"""
        try:
            cobj = self._load_cases_data().get(case_id)
        except Exception:
            return
        sf = cobj.get('service_flow') if cobj else None
        if not sf:
            return
        st = sf.get('stage', {})
        dlg = DeliveryConfirmDialog(
            case_id, doc, self,
            prefill_method=(st.get('method') if prefill else None),
            prefill_deliver=(st.get('deliver_time') if prefill else ''),
            prefill_send=(st.get('send_time') if prefill else ''))
        if dlg.exec_() != QDialog.Accepted:
            return
        nsf = confirm_delivery(sf, doc, dlg.get_method(),
                               dlg.get_deliver_time(), dlg.get_send_time())
        self._persist_service_flow(case_id, nsf)
        self._set_status(f'已记录{DOC_LABEL.get(doc, doc)}送达确认', 'green')

    def _open_postal_tracking(self, case_id: str, doc: str):
        """邮寄送达状态追踪。①② → 退回送达确认第一步并重开窗；③ → 更新送达时间。"""
        try:
            cobj = self._load_cases_data().get(case_id)
        except Exception:
            return
        sf = cobj.get('service_flow') if cobj else None
        if not sf:
            return
        dlg = PostalTrackingDialog(case_id, doc, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        act = dlg.get_action()
        if act in ('resend', 'switch'):
            nsf = revert_to_ask(sf, act, note=dlg.get_note())
            self._persist_service_flow(case_id, nsf)
            # 退回「送达确认」第一步：立即重开窗（再次寄送保留方式，改用其它方式则清空）
            self._open_delivery_confirm(case_id, doc, prefill=(act == 'resend'))
        elif act == 'delivered':
            nsf = delivered_again(sf, dlg.get_new_deliver_time(), note=dlg.get_note())
            self._persist_service_flow(case_id, nsf)
            self._set_status('已确认送达，倒计时按新的送达时间重算', 'green')
        else:
            self._refresh_todo_board()

    def _on_decision_make(self, case_id: str):
        """制作工伤认定决定书确认。是 → 结束流程(移除待办)并触发“案件审批表”(approve)。"""
        try:
            cobj = self._load_cases_data().get(case_id)
        except Exception:
            return
        sf = cobj.get('service_flow') if cobj else None
        if not sf:
            return
        dlg = DecisionConfirmDialog(case_id, self)
        if dlg.exec_() != QDialog.Accepted or not dlg.get_choice():
            return  # 否：关闭，保持该待办等待下次处理
        # 是：视为已触发制作动作 → 流程结束，删除本案件提醒
        self._persist_service_flow(case_id, finish_flow(sf))
        self._set_status(f'{case_id} 文书送达流程结束，触发制作工伤认定决定书(案件审批表)', 'green')
        # 装载该案到主界面，使 approve() 读到正确案本号/日期
        try:
            self._apply_case_object(cobj)
        except Exception as e:
            logger.warning(f"⚠️ 决定书装载案件到主界面失败: {e}")
        self.lineEdit_2.setText(case_id)
        QTimer.singleShot(0, self.approve)  # 等同点击“案件审批表”按钮

    def _build_unified_template_data(self, case_obj: Dict[str, Any]) -> Dict[str, Any]:
        """构建统一的模板渲染字典。

        中文 key 为主（模板占位符统一用中文），同时附带英文 case_obj 字段名 key，
        模板里写中文或英文占位符都能被替换。
        """
        elements = case_obj.get('proposed_article_elements', []) or []
        materials = case_obj.get('materials', []) or []
        # {{已提供材料}} 只列**已勾选**的：未勾选的也列进去，AI 会以为证据已经齐了
        material_names = [m.get('name', '') for m in materials
                          if isinstance(m, dict) and m.get('name') and m.get('provided')]
        injury_time = format_compact_time(case_obj.get('injury_time', ''))
        visit_time = format_compact_time(case_obj.get('visit_time', ''))

        # 统一中文占位符
        zh = {
            '案本号': case_obj.get('case_id', ''),
            '案件性质': case_obj.get('case_nature', ''),
            '申请类型': case_obj.get('applicant_type', ''),
            '用工单位': case_obj.get('employer', ''),
            '用人单位': case_obj.get('labor_unit', ''),
            '工地名称': case_obj.get('site', ''),
            '申请时间': case_obj.get('apply_time', ''),
            '受理时间': case_obj.get('accept_time', ''),
            '受伤时间': injury_time,
            '就诊时间': visit_time,
            '拟用条例': case_obj.get('proposed_article', ''),
            '法律要件': ' + '.join(elements) if elements else '',
            '本人姓名': case_obj.get('name', ''),
            '本人性别': case_obj.get('gender', ''),
            '本人年龄': case_obj.get('age', ''),
            '本人身份证号': case_obj.get('id_card', ''),
            '本人手机号': case_obj.get('phone', ''),
            '本人身份证地址': case_obj.get('address', ''),
            '本人岗位': case_obj.get('position', ''),
            '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
            '单位名称': case_obj.get('labor_unit', ''),
            '本人身份': case_obj.get('identity', DEFAULT_IDENTITY),
            '受伤经过': case_obj.get('injury_description', ''),
            '已提供材料': '、'.join(material_names) if material_names else '',
            '记录人': case_obj.get('recorder', '') or self._get_current_username(),
            '申请人名称': case_obj.get('applicant_name', ''),
            '用户名': self._get_current_username(),
            '当前时期': self.get_data('当前时期', '') or (_date_now() + _time_now()),
        }

        # 英文 key（case_obj 字段名，兼容写法）
        en = {
            'case_id': case_obj.get('case_id', ''),
            'case_nature': case_obj.get('case_nature', ''),
            'applicant_type': case_obj.get('applicant_type', ''),
            'employer': case_obj.get('employer', ''),
            'labor_unit': case_obj.get('labor_unit', ''),
            'site': case_obj.get('site', ''),
            'apply_time': case_obj.get('apply_time', ''),
            'accept_time': case_obj.get('accept_time', ''),
            'injury_time': injury_time,
            'visit_time': visit_time,
            'proposed_article': case_obj.get('proposed_article', ''),
            'proposed_article_elements': ' + '.join(elements) if elements else '',
            'name': case_obj.get('name', ''),
            'gender': case_obj.get('gender', ''),
            'age': case_obj.get('age', ''),
            'id_card': case_obj.get('id_card', ''),
            'phone': case_obj.get('phone', ''),
            'address': case_obj.get('address', ''),
            'position': case_obj.get('position', ''),
            'injury_description': case_obj.get('injury_description', ''),
            'materials': '、'.join(material_names) if material_names else '',
            'recorder': case_obj.get('recorder', '') or self._get_current_username(),
            'applicant_name': case_obj.get('applicant_name', ''),
        }

        merged = dict(zh)
        merged.update(en)
        return merged

    def _build_case_object(self, data, materials) -> Dict[str, Any]:
        """构建单个案件对象（case_id 为第一字段）

        - 本人数据平铺在顶层，injury_description 仅本人
        - materials 只保留「已提供（勾选）」的证据
        - 含记录人、申请人名称、证人（并入 cases_data.json，单一数据源）
        """
        case = {
            "case_id": data.get('case_id', ''),
            "applicant_name": data.get('name', '') if data.get('applicant_type', '') == '个人申请' else data.get('labor_unit', ''),
            "case_nature": data.get('case_nature', ''),
            "applicant_type": data.get('applicant_type', ''),
            "unit_type": data.get('unit_type', DEFAULT_UNIT_TYPE),
            "employer": data.get('employer', ''),
            "labor_unit": data.get('labor_unit', ''),
            "site": data.get('site', ''),
            "apply_time": data.get('apply_time', ''),
            "accept_time": data.get('accept_time', ''),
            "visit_time": data.get('visit_time', ''),
            "injury_time": data.get('injury_time', ''),
            "proposed_article": data.get('regulation', ''),
            "proposed_article_elements": _regulation_elements(data.get('regulation', '')),
            # ── 本人（一套完整数据）──
            "name": data.get('name', ''),
            "gender": data.get('gender', ''),
            "age": data.get('age', ''),
            "id_card": data.get('id_card', ''),
            "phone": data.get('phone', ''),
            "address": data.get('address', ''),
            "position": data.get('position', ''),
            "identity": data.get('identity', DEFAULT_IDENTITY),
            "unit": data.get('unit', '') or self.get_data('本人单位名称', ''),
            "injury_description": data.get('injury_desc', ''),
            "materials": _filter_provided(materials),
            # ── 记录人 / 证人 ──
            "recorder": self._get_current_username(),
            "witnesses": list(self.data_model.witnesses),
            "legal_reps": self._collect_legal_reps(),
            "family_reps": self._collect_family_reps(),
        }
        return case

    def _start_transcript_generation(self, role: str, case_id: str, case_obj: dict, prompt_text: str):
        """统一的笔录生成启动（本人/证人/法人共用一条代码路径）：txt 提示词 → AI 后台线程"""
        self._set_status(f'正在AI生成{role}谈话笔录...', 'black')
        QApplication.processEvents()
        self.transcript_worker = TranscriptFromTemplateWorker(self.ai_service, prompt_text)
        self.transcript_worker.finished.connect(
            lambda result, r=role, cid=case_id, co=case_obj: self._on_transcript_generated(r, cid, co, result)
        )
        self.transcript_worker.error.connect(self._on_transcript_error)
        self.transcript_worker.start()

    def _identity_wording_hint(self, unit_type: str, identity: str, role: str) -> str:
        """按单位性质/身份给 AI 一句措辞提示（只返回句子，行首「- 称谓提示：」标签在模板里）。

        企业案返回空串——沿用「职工/公司」口径，无需提示；此时提示词里那一行会整行消失
        （见 render_prompt_template：被替换成空串的占位符若独占一行则整行删掉）。
        机关/事业单位若不提示，AI 容易写出「公司职工、考勤打卡、车间班组」等企业话术。
        """
        ut = (unit_type or "").strip() or DEFAULT_UNIT_TYPE
        if ut == "企业":
            return ""
        ident = (identity or "").strip() or DEFAULT_IDENTITY
        appellation = UNIT_TYPE_APPELLATION.get(ut) or f"{ut}工作人员"
        return (f"本案单位性质为【{ut}】。{role}的身份是【{ident}】。"
                f"请把受伤职工/被询问人的称谓写成“{appellation}”，"
                f"避免“公司职工、在公司上班、考勤打卡、车间班组”等企业话术。")

    def _prompt_fill_data(self, role: str, case_obj: dict) -> Dict[str, Any]:
        """返回用于填充该角色「发给AI」提示词的数据（case_obj + 当前人记录/法人flat 兼容键）"""
        if role == '证人':
            self._ensure_current_witness()
            # 不调用 _sync_form_to_current_witness（open_data_review 已把表单回填成本人数据，会污染证人）
            w = self._current_witness() or {}
            return {
                '案本号': case_obj.get('case_id', ''),
                '案件性质': case_obj.get('case_nature', ''),
                '申请类型': case_obj.get('applicant_type', ''),
                '本人姓名': case_obj.get('name', ''),
                '本人性别': case_obj.get('gender', ''),
                '本人身份证号': case_obj.get('id_card', ''),
                '用人单位': case_obj.get('labor_unit', ''),
                '用工单位': case_obj.get('employer', ''),
                '工地名称': case_obj.get('site', ''),
                '受伤经过': case_obj.get('injury_description', ''),
                '证人姓名': w.get('name', '') or self.get_data('证人姓名', ''),
                '证人身份证号': w.get('id_card', '') or self.get_data('证人身份证号', ''),
                '证人岗位': w.get('position', '') or self.get_data('证人岗位', ''),
                '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
                '证人身份': w.get('identity') or self.get_data('证人身份', '') or DEFAULT_IDENTITY,
                '身份话术': self._identity_wording_hint(
                    case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
                    w.get('identity') or self.get_data('证人身份', '') or DEFAULT_IDENTITY, '证人'),
            }
        if role == '法人':
            return {
                '案本号': case_obj.get('case_id', ''),
                '案件性质': case_obj.get('case_nature', ''),
                '申请类型': case_obj.get('applicant_type', ''),
                '本人姓名': case_obj.get('name', ''),
                '本人性别': case_obj.get('gender', ''),
                '本人身份证号': case_obj.get('id_card', ''),
                '用人单位': case_obj.get('labor_unit', ''),
                '用工单位': case_obj.get('employer', ''),
                '工地名称': case_obj.get('site', ''),
                '受伤经过': case_obj.get('injury_description', ''),
                '法人姓名': self.get_data('法人姓名', ''),
                '法人职务': self.get_data('法人职务', ''),
                '法人身份证号': self.get_data('法人身份证号', ''),
                '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
                '法人身份': self.get_data('法人身份', '') or DEFAULT_IDENTITY,
                '身份话术': self._identity_wording_hint(
                    case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
                    self.get_data('法人身份', '') or DEFAULT_IDENTITY, '法人'),
            }
        if role == '家属':
            return {
                '案本号': case_obj.get('case_id', ''),
                '案件性质': case_obj.get('case_nature', ''),
                '申请类型': case_obj.get('applicant_type', ''),
                '本人姓名': case_obj.get('name', ''),      # 死者姓名（受伤职工）
                '本人性别': case_obj.get('gender', ''),
                '本人身份证号': case_obj.get('id_card', ''),
                '用人单位': case_obj.get('labor_unit', ''),
                '用工单位': case_obj.get('employer', ''),
                '工地名称': case_obj.get('site', ''),
                '受伤经过': case_obj.get('injury_description', ''),
                '家属姓名': self.get_data('家属姓名', ''),
                '家属身份证号': self.get_data('家属身份证号', ''),
                '与死者关系': self.get_data('家属身份', ''),
                '家属单位名称': self.get_data('家属单位名称', ''),
                # 家属没单位 → 岗位一并留空（与笔录表头同一口径）
                '家属岗位': (self.get_data('家属岗位', '')
                             if self.get_data('家属单位名称', '') else ''),
                '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
                '家属身份': self.get_data('家属身份', ''),
                '身份话术': self._identity_wording_hint(
                    case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
                    self.get_data('家属身份', ''), '家属'),
            }
        # 本人：复用统一模板数据（中文+英文 key 富余项替换无害），并附身份话术
        base = self._build_unified_template_data(case_obj)
        base['身份话术'] = self._identity_wording_hint(
            case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
            case_obj.get('identity', DEFAULT_IDENTITY), '本人')
        return base

    def _build_prompt_for_role(self, role: str, case_obj: dict) -> str:
        """按角色返回发给 AI 的 txt 提示词（ROLE_TALK 定 key，统一渲染并校验残留占位符）

        条件块（时间核对、第（六）项问现住址）已写进各自的 txt 模板，用 `{% if %}` 控制，
        代码这边只负责填数据——要改措辞或加减条件，改 resource/prompts/ 下的 txt 即可。
        """
        from prompt_manager import load_prompt
        meta = ROLE_TALK.get(role, ROLE_TALK['本人'])
        prompt = load_prompt(meta['ai_prompt'])
        return render_prompt_template(prompt, self._prompt_fill_data(role, case_obj), role)

    def _build_prompt_or_warn(self, role: str, case_obj: dict) -> Optional[str]:
        """拼提示词；提示词文件缺失/为空时给个明确提示，返回 None（别让程序闪退）。

        这个异常必须在这里兜住：调用它的是 Qt 槽函数，漏出去的异常会被 qFatal 直接中止
        进程（见 log_utils.install_excepthook），用户只会看到程序莫名其妙关掉。
        """
        from prompt_manager import PromptError
        try:
            return self._build_prompt_for_role(role, case_obj)
        except PromptError as e:
            logger.error(f"❌ 无法生成{role}谈话笔录：{e}")
            QMessageBox.critical(
                self, "提示词文件有问题",
                f"无法生成{role}谈话笔录。\n\n{e}\n\n"
                f"请检查 resource/prompts/ 下的提示词文件（可能被删除、清空或被占用）。")
            self._set_status(f'{role}笔录：提示词文件缺失或为空', 'red')
            return None

    def _generate_role_transcript(self, role: str):
        """统一的谈话笔录生成入口——四个角色都走这里（数据核对确认后拼提示词 → AI 后台线程）"""
        case_id = self.current_case_id or self.lineEdit_2.text().strip()
        if not case_id:
            self._set_status(f'无案本号，无法生成{role}笔录', 'orange')
            return
        if not self.ai_service:
            self._set_status('未配置AI，无法生成笔录', 'orange')
            QMessageBox.warning(self, "提示", f"未配置API密钥，无法生成{role}谈话笔录。\n请在顶部⚙配置中设置API密钥。")
            return
        case_obj = self._load_cases_data().get(case_id)
        if not case_obj:
            self._set_status('未找到该案本号的案件数据', 'orange')
            return
        prompt_text = self._build_prompt_or_warn(role, case_obj)
        if prompt_text is None:
            return
        self._start_transcript_generation(role, case_id, case_obj, prompt_text)

    def _on_transcript_generated(self, role: str, case_id: str, case_obj: dict, result: dict):
        if result.get("状态") != "成功":
            err = result.get("错误信息", "未知错误")
            logger.warning(f"⚠️ {role}谈话笔录生成失败: {err}")
            self._set_status(f'{role}谈话笔录生成失败: {err[:40]}', 'orange')
            return
        content = (result.get("内容", "") or "").strip()
        if not content:
            logger.warning(f"⚠️ {role}谈话笔录生成失败：AI 返回内容为空")
            self._set_status(f'{role}谈话笔录生成失败：AI 返回内容为空', 'orange')
            return
        path = self._save_transcript_to_template(case_obj, content, role)
        if not path:
            return
        if role == '证人':
            self._save_witnesses()  # 持久化证人数据
        # 个人申请案：录入第一份谈话笔录后自动建立“文书送达”待办（看板）
        self._ensure_service_flow_started(case_id, case_obj, role)
        success, message = self.file_service.open_document(path)
        if success:
            self._set_status(f'已生成并打开{role}谈话笔录', 'green')
        else:
            self._set_status(f'{role}谈话笔录已生成，打开失败: {message}', 'orange')

    def _on_transcript_error(self, err: str):
        logger.error(f"❌ 询问笔录生成出错: {err}")
        self._set_status('询问笔录生成出错', 'red')

    def _main_transcript_files(self, case_id: str) -> List[str]:
        """该案卷目录下已生成的本人笔录文件（基础名与 (2)(3) 副本都算）

        用 _locate_case_dir 找**已存在**的目录——_case_dir 在案件还没落盘时会返回一个
        尚不存在的新路径。各角色 label 不同（本人/证人/法人/家属谈话笔录），
        按「本人谈话笔录」匹配不会误伤别的角色。
        """
        folder = self._locate_case_dir(case_id) if case_id else ''
        if not folder or not os.path.isdir(folder):
            return []
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        return [os.path.join(folder, n) for n in sorted(names)
                if n.endswith('.docx') and '本人谈话笔录' in n]

    def _delete_main_transcripts(self, paths: List[str]) -> bool:
        """删掉旧的本人笔录；有文件删不掉（例如正被 Word 打开）就返回 False 并提示"""
        failed = []
        for p in paths:
            try:
                os.remove(p)
                print(f"🗑️ 已删除旧的本人笔录: {p}")
            except OSError as e:
                logger.warning("⚠️ 删除旧笔录失败 %s: %s", p, e)
                failed.append(p)
        if failed:
            QMessageBox.warning(
                self, "无法覆盖",
                "删除旧的本人笔录失败（可能正被 Word/WPS 打开）：\n%s\n\n"
                "请先关闭该笔录文档，再点一次「谈话笔录」。" % '\n'.join(failed))
            return False
        return True

    def _confirm_overwrite_main_transcript(self, case_id: str) -> bool:
        """本人笔录已存在时问一句要不要覆盖；返回 True 表示可以继续生成。

        点「是」会把旧笔录（含之前累积的 (2)(3) 副本）一起删掉，随后按主界面当前
        数据重新生成；删不掉就不继续，免得又堆一个新文件。
        """
        old = self._main_transcript_files(case_id)
        if not old:
            return True
        msg = QMessageBox(self)
        msg.setWindowTitle("本人笔录已存在")
        msg.setIcon(QMessageBox.Question)
        msg.setText("该案本号下已生成过本人谈话笔录，是否覆盖？")
        msg.setInformativeText(
            "点「是」会删掉旧的笔录文件（含之前生成的副本 %d 个），"
            "然后按主界面当前数据重新生成。" % len(old))
        yes_btn = msg.addButton("是", QMessageBox.YesRole)
        no_btn = msg.addButton("否", QMessageBox.NoRole)
        msg.setDefaultButton(no_btn)          # 默认落在「否」：手快回车不该把旧的删了
        msg.exec_()
        if msg.clickedButton() is not yes_btn:
            logger.info("用户选择不覆盖旧的本人笔录")
            return False
        return self._delete_main_transcripts(old)

    def _save_transcript_to_template(self, case_obj: dict, content: str, role: str = '本人') -> str:
        """渲染「{role}谈话笔录（普通工伤案件）.docx」模板，把 AI 问答插入到告知程序之后，返回文件路径（失败返回空串）。
        本人/证人/法人共用同一条逻辑，仅按角色选择模板与占位符数据。"""
        try:
            from docx.shared import Pt
            meta = ROLE_TALK.get(role, ROLE_TALK['本人'])
            label = f'{role}谈话笔录'

            # ── 1. 渲染谈话模板（替换占位符）──
            template_path = str(path_utils.get_talk_template_path(meta['talk_template']))
            if not os.path.exists(template_path):
                logger.warning(f"⚠️ 谈话模板不存在: {template_path}")
                return ""

            template_data = getattr(self, meta['docx_data'])(case_obj)

            # ── 1.1 用 docxtpl 渲染占位符（保留占位符原有格式）──
            from docxtpl import DocxTemplate
            doc = DocxTemplate(template_path)
            doc.render(template_data)

            # ── 2. 定位「答：听清楚了，不申请回避。」锚点 ──
            anchor_index = None
            anchor_pf = None
            for i, p in enumerate(doc.paragraphs):
                if '答：听清楚了，不申请回避' in p.text:
                    anchor_index = i
                    anchor_pf = p.paragraph_format
                    break

            if anchor_index is None:
                logger.warning("⚠️ 未找到锚点「答：听清楚了，不申请回避」")
                return ""

            # ── 3. 删除锚点之后的模板样例问答（保留头部+告知，避免与AI问答重复）──
            body = doc.element.body
            anchor_elem = doc.paragraphs[anchor_index]._element
            after_anchor = False
            for child in list(body):
                if after_anchor:
                    body.remove(child)
                elif child is anchor_elem:
                    after_anchor = True

            # ── 4. 在锚点之后插入 AI 问答（每行下划线）──
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                p = doc.add_paragraph()
                if anchor_pf is not None:
                    p.paragraph_format.alignment = anchor_pf.alignment
                    p.paragraph_format.first_line_indent = anchor_pf.first_line_indent
                    p.paragraph_format.space_before = anchor_pf.space_before
                    p.paragraph_format.space_after = anchor_pf.space_after
                run = p.add_run(line)
                run.font.size = Pt(15)
                run.underline = True

            # ── 5. 保存 ──
            if not self.current_case_folder or not os.path.exists(self.current_case_folder):
                folder_subject = str(case_obj.get('case_id', '') or case_obj.get('name', '') or '案件').strip()
                # 走 _case_dir：年份目录 + 已存在的按原位（文书和数据必须落在同一个案卷文件夹里）
                self.current_case_folder = self._case_dir(folder_subject)
                os.makedirs(self.current_case_folder, exist_ok=True)

            subject = str(case_obj.get('name', '') or case_obj.get('case_id', '') or '案件').strip()
            file_name = f"{subject}{label}.docx"
            target_path = os.path.join(self.current_case_folder, file_name)
            counter = 2
            while os.path.exists(target_path):
                file_name = f"{subject}{label}({counter}).docx"
                target_path = os.path.join(self.current_case_folder, file_name)
                counter += 1

            doc.save(target_path)
            print(f"✅ {label}已生成: {target_path}")
            return target_path
        except Exception as e:
            logger.error(f"❌ 生成{role}谈话笔录失败: {e}")
            import traceback
            traceback.print_exc()
            self._set_status(f'生成{role}谈话笔录失败', 'red')
            return ""

    # ========================================================================
    # 证人谈话笔录：提示词 / 模板占位符数据（生成统一走 _generate_role_transcript）
    # ========================================================================

    def _build_witness_template_data(self, case_obj: dict) -> dict:
        """构建证人谈话笔录模板的占位符数据"""
        self._ensure_current_witness()
        # 注意：不调用 _sync_form_to_current_witness（open_data_review 已把表单回填成本人数据，会污染证人）
        w = self._current_witness() or {}
        return {
            '当前时期': self.get_data('当前时期', '') or (_date_now() + _time_now()),
            '用户名': self._get_current_username(),
            '本人姓名': case_obj.get('name', ''),
            '证人姓名': w.get('name', '') or self.get_data('证人姓名', ''),
            '证人性别': w.get('gender', '') or self.get_data('证人性别', ''),
            '证人年龄': w.get('age', '') or self.get_data('证人年龄', ''),
            '证人身份证号': w.get('id_card', '') or self.get_data('证人身份证号', ''),
            '证人身份证地址': w.get('address', '') or self.get_data('证人身份证地址', ''),
            '证人手机号': w.get('phone', '') or self.get_data('证人手机号', ''),
            '证人岗位': w.get('position', '') or self.get_data('证人岗位', ''),
            '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
            '单位名称': case_obj.get('labor_unit', ''),
            '证人身份': w.get('identity') or self.get_data('证人身份', '') or DEFAULT_IDENTITY,
            '公司名称': case_obj.get('labor_unit', ''),  # 用人单位（签合同的单位）
        }


    # ========================================================================
    # 法人谈话笔录：表单同步 + 提示词 / 模板占位符数据（生成统一走 _generate_role_transcript）
    # ========================================================================

    def _sync_legal_to_form(self):
        """数据核对后把表单切回法人数据（open_data_review 会把表单回填成本人数据）"""
        if self.get_current_role_type() != "法人":
            return
        self._write_person_to_form(self._person_from_flat('法人'))

    def _sync_family_to_form(self):
        """数据核对后把表单切回家属数据（open_data_review 会把表单回填成本人数据）"""
        if self.get_current_role_type() != "家属":
            return
        self._write_person_to_form(self._person_from_flat('家属'))

    def _build_legal_template_data(self, case_obj: dict) -> dict:
        """构建法人谈话笔录模板的占位符数据"""
        return {
            '当前时期': self.get_data('当前时期', '') or (_date_now() + _time_now()),
            '用户名': self._get_current_username(),
            '本人姓名': case_obj.get('name', ''),
            '法人姓名': self.get_data('法人姓名', ''),
            '法人性别': self.get_data('法人性别', ''),
            '法人年龄': self.get_data('法人年龄', ''),
            '法人身份证号': self.get_data('法人身份证号', ''),
            '法人身份证地址': self.get_data('法人身份证地址', ''),
            '法人手机号': self.get_data('法人手机号', ''),
            '法人职务': self.get_data('法人职务', ''),
            '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
            '单位名称': case_obj.get('labor_unit', ''),
            '法人身份': self.get_data('法人身份', '') or DEFAULT_IDENTITY,
            '公司名称': case_obj.get('labor_unit', ''),  # 用人单位（签合同的单位）
        }

    def _build_family_template_data(self, case_obj: dict) -> dict:
        """构建家属谈话笔录模板的占位符数据（被询问人=工亡职工近亲属；本人姓名 指死者）

        表头那行描述的是「家属自己的单位」：家属单位名称 逐字取自「用人单位」控件
        （本人角色下才是案件级用人单位）。家属没单位时，岗位一并留空。
        """
        fam_unit = self.get_data('家属单位名称', '')
        return {
            '当前时期': self.get_data('当前时期', '') or (_date_now() + _time_now()),
            '用户名': self._get_current_username(),
            '本人姓名': case_obj.get('name', ''),      # 死者姓名
            '家属姓名': self.get_data('家属姓名', ''),
            '家属性别': self.get_data('家属性别', ''),
            '家属年龄': self.get_data('家属年龄', ''),
            '家属身份证号': self.get_data('家属身份证号', ''),
            '家属身份证地址': self.get_data('家属身份证地址', ''),
            '家属手机号': self.get_data('家属手机号', ''),
            '与死者关系': self.get_data('家属身份', ''),
            '家属单位名称': fam_unit,
            '家属岗位': self.get_data('家属岗位', '') if fam_unit else '',
            '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
            '单位名称': case_obj.get('labor_unit', ''),
            '家属身份': self.get_data('家属身份', ''),
            '公司名称': case_obj.get('labor_unit', ''),  # 用人单位（签合同的单位）
        }


    # ========================================================================
    # F2 测试数据轮换
    # ========================================================================

    def keyPressEvent(self, event):
        """F2 键轮换测试数据"""
        if event.key() == Qt.Key_F2:
            self._cycle_test_data()
        else:
            super().keyPressEvent(event)

    def _cycle_test_data(self):
        """按 F2 切换到下一组测试数据"""
        self._test_data_index = (self._test_data_index + 1) % len(TEST_DATA_PRESETS)
        data = TEST_DATA_PRESETS[self._test_data_index]

        print(f"\n{'=' * 50}")
        print(f"F2 测试数据: {data['name']}")
        print(f"{'=' * 50}")

        # ── 角色单选按钮 ──
        role_map = {"本人": self.radioButton, "证人": self.radioButton_2,
                    "法人": self.radioButton_3, "家属": self.radioButton_4}
        for role_name, btn in role_map.items():
            btn.setChecked(role_name == data["role"])
        # 程序化 setChecked 不会触发 clicked，手动触发一次角色切换清理
        self.clear_role_fields()

        # ── 案例类型复选框 ──
        self.death_case_checkbox.setChecked(data["deathCaseCheckbox"])
        self.personal_application_checkbox.setChecked(data["personalApplicationCheckbox"])
        self.on_case_type_changed()

        # ── 基本信息输入框 ──
        self.name_pane.setText(data["name_pane"])
        self.idnumer_pane.setText(data["idnumer_pane"])
        self.textEdit.setPlainText(data["textEdit"])
        self.lineEdit_4.setText(data["lineEdit_4"])
        self.lineEdit_5.setText(data["lineEdit_5"])
        # 案本号：切到本人时清空（输入新姓名后自动生成）；切到证人/法人保留同一案本号
        if data["role"] == "本人":
            self.lineEdit_2.clear()

        # ── 条例下拉框 ──
        # 拟用条例按规范短名定位，不要用索引——增删条例会让索引整体位移，
        # 预设就静默指到别的条例上去了
        _reg_idx = self.comboBox.findText(_regulation_short_to_full(data.get("regulation", "")))
        if _reg_idx >= 0:
            self.comboBox.setCurrentIndex(_reg_idx)

        # ── 公司下拉框 ──
        self._set_combo_or_type(self.company_pane, data["company_pane"])
        self._set_combo_or_type(self.construction_company, data["construction_company"])
        self._set_combo_or_type(self.construction_plant, data["construction_plant"])

        # ── 单位性质 / 人员身份（机关公务员/事业单位支持）──
        if hasattr(self, 'unit_type_combo'):
            self._set_combo_or_type(self.unit_type_combo, data.get('unit_type', DEFAULT_UNIT_TYPE))
        if hasattr(self, 'identity_edit'):
            self.identity_edit.setText(data.get('identity', DEFAULT_IDENTITY))

        # ── 自动计算年龄和性别 ──
        self.on_id_input_finished()

        # ── 同步角色数据到数据模型，供 JSON 保存（统一人记录 schema） ──
        role = self.get_current_role_type()
        if role == "证人":
            name = data['name_pane']
            w = next((x for x in self.data_model.witnesses if x.get('name') == name), None)
            if w is None:
                w = {"role": "证人", "seq": _witness_label(len(self.data_model.witnesses) + 1)}
                self.data_model.witnesses.append(w)
            w.update(self._read_form_as_person())
            # 指向该证人，避免索引无效导致生成/回填拿不到证人数据
            self.data_model.current_witness_index = self.data_model.witnesses.index(w)
            # 同步扁平 证人* 键：本人/法人/家属分支走 _person_to_flat，证人需显式镜像，
            # 否则扁平键会停留在上一位证人（F2 轮换后生成笔录会拿到旧值）
            self._mirror_witness_to_flat(w)
        else:
            # 本人 / 法人 / 家属：表单 → 角色前缀扁平兼容键（统一走 _read_form_as_person）
            self._person_to_flat(role, self._read_form_as_person())
            # 家属(工亡)：以死者(injured_worker)为本人姓名，供案号/文案指向死者
            if role == "家属" and data.get('injured_worker'):
                self.set_data('本人姓名', data['injured_worker'], 'basic')

        # ── 右侧面板（同案沿用） ──
        current_worker = data.get("injured_worker", "")
        prev_worker = getattr(self, '_prev_injured_worker', None)
        is_same_case = (prev_worker is not None and current_worker == prev_worker)
        if not is_same_case:
            self.current_case_id = ""  # 换了受伤职工 → 视为新案件，重置案本号关联
            self.set_data('案本号', '', 'case')  # 同时清掉数据模型里缓存的旧案本号
            self.lineEdit_2.clear()  # 测试轮换时清掉界面案本号，点击后重新生成

        # 案件陈述：原「案件申请陈述」框已停用，预设里的陈述直接写数据模型，
        # 仍供文书/提示词的 {{受伤经过}} 使用
        self.set_data('受伤经过', data.get("statement_edit", ""), 'investigation')
        if is_same_case:
            print(f"📋 同案沿用案件陈述（{current_worker}）")

        # 材料：只有本人的证据有效（证人/法人的证据不保存）
        if role == "本人":
            mats = data.get('materials', [])
            if hasattr(self, 'material_list'):
                self.material_list.set_materials(mats)
            self.data_model.investigation['本人材料'] = mats
        else:
            # 证人/法人：恢复显示本人证据，不显示证人/法人证据
            if hasattr(self, 'material_list'):
                self.material_list.set_materials(self.data_model.investigation.get('本人材料', []))

        self._prev_injured_worker = current_worker

        # ── 重置案件状态，允许重新生成笔录 ──
        self.pushButton.setEnabled(True)
        self.pushButton.setStyleSheet("")
        self.current_case_folder = None
        self.current_person_name = ""

        # ── 状态提示 ──
        label = (f"[测试 {self._test_data_index + 1}/{len(TEST_DATA_PRESETS)}] "
                 f"{data['name']}  |  F2=下一个")
        self._set_status(label, 'green')
        print(f"OK 测试数据已填充: {data['name']}")

    def on_role_changed(self):
        """当角色切换时调用"""
        role = self.get_current_role_type()
        print(f"🔄 角色切换: {role}")
        # 共享“身份”输入行的标签/提示随角色变化（家属这一栏填的是与死者关系）
        # 标签槽是固定宽度（见 ui_main_build._SIZES 里的 identity_label 44px），
        # 所以不再按文本长度调宽度，
        # 否则切到长标签的角色会把左边的岗位输入框压住。
        if hasattr(self, 'identity_label'):
            self.identity_label.setText(
                _ROLE_IDENTITY_LABEL.get(role, _ROLE_IDENTITY_LABEL["本人"]))
        if hasattr(self, 'identity_edit'):
            self.identity_edit.setToolTip(_ROLE_IDENTITY_HINT.get(role, ""))
            # 家属这一栏无通用默认值，清掉占位符「职工」避免误读
            self.identity_edit.setPlaceholderText("" if role == "家属" else DEFAULT_IDENTITY)

    def _setup_paths(self):
        """统一使用PathUtils设置所有路径"""
        print("=" * 50)
        print("🔄 使用统一的PathUtils设置所有路径...")

        # 使用PathUtils获取路径（path_utils.get_xxx() 方法已确保目录存在）
        self.BASE_PATH = str(path_utils.get_storage_path())
        self.TEMPLATE_PATH = str(path_utils.get_template_path())
        self.CONFIG_PATH = str(path_utils.get_config_path(""))
        self.DATA_PATH = str(path_utils.get_data_path(""))

        # 获取模板子目录（path_utils 已确保目录存在）
        self.TALK_TEMPLATE_PATH = str(path_utils.get_talk_template_path())
        self.DOCUMENT_TEMPLATE_PATH = str(path_utils.get_document_template_path())

    def _update_services_paths(self):
        """更新所有服务的路径"""
        print("🔄 更新服务路径...")

        # 更新FileService
        if hasattr(self, 'file_service'):
            self.file_service.BASE_PATH = self.BASE_PATH
            print(f"✅ 更新FileService路径: {self.BASE_PATH}")


        # 更新数据模型
        if hasattr(self, 'data_model'):
            self.data_model.company_info['存储路径'] = self.BASE_PATH
            self.data_model.company_info['模板路径'] = self.TEMPLATE_PATH

        print("✅ 服务路径更新完成")

    def select_all_questions(self, select_all: bool):
        """全选或全不选问题"""
        if not hasattr(self, 'question_list_widget'):
            return

        for i in range(self.question_list_widget.count()):
            item = self.question_list_widget.item(i)
            item.setCheckState(Qt.Checked if select_all else Qt.Unchecked)

    def insert_selected_questions(self, dialog):
        """将选中的问题插入到笔录文档"""
        try:
            # 获取选中的问题
            selected_questions = []
            for i in range(self.question_list_widget.count()):
                item = self.question_list_widget.item(i)
                if item.checkState() == Qt.Checked:
                    selected_questions.append(item.text())

            if not selected_questions:
                QMessageBox.warning(dialog, "提示", "请至少选择一个要插入的问题")
                return

            print(f"✅ 选择了 {len(selected_questions)} 个问题准备插入")

            # 检查案件文件夹
            if not self.current_case_folder:
                QMessageBox.warning(dialog, "提示", "请先保存案件信息")
                return

            # 查找本人笔录文件
            person_name = self.get_data("本人姓名", "") or self.name_pane.text().strip()
            if not person_name:
                QMessageBox.warning(dialog, "提示", "请先输入受伤职工姓名")
                return

            # 查找主询问对象笔录（工亡案=家属，普通案=本人）
            person_files = self._main_transcript_candidates()
            if not person_files:
                QMessageBox.warning(dialog, "提示", "未找到可插入问题的谈话笔录文件")
                return

            # 使用第一个找到的主询问对象笔录
            file_path = os.path.join(self.current_case_folder, person_files[0])

            # 插入问题到文档
            success, message = self.insert_questions_to_document(file_path, selected_questions)

            if success:
                QMessageBox.information(dialog, "成功",
                                        f"已成功插入 {len(selected_questions)} 个问题到笔录中\n\n"
                                        f"文件：{os.path.basename(file_path)}")
                dialog.close()
            else:
                QMessageBox.critical(dialog, "失败", f"插入失败：{message}")

        except Exception as e:
            logger.error(f"❌ 插入问题失败: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(dialog, "错误", f"插入过程中发生错误：{str(e)}")

    def insert_questions_to_document(self, file_path: str, questions: list) -> tuple:
        """
        将问题插入到Word文档中

        Args:
            file_path: Word文档路径
            questions: 问题列表

        Returns:
            (是否成功, 消息)
        """
        try:
            from docx import Document
            from docx.shared import Pt, RGBColor

            print(f"📄 正在处理文档: {file_path}")

            # 打开文档
            doc = Document(file_path)

            # 查找插入位置（文档末尾）
            # 我们可以找到最后一个段落，然后在其后插入

            # 添加分隔线
            separator = doc.add_paragraph("=" * 50)
            separator.alignment = 1  # 居中

            # 添加标题
            title = doc.add_paragraph("AI建议补充问题：")
            title.runs[0].bold = True
            title.alignment = 0  # 左对齐

            # 插入每个问题
            for i, question_text in enumerate(questions, 1):
                # 添加问题
                question_para = doc.add_paragraph()
                question_para.add_run(f"{i}. {question_text}")

                # 添加答案占位符（带下划线）
                answer_para = doc.add_paragraph()
                answer_run = answer_para.add_run("答：")
                answer_run.font.underline = True
                answer_run.font.color.rgb = RGBColor(0, 0, 0)

                # 添加空行
                doc.add_paragraph()

            # 保存文档
            doc.save(file_path)

            print(f"✅ 成功插入 {len(questions)} 个问题到文档")

            return True, "插入成功"

        except Exception as e:
            logger.error(f"❌ 插入问题到文档失败: {e}")
            import traceback
            traceback.print_exc()
            return False, str(e)

    def parse_ai_result(self, ai_text: str) -> dict:
        """
        解析AI结果，提取审查结果和缺失问题

        Args:
            ai_text: AI返回的完整文本

        Returns:
            包含审查结果和缺失问题的字典
        """
        result = {
            "审查结果": "",
            "缺失问题": [],
            "原始文本": ai_text
        }

        try:
            # 分割审查结果和缺失问题
            if "【审查结果】" in ai_text and "【缺失问题列表】" in ai_text:
                # 提取审查结果部分
                start = ai_text.find("【审查结果】")
                end = ai_text.find("【缺失问题列表】")

                if start != -1 and end != -1:
                    review_text = ai_text[start:end]
                    # 清理标记
                    review_text = review_text.replace("【审查结果】", "").strip()
                    result["审查结果"] = review_text

                    # 提取缺失问题部分
                    questions_text = ai_text[end:]
                    # 按行分割
                    lines = questions_text.split('\n')

                    for line in lines:
                        line = line.strip()
                        # 查找带方框的问题行
                        if "□" in line and "问：" in line:
                            # 提取问题文本（去掉方框和序号）
                            # 示例：□ 1. 问：您与公司是否签订了书面劳动合同？
                            question = line
                            # 去掉方框标记
                            question = question.replace("□", "", 1).strip()
                            # 去掉序号（如"1. "）
                            if "." in question:
                                question = question.split(".", 1)[1].strip()

                            result["缺失问题"].append(question)

            # 如果格式不正确，尝试其他解析方式
            elif "审查结果" in ai_text and "缺失问题" in ai_text:
                # 尝试其他格式解析
                pass

            else:
                # 如果没有找到格式标记，整个文本作为审查结果
                result["审查结果"] = ai_text

        except Exception as e:
            print(f"解析AI结果失败: {e}")
            result["审查结果"] = ai_text

        print(f"✅ 解析结果: 审查结果长度={len(result['审查结果'])}, 问题数量={len(result['缺失问题'])}")
        return result

    def on_pushButton_12_clicked(self):
        """谈话通知书按钮点击事件"""
        print("🔄 谈话通知书按钮被点击")

        # 调用谈话通知书生成函数
        self.generate_interview_notice_from_approval()

    def extract_data_from_approval_table(self, file_path):
        """
        从审批表Word文件中提取数据（简化版）
        """
        try:
            from docx import Document

            print(f"📄 开始提取审批表数据: {file_path}")

            # 搜索关键词映射
            search_mapping = {
                '用人单位': '用人单位',
                '职工姓名': '职工姓名',
                '身份证号': '职工身份证号',
                '申请时间': '申请时间',
                '受理时间': '受理时间',
                '受伤经过': '受伤经过',
                '医疗诊断': '医疗证明',  # 搜索"医疗诊断"
            }

            extracted_data = {}
            document = Document(file_path)

            # 遍历所有表格
            for table in document.tables:
                for row in table.rows:
                    # 遍历每个单元格
                    for i, cell in enumerate(row.cells):
                        cell_text = cell.text.strip()

                        # 检查每个搜索词
                        for search_term, data_field in search_mapping.items():
                            # 如果已经找到了，跳过
                            if data_field in extracted_data:
                                continue

                            # 检查是否包含搜索词
                            if search_term in cell_text:
                                print(f"✅ 找到关键词 '{search_term}'")

                                # 尝试获取右边的单元格
                                if i + 1 < len(row.cells):
                                    right_cell = row.cells[i + 1]
                                    right_text = right_cell.text.strip()

                                    # 只有当右边单元格有内容且不是关键词时才使用
                                    if right_text and right_text != search_term:
                                        extracted_data[data_field] = right_text
                                        print(f"  提取 {data_field}: {right_text}")
                                    else:
                                        # 右边单元格是空的，标记为红色
                                        logger.warning(f"  ⚠️ {data_field}: 右边单元格为空")
                                else:
                                    logger.warning(f"  ⚠️ {data_field}: 没有右侧单元格")

            print("\n📋 提取结果:")
            required_fields = ['用人单位', '职工姓名', '职工身份证号', '申请时间', '受理时间', '受伤经过', '医疗证明']

            for field in required_fields:
                if field in extracted_data:
                    print(f"  ✅ {field}: {extracted_data[field]}")
                else:
                    logger.error(f"  ❌ {field}: 未找到")

            # 填充缺失字段
            current_date = _date_now()

            if '申请时间' not in extracted_data:
                extracted_data['申请时间'] = current_date
                print(f"  🟥 使用默认值 申请时间: {current_date}")

            if '受理时间' not in extracted_data:
                extracted_data['受理时间'] = current_date
                print(f"  🟥 使用默认值 受理时间: {current_date}")

            if '医疗证明' not in extracted_data:
                extracted_data['医疗证明'] = '详见医疗诊断证明'
                print(f"  🟥 使用默认值 医疗证明: 详见医疗诊断证明")

            # 其他字段使用界面数据
            if '用人单位' not in extracted_data:
                extracted_data['用人单位'] = self.company_pane.currentText().strip() or '未知公司'

            if '职工姓名' not in extracted_data:
                extracted_data['职工姓名'] = self.get_data('本人姓名', '') or self.name_pane.text().strip() or '未知'

            if '职工身份证号' not in extracted_data:
                extracted_data['职工身份证号'] = self.get_data('本人身份证号', '')

            return extracted_data

        except Exception as e:
            logger.error(f"❌ 提取审批表数据失败: {e}")
            import traceback
            traceback.print_exc()

            # 返回最小可用数据
            current_date = _date_now()
            return {
                '用人单位': self.company_pane.currentText().strip() or '未知公司',
                '职工姓名': self.get_data('本人姓名', '') or self.name_pane.text().strip() or '未知',
                '职工身份证号': self.get_data('本人身份证号', ''),
                '申请时间': current_date,
                '受理时间': current_date,
                '受伤经过': '详见谈话笔录',
                '医疗证明': '详见医疗诊断证明'
            }

    def generate_interview_notice_from_approval(self):
        """从审批表生成接受谈话通知书"""
        try:
            # 1. 检查本人姓名
            person_name = self.get_data("本人姓名", "")
            if not person_name:
                person_name = self.name_pane.text().strip()
            if not person_name:
                self._set_status('请先输入本人姓名', 'red')
                return

            # 2. 检查案件文件夹
            if not self.current_case_folder or not os.path.exists(self.current_case_folder):
                self._set_status('请先保存案件信息', 'red')
                return

            # 3. 查找本人案件审批表文件
            approval_file_name = f"{person_name}案件审批表.docx"
            approval_file_path = os.path.join(self.current_case_folder, approval_file_name)

            if not os.path.exists(approval_file_path):
                self._set_status(f'未找到审批表: {approval_file_name}', 'red')
                # 尝试查找其他可能的审批表文件
                all_files = os.listdir(self.current_case_folder)
                approval_files = [f for f in all_files if "审批表" in f and f.endswith('.docx')]

                if not approval_files:
                    self._set_status('案件文件夹中没有审批表文件', 'red')
                    return

                # 使用找到的第一个审批表文件
                approval_file_path = os.path.join(self.current_case_folder, approval_files[0])
                logger.warning(f"⚠️ 使用替代审批表: {approval_files[0]}")

            print(f"✅ 找到审批表文件: {approval_file_path}")

            # 4. 从审批表提取数据
            self._set_status('正在提取审批表数据...', 'black')
            QApplication.processEvents()

            extracted_data = self.extract_data_from_approval_table(approval_file_path)

            # 处理申请时间
            if '申请时间' in extracted_data:
                apply_time = extracted_data['申请时间']
                if isinstance(apply_time, str) and apply_time.isdigit() and len(apply_time) == 8:
                    extracted_data['申请时间'] = f"{apply_time[0:4]}年{apply_time[4:6]}月{apply_time[6:8]}日"

            # 处理受理时间
            if '受理时间' in extracted_data:
                accept_time = extracted_data['受理时间']
                if isinstance(accept_time, str) and accept_time.isdigit() and len(accept_time) == 8:
                    extracted_data['受理时间'] = f"{accept_time[0:4]}年{accept_time[4:6]}月{accept_time[6:8]}日"

            if not extracted_data:
                self._set_status('提取审批表数据失败', 'red')
                return

            # 检查必要字段
            required_fields = ['用人单位', '职工姓名', '职工身份证号', '申请时间', '受理时间', '受伤经过', '医疗证明']
            missing_required = []

            for field in required_fields:
                if field not in extracted_data or not extracted_data[field]:
                    missing_required.append(field)

            if missing_required:
                self._set_status(f'审批表缺少必要字段: {missing_required}', 'red')
                return

            self._set_status('审批表数据提取成功', 'green')

            # 5. 准备模板数据
            current_date = _date_now()

            # 使用docxtpl的RichText来设置红色
            from docxtpl import RichText

            template_data = {
                '用人单位': extracted_data.get('用人单位', self.company_pane.currentText().strip()),
                '本人姓名': extracted_data.get('职工姓名', self.get_data('本人姓名', '')),
                '职工性别': extracted_data.get('职工性别', self.get_data('本人性别', '')),
                '本人身份证号': extracted_data.get('职工身份证号', self.get_data('本人身份证号', '')),
                '受伤经过': extracted_data.get('受伤经过', '详见谈话笔录'),
                '当前时期': current_date,
                '当前日期': current_date,
                '当前时间': _time_now(),
                '案本号': self.get_data('案本号', '')
            }

            # 申请时间：如果有就用，没有就用红色的当前日期
            if '申请时间' in extracted_data and extracted_data['申请时间']:
                template_data['申请时间'] = extracted_data['申请时间']
            else:
                rt = RichText()
                rt.add(current_date, color='FF0000')  # 红色
                template_data['申请时间'] = rt

            # 受理时间：如果有就用，没有就用红色的当前日期
            if '受理时间' in extracted_data and extracted_data['受理时间']:
                template_data['受理时间'] = extracted_data['受理时间']
            else:
                rt = RichText()
                rt.add(current_date, color='FF0000')  # 红色
                template_data['受理时间'] = rt

            # 医疗证明：如果有就用，没有就用红色的"详见医疗诊断证明"
            if '医疗证明' in extracted_data and extracted_data['医疗证明']:
                template_data['医疗证明'] = extracted_data['医疗证明']
            else:
                rt = RichText()
                rt.add('详见医疗诊断证明', color='FF0000')  # 红色
                template_data['医疗证明'] = rt

            # ============ 关键修复：在这里检查模板文件并定义 template_path ============
            # 6. 检查模板文件
            template_path = str(path_utils.get_document_template_path('接受谈话通知书（样本）.docx'))

            # 打印哪些字段用了红色
            red_fields = []
            if '申请时间' not in extracted_data or not extracted_data['申请时间']:
                red_fields.append('申请时间')
            if '受理时间' not in extracted_data or not extracted_data['受理时间']:
                red_fields.append('受理时间')
            if '医疗证明' not in extracted_data or not extracted_data['医疗证明']:
                red_fields.append('医疗证明')

            if red_fields:
                print(f"🔴 以下字段使用红色: {red_fields}")

            # 6. 生成文档
            self._set_status('正在生成谈话通知书...', 'black')
            QApplication.processEvents()

            try:
                from docxtpl import DocxTemplate
                word = DocxTemplate(template_path)
                word.render(template_data)

                notice_file_name = f"{person_name}接受谈话通知书.docx"
                target_path = os.path.join(self.current_case_folder, notice_file_name)
                word.save(target_path)

                print(f"✅ 谈话通知书保存到: {target_path}")

                # 打开文件
                success, message = self.file_service.open_document(target_path)
                if success:
                    self._set_status('谈话通知书生成成功', 'green')
                else:
                    self._set_status(f'谈话通知书生成成功，但打开失败: {message}', 'orange')

            except Exception as e:
                logger.error(f"❌ 生成谈话通知书失败: {e}")
                import traceback
                traceback.print_exc()
                self._set_status(f'生成谈话通知书失败: {str(e)}', 'red')

        except Exception as e:
            logger.error(f"❌ 谈话通知书过程异常: {e}")
            import traceback
            traceback.print_exc()
            self._set_status(f'生成谈话通知书异常: {str(e)}', 'red')

    def _cleanup_resources(self):
        """统一清理所有临时资源（对话框、线程等）"""
        resources = ['wait_dialog', 'progress_dialog', 'ai_worker']

        for attr_name in resources:
            if hasattr(self, attr_name):
                try:
                    resource = getattr(self, attr_name)
                    if attr_name == 'ai_worker' and resource.isRunning():
                        resource.terminate()  # 改为terminate
                        resource.wait(5000)  # 等待5秒
                    elif hasattr(resource, 'close'):
                        resource.close()
                except Exception as e:
                    print(f"清理资源 {attr_name} 失败: {e}")
    def _set_status(self, text, color="black", label="label_14"):
        """设置状态标签"""
        label_widget = getattr(self, label, None)
        if not label_widget:
            return

        label_widget.setText(text)
        if color == "green":
            label_widget.setStyleSheet("QLabel{color:green;}")
        elif color == "red":
            label_widget.setStyleSheet("QLabel{color:red;}")
        elif color == "orange":
            label_widget.setStyleSheet("QLabel{color:orange;}")
        else:
            label_widget.setStyleSheet("QLabel{color:black;}")

    def _handle_ai_result(self, result=None, error=None, canceled=False):
        """
        统一处理AI操作结果
        """
        print(f"🔄 处理AI结果: result={result is not None}, error={error}, canceled={canceled}")

        # 添加简单的防重复
        if hasattr(self, '_is_handling_ai_result') and self._is_handling_ai_result:
            logger.warning("⚠️ 已经在处理AI结果，跳过重复调用")
            return

        self._is_handling_ai_result = True

        try:
            # 清理资源
            self._cleanup_resources()

            if canceled:
                print("⏹️ AI操作被用户取消")
                return

            if error:
                logger.error(f"❌ AI操作出错: {error}")
                QMessageBox.critical(self, "AI审查错误", f"操作失败: {error}")
                return

            if result:
                print("✅ AI操作成功，显示结果")
                self.show_ai_review_result(result)
        except Exception as e:
            logger.error(f"❌ 处理AI结果时出错: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 清理标志
            if hasattr(self, '_is_handling_ai_result'):
                delattr(self, '_is_handling_ai_result')

    def _resolve_date_input(self, raw_value: str) -> str:
        """把输入框内容解析为日期字符串；为空时返回系统当前日期（用于 申请/受理时间）"""
        raw = (raw_value or "").strip()
        if not raw:
            return _date_now()
        # 8位纯数字 → YYYY年MM月DD日
        if raw.isdigit() and len(raw) == 8:
            return f"{raw[0:4]}年{raw[4:6]}月{raw[6:8]}日"
        return raw

    def _normalize_compact_time(self, raw_value: str) -> str:
        """受伤/就诊时间归一为 年月日时分 纯数字 YYYYMMDDHHMM；留空返回 ''（不默认当前）"""
        raw = (raw_value or "").strip()
        if not raw:
            return ""
        return "".join(ch for ch in raw if ch.isdigit())

    def _save_date_inputs(self):
        """保存 申请/受理/受伤/就诊 时间到数据模型（申请/受理留空用当前；受伤/就诊留空不填）"""
        if not hasattr(self, 'apply_time_edit') or not hasattr(self, 'accept_time_edit'):
            return
        self.set_data('申请时间', self._resolve_date_input(self.apply_time_edit.text()), 'case')
        self.set_data('受理时间', self._resolve_date_input(self.accept_time_edit.text()), 'case')
        if hasattr(self, 'injury_time_edit'):
            self.set_data('受伤时间', self._normalize_compact_time(self.injury_time_edit.text()), 'case')
        if hasattr(self, 'visit_time_edit'):
            self.set_data('就诊时间', self._normalize_compact_time(self.visit_time_edit.text()), 'case')

    def _load_saved_user_config(self):
        """加载已保存的用户配置到UI"""
        remembered = self.user_manager.get_remembered_user()
        if remembered:
            username = remembered.get("username", "")
            api_key = remembered.get("api_key", "")
            # 填充用户下拉列表
            user_list = self.user_manager.get_user_list()
            self.api_user_combo.addItems(user_list)
            if username:
                idx = self.api_user_combo.findText(username)
                if idx >= 0:
                    self.api_user_combo.setCurrentIndex(idx)
                else:
                    self.api_user_combo.setCurrentText(username)
            if api_key:
                self.api_key_input.setText(api_key)
            print(f"✅ 已加载记住的用户: {username}")
        else:
            # 至少填充用户列表
            user_list = self.user_manager.get_user_list()
            self.api_user_combo.addItems(user_list)
            print("ℹ️ 没有记住的用户")

    def _get_current_username(self) -> str:
        """获取当前用户名"""
        if hasattr(self, 'api_user_combo'):
            return self.api_user_combo.currentText().strip()
        return "未登录用户"

    def _toggle_config_panel(self):
        """展开/收起用户配置面板"""
        visible = self.api_group.isVisible()
        if visible:
            self.api_group.hide()
        else:
            self.api_group.show()
            self.api_group.raise_()  # 置顶，避免被“案件申请陈述”等面板遮挡
        arrow = "▼" if visible else "⚙"
        self.config_toggle_btn.setText(arrow)

    def _update_api_status(self):
        """更新顶栏状态文字（配置面板内不再显示状态/图标）"""
        if not hasattr(self, 'top_status_label'):
            return
        if self.ai_service:
            self.top_status_label.setText("AI 已就绪")
            self.top_status_label.setStyleSheet("color: green; background: transparent; border: none;")
        else:
            username = self._get_current_username()
            if not username or username == "未登录用户":
                msg = "请配置用户名和API密钥"
            else:
                msg = "API密钥未配置，AI 功能不可用"
            self.top_status_label.setText(msg)
            self.top_status_label.setStyleSheet("color: orange; background: transparent; border: none;")

    def _on_api_edited(self):
        """用户/密钥编辑完成后自动保存（无需“保存”按钮）；仅输入改变时才会触发。"""
        username = self.api_user_combo.currentText().strip()
        api_key = self.api_key_input.text().strip()
        if not username:
            return
        try:
            self.user_manager.save_user_config(
                username=username,
                api_url="https://api.deepseek.com",
                api_key=api_key,
                remember_me=True,   # 默认记住；启动自动使用
                service="DeepSeek",
            )
        except Exception as e:
            logger.warning(f"⚠️ 自动保存失败: {e}")
            return
        if self.api_user_combo.findText(username) < 0:
            self.api_user_combo.addItem(username)
        self.data_model.output_config['用户名'] = username
        self.init_ai_service()
        print(f"✅ API配置已自动保存: 用户={username}, 密钥={'已设置' if api_key else '未设置'}")

    def _on_user_combo_changed(self, text):
        """用户名下拉框变化时自动加载对应的API密钥（不触发保存）"""
        if not text or not text.strip():
            return
        username = text.strip()
        user_data = self.user_manager.users_data.get('users', {}).get(username, {})
        if user_data:
            api_key = user_data.get('api_key', '')
            self.api_key_input.setText(api_key)
            if api_key:
                print(f"✅ 已加载用户 '{username}' 的API配置")
            else:
                print(f"ℹ️ 用户 '{username}' 未配置API密钥")

    def init_ai_service(self):
        """初始化AI服务 - 从UserManager读取配置"""
        try:
            # 从UserManager获取当前用户的API配置
            username = self._get_current_username()
            if not username:
                logger.warning("⚠️ 未找到用户配置，AI功能将不可用")
                self.ai_service = None
                self._update_api_status()
                return

            user_data = self.user_manager.users_data.get('users', {}).get(username, {})
            api_key = user_data.get('api_key', '')
            api_url = user_data.get('api_url', 'https://api.deepseek.com')

            # 检查配置是否完整
            if not api_key or not api_url:
                logger.warning("⚠️ API配置不完整，AI功能将不可用")
                print(f"  API地址: {api_url if api_url else '未设置'}")
                print(f"  API密钥: {'已设置' if api_key else '未设置'}")
                self.ai_service = None
                self._update_api_status()
                return

            print(f"✅ 使用用户 '{username}' 的API配置初始化AI服务")
            print(f"  API地址: {api_url}")
            print(f"  API密钥前8位: {api_key[:8]}...")

            # 创建AI服务实例
            self.ai_service = AIService(api_key, api_url)
            print("✅ AI服务初始化成功")
            self._update_api_status()

        except Exception as e:
            logger.error(f"❌ AI服务初始化失败: {e}")
            import traceback
            traceback.print_exc()
            self.ai_service = None
            self._update_api_status()

    def _main_transcript_candidates(self) -> list:
        """案件目录下可作为主询问对象笔录的文件：优先 家属(工亡案)，其次 本人；
        均无则退回第一份非 审批表/告知书/通知书 的谈话笔录。"""
        if not self.current_case_folder or not os.path.isdir(self.current_case_folder):
            return []
        cands, family, main_self = [], [], []
        for file in sorted(os.listdir(self.current_case_folder)):
            if not file.endswith('.docx'):
                continue
            if any(k in file for k in ("审批表", "告知书", "通知书")):
                continue
            cands.append(file)
            if "家属" in file:
                family.append(file)
            elif "本人" in file:
                main_self.append(file)
        return (family or main_self or cands[:1])

    def ai_review_document(self):
        """AI审查文档"""
        try:
            print("=" * 50)
            print("🔄 AI审查开始执行...")

            # 检查AI服务
            if not self.ai_service:
                logger.error("❌ AI服务未初始化")
                QMessageBox.warning(self, "AI审查", "请先配置API密钥。")
                self.init_ai_service()
                return

            print("✅ AI服务检查通过")

            # 检查案件文件夹
            if not self.current_case_folder:
                logger.error("❌ 当前案件文件夹为空")
                QMessageBox.warning(self, "AI审查", "请先保存案件信息。")
                return

            print(f"📁 案件文件夹: {self.current_case_folder}")

            # 查找主询问对象笔录（工亡案=家属，普通案=本人）
            person_files = self._main_transcript_candidates()
            if not person_files:
                logger.error("❌ 未找到可审查的谈话笔录文件")
                QMessageBox.warning(self, "AI审查", "未找到可审查的谈话笔录文件。")
                return

            print(f"✅ 找到{len(person_files)}个谈话笔录文件")

            # 使用第一个找到的主询问对象笔录
            file_path = os.path.join(self.current_case_folder, person_files[0])
            print(f"📄 使用文件路径: {file_path}")

            # 检查文件是否存在
            if not os.path.exists(file_path):
                logger.error(f"❌ 文件不存在")
                QMessageBox.warning(self, "AI审查", f"文件不存在: {file_path}")
                return

            print("✅ 文件存在")

            # ======================
            # 在这里创建进度对话框和AI工作线程
            # ======================

            # 创建进度对话框
            self.progress_dialog = QProgressDialog("正在分析文档...", "取消", 0, 100, self)
            self.progress_dialog.setWindowTitle("AI审查")
            self.progress_dialog.setWindowModality(Qt.WindowModal)

            # 创建AI工作线程（现在file_path已经定义）
            self.ai_worker = AIWorker(self.ai_service, file_path)

            # 连接信号到统一处理方法
            self.ai_worker.finished.connect(
                lambda result: self._handle_ai_result(result=result)
            )
            self.ai_worker.error.connect(
                lambda error: self._handle_ai_result(error=error)
            )
            self.ai_worker.progress.connect(
                lambda msg, value: self.progress_dialog.setLabelText(f"{msg} ({value}%)")
            )

            # 进度对话框取消
            self.progress_dialog.canceled.connect(
                lambda: self._handle_ai_result(canceled=True)
            )

            # 显示进度对话框并启动线程
            self.progress_dialog.show()
            self.ai_worker.start()

        except Exception as e:
            print(f"🔥 整体审查过程异常: {str(e)}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "AI审查错误", f"审查失败: {str(e)}")

    def show_ai_review_result(self, review_result):
        """显示AI审查结果 - 带问题选择功能"""
        print(f"🖥️ 显示审查结果，结果类型: {type(review_result)}")

        # 处理不同的结果格式
        if isinstance(review_result, str):
            result_text = review_result
        elif isinstance(review_result, dict):
            if "结果" in review_result:
                result_text = review_result["结果"]
            elif "错误信息" in review_result:
                result_text = f"错误: {review_result['错误信息']}"
            elif "原始回复" in review_result:
                result_text = review_result["原始回复"]
            else:
                result_text = str(review_result)
        else:
            result_text = str(review_result)

        print(f"📝 要解析的文本长度: {len(result_text)}")

        # 解析AI结果
        parsed_result = self.parse_ai_result(result_text)

        # 创建对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("AI法律审查结果")
        dialog.resize(700, 600)

        layout = QVBoxLayout()

        # 标题
        title = QLabel("AI法律审查报告")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # 创建标签页
        tab_widget = QTabWidget()

        # 标签1：审查结果
        review_tab = QWidget()
        review_layout = QVBoxLayout()

        review_label = QLabel("审查结果分析：")
        review_label.setStyleSheet("font-weight: bold;")
        review_layout.addWidget(review_label)

        # 审查结果显示区域
        review_text_edit = QTextEdit()
        review_text_edit.setReadOnly(True)
        review_text_edit.setPlainText(parsed_result["审查结果"])
        review_text_edit.setMinimumHeight(300)
        review_layout.addWidget(review_text_edit)

        review_tab.setLayout(review_layout)
        tab_widget.addTab(review_tab, "审查结果")

        # 标签2：缺失问题（如果有）
        if parsed_result["缺失问题"]:
            questions_tab = QWidget()
            questions_layout = QVBoxLayout()

            questions_label = QLabel(f"发现 {len(parsed_result['缺失问题'])} 个缺失问题，请勾选需要添加到笔录的问题：")
            questions_label.setStyleSheet("font-weight: bold; color: #e74c3c;")
            questions_layout.addWidget(questions_label)

            # 创建问题列表（带复选框）
            self.question_list_widget = QListWidget()

            for question in parsed_result["缺失问题"]:
                item = QListWidgetItem(question)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)  # 默认未选中
                self.question_list_widget.addItem(item)

            questions_layout.addWidget(self.question_list_widget)

            # 全选/全不选按钮
            select_buttons_layout = QHBoxLayout()

            btn_select_all = QPushButton("全选")
            btn_select_all.clicked.connect(lambda: self.select_all_questions(True))

            btn_select_none = QPushButton("全不选")
            btn_select_none.clicked.connect(lambda: self.select_all_questions(False))

            select_buttons_layout.addWidget(btn_select_all)
            select_buttons_layout.addWidget(btn_select_none)
            select_buttons_layout.addStretch()

            questions_layout.addLayout(select_buttons_layout)

            questions_tab.setLayout(questions_layout)
            tab_widget.addTab(questions_tab, f"缺失问题 ({len(parsed_result['缺失问题'])})")

        layout.addWidget(tab_widget)

        # 按钮区域
        button_layout = QHBoxLayout()

        # 插入到笔录按钮（只在有问题时显示）
        if parsed_result.get("缺失问题"):
            btn_insert = QPushButton("插入选中问题到笔录")
            btn_insert.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
            btn_insert.clicked.connect(lambda: self.insert_selected_questions(dialog))
            button_layout.addWidget(btn_insert)

        btn_copy = QPushButton("复制结果")
        btn_copy.clicked.connect(lambda: self.copy_to_clipboard(parsed_result["审查结果"]))

        btn_save = QPushButton("保存报告")
        btn_save.clicked.connect(lambda: self.save_ai_report(parsed_result))

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dialog.close)

        button_layout.addWidget(btn_copy)
        button_layout.addWidget(btn_save)
        button_layout.addWidget(btn_close)

        layout.addLayout(button_layout)

        dialog.setLayout(layout)

        # 保存解析结果到对话框对象，以便后续使用
        dialog.parsed_result = parsed_result

        dialog.exec_()

    def copy_to_clipboard(self, text, parent=None):
        """复制文本到剪贴板"""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        QMessageBox.information(parent or self, "复制成功", "已复制到剪贴板")

    def _copy_statement(self):
        """复制案件申请陈述"""
        text = self.statement_edit.toPlainText().strip()
        if text:
            self.copy_to_clipboard(text)
        else:
            QMessageBox.information(self, "提示", "案件申请陈述为空")

    def _copy_material(self):
        """复制材料分类"""
        if hasattr(self, 'material_list'):
            self.material_list.copy_to_clipboard()
        QMessageBox.information(self, "提示", "材料列表已复制到剪贴板")

    def save_ai_report(self, parsed_result):
        """保存AI审查报告"""
        if not self.current_case_folder:
            QMessageBox.warning(self, "提示", "请先保存案件信息")
            return

        person_name = self.get_data('本人姓名', '未知')
        timestamp = _timestamp_now()
        filename = f"{person_name}_AI审查报告_{timestamp}.txt"
        filepath = os.path.join(self.current_case_folder, filename)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("=" * 60 + "\n")
                f.write("AI法律审查报告\n")
                f.write("=" * 60 + "\n\n")
                f.write(f"审查时间：{datetime.datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}\n")
                f.write(f"审查对象：{person_name}\n\n")

                f.write("【审查结果】\n")
                f.write(parsed_result.get("审查结果", "无审查结果") + "\n\n")

                if parsed_result.get("缺失问题"):
                    f.write("【缺失问题列表】\n")
                    for i, question in enumerate(parsed_result["缺失问题"], 1):
                        f.write(f"{i}. {question}\n")

            QMessageBox.information(self, "保存成功", f"报告已保存为：\n{filename}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存失败：{str(e)}")

    def generate_injury_notice(self):
        """生成工伤告知书：按案本号找目录 → 检查审批表 → 按JSON结论选模板渲染"""
        try:
            # 1. 读案本号
            case_number = self.lineEdit_2.text().strip()
            if not case_number:
                self._set_status('未找到案本号', 'red')
                QMessageBox.warning(self, "提示", "请先输入或生成案本号")
                return

            # 2. 用案本号找案件文件夹（年份目录 + 老布局都认）
            case_folder = self._case_dir(case_number)
            if not os.path.exists(case_folder):
                self._set_status('未找到案件目录', 'red')
                QMessageBox.warning(self, "提示", f"未找到案本号对应的案件目录：\n{case_folder}")
                return

            # 3. 检查审批表文件
            approval_files = [f for f in os.listdir(case_folder)
                              if f.endswith('.docx') and '审批表' in f]
            if not approval_files:
                self._set_status('目录下没有审批表文件，请先生成案件审批表', 'red')
                QMessageBox.warning(self, "提示", "目录下没有审批表文件，请先生成案件审批表。")
                return

            # 多个审批表：弹窗选择
            selected_file = approval_files[0]
            if len(approval_files) > 1:
                choice, ok = QInputDialog.getItem(self, "选择审批表",
                                                  "目录下有多个审批表，请选择：",
                                                  approval_files, 0, False)
                if not ok or not choice:
                    self._set_status('已取消', 'black')
                    return
                selected_file = choice
            print(f"✅ 使用审批表: {selected_file}")

            # 4. 用 JSON 数据生成字典
            case_obj = self._load_cases_data().get(case_number)
            if not case_obj:
                self._set_status('未找到该案本号的案件数据', 'red')
                QMessageBox.warning(self, "提示", f"未在数据中找到案本号：{case_number}")
                return
            template_data = self._build_notice_template_data(case_obj)

            # 5. 按 JSON 结论选模板
            conclusion = case_obj.get('conclusion', '')
            if conclusion == "不予认定":
                template_name = '不予工伤认定告知书（样本）.docx'
            else:
                template_name = '工伤认定告知书（样本）.docx'
            template_path = str(path_utils.get_document_template_path(template_name))
            if not os.path.exists(template_path):
                self._set_status(f'模板文件不存在: {template_name}', 'red')
                QMessageBox.critical(self, "错误", f"找不到模板文件:\n{template_path}")
                return

            # 6. 渲染
            self._set_status('正在生成工伤告知书...', 'black')
            QApplication.processEvents()
            from docxtpl import DocxTemplate
            word = DocxTemplate(template_path)
            word.render(template_data)

            # 7. 保存 + 打开
            name = case_obj.get('name', '') or '职工'
            if conclusion == "不予认定":
                notice_file_name = f"{name}不予工伤认定告知书.docx"
            else:
                notice_file_name = f"{name}工伤认定告知书.docx"
            target_path = os.path.join(case_folder, notice_file_name)
            word.save(target_path)
            print(f"✅ 工伤告知书保存到: {target_path}")

            success, message = self.file_service.open_document(target_path)
            if success:
                self._set_status('工伤告知书生成成功', 'green')
                QMessageBox.information(self, "成功", f"工伤告知书已生成:\n{notice_file_name}")
            else:
                self._set_status(f'工伤告知书生成成功，但打开失败: {message}', 'orange')
                QMessageBox.information(self, "成功",
                                        f"工伤告知书已生成:\n{notice_file_name}\n\n但打开失败: {message}")

        except Exception as e:
            logger.error(f"❌ 生成工伤告知书失败: {e}")
            import traceback
            traceback.print_exc()
            self._set_status(f'生成工伤告知书失败: {str(e)}', 'red')
            QMessageBox.critical(self, "错误", f"生成工伤告知书失败:\n{str(e)}")

    def _build_notice_template_data(self, case_obj: dict) -> dict:
        """构建工伤告知书模板字典（从案件 JSON 数据）"""
        current_date = _date_now()
        return {
            '本人姓名': case_obj.get('name', ''),
            '本人身份证号': case_obj.get('id_card', ''),
            '用人单位': case_obj.get('labor_unit', ''),  # 用人单位（签合同单位）
            '受伤经过': case_obj.get('injury_process', case_obj.get('injury_description', '详见谈话笔录')),
            '医疗证明': case_obj.get('medical_conclusion', ''),
            '申请时间': case_obj.get('apply_time', current_date),
            '受理时间': case_obj.get('accept_time', current_date),
            '当前时期': current_date + _time_now(),
            '告知日期': current_date,
            '受理编号': case_obj.get('case_id', ''),
            '案本号': case_obj.get('case_id', ''),
            '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
            '本人身份': case_obj.get('identity', DEFAULT_IDENTITY),
            '本人所属表述': _person_affiliation(case_obj),
            '认定依据句': _notice_basis_sentence(
                case_obj, deny=('不予' in str(case_obj.get('conclusion', '')))),
        }

    def _apply_ui_settings(self):
        """应用UI设置"""
        try:
            ui_settings = self.config_service.get_ui_settings()

            # 设置字体
            font = QFont(ui_settings.font_family, ui_settings.font_size)
            self.setFont(font)

            # 注意：窗口大小由 MainWindowUI._apply_window_size 固定为 870×850，
            # 此处不再覆盖
        except Exception as e:
            logger.warning(f"⚠️ 应用UI设置失败: {e}")

    # ========================================================================
    # 统一"人记录"助手（表单 ↔ 英文 schema ↔ 中文兼容扁平键）
    # ========================================================================

    def _read_form_as_person(self) -> Dict[str, Any]:
        """把共享表单控件读成统一人记录（英文 schema，不含 role）

        unit 取「用人单位」控件：本人角色下即案件级用人单位，其余角色下是
        该人自己的工作单位（证人/家属不必与案件用人单位相同）。
        """
        return {
            'name': self.name_pane.text().strip(),
            'gender': self.lineEdit.text().strip(),
            'age': self.age_pane.text().strip(),
            'id_card': self.idnumer_pane.text().strip(),
            'address': self.textEdit.toPlainText().strip(),
            'phone': self.lineEdit_4.text().strip(),
            'position': self.lineEdit_5.text().strip(),
            'identity': (self.identity_edit.text().strip()
                         if hasattr(self, 'identity_edit') else ''),
            'unit': self.company_pane.currentText().strip(),
        }

    def _write_person_to_form(self, person: Dict[str, Any]):
        """把统一人记录（英文 schema）回填共享表单控件（不写数据模型）"""
        def _txt(person, key):
            v = person.get(key)
            return "" if v is None else str(v)

        self.name_pane.setText(_txt(person, 'name'))
        self.lineEdit.setText(_txt(person, 'gender'))
        self.age_pane.setText(_txt(person, 'age'))
        self.idnumer_pane.setText(_txt(person, 'id_card'))
        self.textEdit.setPlainText(_txt(person, 'address'))
        self.lineEdit_4.setText(_txt(person, 'phone'))
        self.lineEdit_5.setText(_txt(person, 'position'))
        if hasattr(self, 'identity_edit'):
            self.identity_edit.setText(_txt(person, 'identity'))
        if hasattr(self, 'company_pane'):
            self._set_combo_or_type(self.company_pane, _txt(person, 'unit'))

    def _person_to_flat(self, role: str, person: Dict[str, Any]):
        """统一人记录 → 兼容扁平中文键（本人姓名/证人姓名/…，经 set_data 入 basic_info）

        空值也要写。表单是该角色数据的唯一来源，跳过空值会让"清空输入框"传不进来，
        旧值残留在扁平键里、下次保存照样写进笔录与文书。
        """
        for field in PERSON_BASE_FIELDS:
            self.set_data(person_flat_key(role, field), person.get(field) or '', 'basic')

    def _person_from_flat(self, role: str) -> Dict[str, Any]:
        """兼容扁平中文键 → 统一人记录（英文 schema，字符串；缺值置空）"""
        return {
            field: str(self.get_data(person_flat_key(role, field), '') or '')
            for field in PERSON_BASE_FIELDS
        }

    def _restore_role_unit(self):
        """把「用人单位」控件回填成*当前角色自己的*单位。

        本人 → 案件级用人单位；证人/法人/家属 → 该人记录里的 unit。
        该控件是共享的，若不用角色自己的值回填，换角色后会串数据。
        """
        if not hasattr(self, 'company_pane'):
            return
        role = self.get_current_role_type()
        if role == "本人":
            self._set_combo_or_type(self.company_pane, self.get_data('用人单位', ''))
        else:
            self._set_combo_or_type(
                self.company_pane, self.get_data(person_flat_key(role, 'unit'), ''))

    def clear_role_fields(self):
        """
        清空当前角色的字段
        """
        role = self.get_current_role_type()

        print(f"🧹 清空{role}字段")

        # 清空输入控件
        self.clear_fields()

        # 清空该角色的数据模型与模板缓存，避免多名证人/人员数据串用
        self._clear_role_data(role)

        # 当角色切换时，更新按钮状态
        self.on_role_changed()

        # 「用人单位」控件按角色回填（本人＝案件级用人单位，跨角色保留；
        # 其余角色＝该人自己的工作单位，上面 _clear_role_data 已清空故为空白待录）
        self._restore_role_unit()

        # 切换回本人时清空案本号，输入新姓名后自动生成
        if role == "本人":
            self.lineEdit_2.clear()

        # 多证人：证人编号行常显（见 ui_main_build._create_witness_widgets），
        # 不再随角色隐藏。
        # 切到证人时加载当前证人；切走时先把表单写回当前证人。
        if role == "证人":
            self._show_witness_ui()
        else:
            self._sync_form_to_current_witness()

    def _clear_role_data(self, role: str):
        """清除指定角色在数据模型与模板字典中的所有数据，确保多人数据一一对应"""
        if role not in ("本人", "证人", "法人", "家属"):
            return
        self.data_model.clear_role_data(role)
        for key in [k for k in list(self._template_dict.keys()) if k.startswith(role)]:
            self._template_dict.pop(key, None)
        self.var_manager.clear_cache()
        # 常显（不再随角色隐藏），位置由 ui_main_build._row_unit_witness() 摆在
        # 单位性质那一行

    def _show_witness_ui(self):
        """切换到证人角色时显示下拉框，并加载当前/首位证人"""
        self.witness_label.show()
        self.witness_combo.show()
        self.add_witness_btn.show()

        if not self.data_model.witnesses:
            if self.witness_combo.count() == 0:
                self.witness_combo.blockSignals(True)
                self.witness_combo.addItem("（暂无证人）")
                self.witness_combo.blockSignals(False)
            return

        if self.data_model.current_witness_index < 0:
            self.data_model.current_witness_index = 0
        self._refresh_witness_combo()
        self._sync_current_witness_to_form()


    def _current_witness(self) -> Optional[Dict[str, Any]]:
        idx = self.data_model.current_witness_index
        if 0 <= idx < len(self.data_model.witnesses):
            return self.data_model.witnesses[idx]
        return None

    def _refresh_witness_combo(self):
        """重建下拉框内容（阻塞信号，避免触发切换逻辑）"""
        self.witness_combo.blockSignals(True)
        self.witness_combo.clear()
        for w in self.data_model.witnesses:
            name = w.get("name") or "未命名"
            self.witness_combo.addItem(f"{w.get('seq', '')} · {name}")
        if 0 <= self.data_model.current_witness_index < self.witness_combo.count():
            self.witness_combo.setCurrentIndex(self.data_model.current_witness_index)
        self.witness_combo.blockSignals(False)

    def _mirror_witness_to_flat(self, w: Dict[str, Any]):
        """把统一证人记录（英文 schema）同步到扁平 证人* 键（供模板/AI 兼容使用）

        空值同样要写，理由见 _person_to_flat：否则清空证人某个字段传不进去。
        """
        for field in PERSON_BASE_FIELDS:
            self.set_data(person_flat_key("证人", field), w.get(field) or '', 'basic')

    def _sync_form_to_current_witness(self):
        """把表单内容写回当前证人，并同步扁平 证人* 键"""
        if self.get_current_role_type() != "证人":
            return
        w = self._current_witness()
        if w is None:
            return

        w.update(self._read_form_as_person())

        self._mirror_witness_to_flat(w)

        # 更新下拉框当前项的显示（不重建，避免选中状态被打乱）
        idx = self.data_model.current_witness_index
        if 0 <= idx < self.witness_combo.count():
            self.witness_combo.blockSignals(True)
            self.witness_combo.setItemText(idx, f"{w.get('seq', '')} · {w.get('name') or '未命名'}")
            self.witness_combo.blockSignals(False)

        self.var_manager.clear_cache()

    def _sync_current_witness_to_form(self):
        """把当前证人数据填入表单 + 扁平 证人* 键"""
        w = self._current_witness()
        if w is None:
            return
        self._write_person_to_form(w)

        self._mirror_witness_to_flat(w)
        self.var_manager.clear_cache()

    def _ensure_current_witness(self):
        """生成证人笔录前调用：确保 current_witness_index 指向一个有效证人"""
        if self.get_current_role_type() != "证人":
            return
        if 0 <= self.data_model.current_witness_index < len(self.data_model.witnesses):
            return
        if self.data_model.witnesses:
            # 已有证人但索引无效（如 F2 填充后未设索引）→ 指向第一个
            self.data_model.current_witness_index = 0
            self._refresh_witness_combo()
            return
        new_index = len(self.data_model.witnesses)
        new_witness = {
            "role": "证人",
            "seq": _witness_label(new_index + 1),
            "name": "", "gender": "", "age": "",
            "id_card": "", "address": "", "phone": "", "position": "",
            "identity": "",
        }
        self.data_model.witnesses.append(new_witness)
        self.data_model.current_witness_index = new_index
        self._refresh_witness_combo()

    def _add_witness(self):
        """添加一个新证人，自动编号为 证人一/证人二/…"""
        self._sync_form_to_current_witness()  # 先保存当前证人的编辑

        new_index = len(self.data_model.witnesses)
        new_witness = {
            "role": "证人",
            "seq": _witness_label(new_index + 1),
            "name": "", "gender": "", "age": "",
            "id_card": "", "address": "", "phone": "", "position": "",
            "identity": "",
        }
        self.data_model.witnesses.append(new_witness)
        self.data_model.current_witness_index = new_index

        self._refresh_witness_combo()
        self._sync_current_witness_to_form()  # 清空表单，准备录入新证人
        self._save_witnesses()

    def _on_witness_selected(self, index):
        """切换选中的证人"""
        if index < 0 or index >= len(self.data_model.witnesses):
            return
        if self.data_model.current_witness_index != index:
            self._sync_form_to_current_witness()  # 保存上一个证人
        self.data_model.current_witness_index = index
        self._sync_current_witness_to_form()

    def _save_witnesses(self):
        """把证人数据写回 cases_data.json 的当前案件（单一数据源，不再单独存证人信息.json）"""
        case_id = (self.current_case_id or '').strip() or self.lineEdit_2.text().strip()
        if not case_id:
            return
        try:
            cases = self._load_cases_data()
            case_obj = cases.get(case_id)
            if case_obj is None:
                return
            case_obj['witnesses'] = list(self.data_model.witnesses)
            self._save_cases_data(cases)
            print(f"✅ 证人信息已保存到案件数据: {len(self.data_model.witnesses)} 位证人")
        except Exception as e:
            print(f"保存证人信息失败: {e}")

    def setup_logging(self):
        """配置统一日志：轮转文件 + 控制台 + 全局异常兜底（幂等）。

        日志落在 数据目录/logs/ 下（程序目录不可写时自动退回临时目录）。
        """
        base = getattr(self, 'DATA_PATH', '') or str(path_utils.get_data_path(""))
        log_utils.setup_logging(os.path.join(base, "logs"))
        log_utils.install_excepthook()
        log_utils.install_qt_message_handler()
        self.log_warning = lambda msg: logger.warning("%s", msg)
        self.log_error = lambda msg: logger.error("%s", msg)
    def on_case_type_changed(self):
        """当案件类型选择改变时调用"""
        is_death_case = self.death_case_checkbox.isChecked()
        is_personal = self.personal_application_checkbox.isChecked()

        self.set_data('案件性质', "工亡案件" if is_death_case else "工伤案件", 'case')
        self.set_data('申请类型', "个人申请" if is_personal else "单位申请", 'case')
        self.update_case_type_hint()

    def update_case_type_hint(self):
        """更新案件类型提示信息"""
        is_death_case = self.death_case_checkbox.isChecked()
        is_personal = self.personal_application_checkbox.isChecked()

        hint_text = "当前案件类型: "
        if is_death_case and is_personal:
            hint_text += "个人申请的工亡案件"
        elif is_death_case:
            hint_text += "单位申请的工亡案件"
        elif is_personal:
            hint_text += "个人申请的工伤案件"
        else:
            hint_text += "单位申请的工伤案件"

        hint_label = self.findChild(QLabel, "labelCaseHint")
        if hint_label:
            hint_label.setText(hint_text)

    def init_combobox_data(self):
        """初始化组合框数据（简化版）"""
        try:
            # 使用 path_utils 的数据路径
            from path_utils import path_utils

            doc_template_dir = path_utils.get_document_template_path("")
            print(f"🔍 文书模板目录: {doc_template_dir}")

            # 用工单位 - 使用文书模板目录
            company_file = str(path_utils.get_document_template_path('用工单位汇总.xlsx'))
            print(f"🔍 用工单位文件: {company_file}")

            if os.path.exists(company_file):
                try:
                    file = pd.read_excel(company_file)
                    self.items_list = file['用工单位汇总'].tolist()
                    print(f"✅ 加载用工单位: {len(self.items_list)}个")
                    if self.items_list:
                        print(f"   示例: {self.items_list[:3]}")
                except Exception as e:
                    logger.error(f"❌ 读取用工单位文件失败: {e}")
                    self.items_list = ['公司A', '公司B', '公司C']  # 默认数据
            else:
                logger.warning("⚠️ 用工单位文件不存在，创建默认文件")
                self.items_list = ['公司A', '公司B', '公司C']
                # 创建默认文件
                try:
                    df = pd.DataFrame(self.items_list, columns=['用工单位汇总'])
                    df.to_excel(company_file, index=False)
                    print(f"✅ 创建默认用工单位文件")
                except Exception as e:
                    logger.error(f"❌ 创建用工单位文件失败: {e}")

            # 用人单位 - 使用文书模板目录
            employer_file = str(path_utils.get_document_template_path('用人单位汇总.xlsx'))
            print(f"🔍 用人单位文件: {employer_file}")

            if os.path.exists(employer_file):
                try:
                    file1 = pd.read_excel(employer_file)
                    self.items_list1 = file1['用人单位汇总'].tolist()
                    print(f"✅ 加载用人单位: {len(self.items_list1)}个")
                except Exception as e:
                    logger.error(f"❌ 读取用人单位文件失败: {e}")
                    self.items_list1 = ['用人单位A', '用人单位B']
            else:
                logger.warning("⚠️ 用人单位文件不存在，创建默认文件")
                self.items_list1 = ['用人单位A', '用人单位B']
                try:
                    df = pd.DataFrame(self.items_list1, columns=['用人单位汇总'])
                    df.to_excel(employer_file, index=False)
                    print(f"✅ 创建默认用人单位文件")
                except Exception as e:
                    logger.error(f"❌ 创建用人单位文件失败: {e}")

            # 工地名称 - 使用文书模板目录
            site_file = str(path_utils.get_document_template_path('工地名称汇总.xlsx'))
            print(f"🔍 工地名称文件: {site_file}")

            if os.path.exists(site_file):
                try:
                    file2 = pd.read_excel(site_file)
                    self.items_list2 = file2['工地名称汇总'].tolist()
                    print(f"✅ 加载工地名称: {len(self.items_list2)}个")
                except Exception as e:
                    logger.error(f"❌ 读取工地名称文件失败: {e}")
                    self.items_list2 = ['工地A', '工地B']
            else:
                logger.warning("⚠️ 工地名称文件不存在，创建默认文件")
                self.items_list2 = ['工地A', '工地B']
                try:
                    df = pd.DataFrame(self.items_list2, columns=['工地名称汇总'])
                    df.to_excel(site_file, index=False)
                    print(f"✅ 创建默认工地名称文件")
                except Exception as e:
                    logger.error(f"❌ 创建工地名称文件失败: {e}")

        except Exception as e:
            logger.error(f"❌ 初始化组合框数据失败: {e}")
            import traceback
            traceback.print_exc()

            # 设置默认数据
            self.items_list = ['公司A', '公司B', '公司C']
            self.items_list1 = ['用人单位A', '用人单位B']
            self.items_list2 = ['工地A', '工地B']
            print("✅ 使用默认数据")

    def on_id_input_finished(self):
        """当身份证输入框完成编辑时自动计算年龄和性别"""
        try:
            role = self.get_current_role_type()
            idcard = self.idnumer_pane.text().strip()

            if not idcard or len(idcard) not in (15, 18):
                return

            self.set_data(f"{role}身份证号", idcard, 'basic')
            self.process_id_info(role)
            self.calculate_age_from_id(role)

        except Exception as e:
            import traceback
            traceback.print_exc()

    def save_company(self):
        """保存用人单位到Excel（company_pane 现为用人单位）"""
        new_item = self.company_pane.currentText().strip()
        if new_item and new_item not in self.items_list1:
            self.items_list1 = self.file_service.save_to_excel(
                "",
                '用人单位汇总.xlsx',
                '用人单位汇总',
                new_item,
                self.items_list1
            )
            self.init_combobox(self.company_pane, self.items_list1)
            print(f"💾 保存用人单位: {new_item}")

    def save_construction_company(self):
        """保存用工单位到Excel（construction_company 现为用工单位）"""
        new_item = self.construction_company.currentText().strip()
        if new_item and new_item not in self.items_list:
            self.items_list = self.file_service.save_to_excel(
                "",
                '用工单位汇总.xlsx',
                '用工单位汇总',
                new_item,
                self.items_list
            )
            self.init_combobox(self.construction_company, self.items_list)
            print(f"💾 保存用工单位: {new_item}")

    def save_construction_plant(self):
        """保存工地名称到Excel"""
        new_item = self.construction_plant.currentText().strip()
        if new_item and new_item not in self.items_list2:
            self.items_list2 = self.file_service.save_to_excel(
                "",  # 空字符串
                '工地名称汇总.xlsx',
                '工地名称汇总',
                new_item,
                self.items_list2
            )
            self.init_combobox(self.construction_plant, self.items_list2)
            print(f"💾 保存工地名称: {new_item}")

    def init_combobox(self, combobox, items):
        """初始化组合框"""
        print(f"🔍 初始化 {combobox.objectName()}，数据长度: {len(items)}")

        combobox.clear()

        if items:
            for item in items:
                combobox.addItem(str(item))
            print(f"✅ 添加了 {len(items)} 个选项")
        else:
            logger.warning("⚠️ 没有数据可添加")
            combobox.addItem("暂无数据")

        combobox.setCurrentIndex(-1)  # 清空选择

        # 设置自动完成
        completer = QCompleter(items)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        combobox.setCompleter(completer)

        print(f"✅ {combobox.objectName()} 初始化完成，当前项数: {combobox.count()}")

    def calculate_age_from_id(self, role):
        """根据身份证号计算年龄（使用DataService）"""
        try:
            idcard = self.get_data(f"{role}身份证号", "")
            if not idcard:
                return

            # 使用DataService计算年龄
            age = self.data_service.calculate_age_from_idcard(idcard)
            if age is None:
                self._set_status('身份证号格式错误', 'red', 'label_12')
                return

            # 设置年龄数据
            self.set_data(f"{role}年龄", age, 'basic')
            self.age_pane.setText(str(age))

            # 超龄检查（仅对本人）
            if role == "本人":
                gender = self.get_data(f"{role}性别", "")
                if (gender == "男" and age > 60) or (gender == "女" and age > 50):
                    self._set_status('此人已经超龄', 'red', 'label_12')
                else:
                    self._set_status('', 'black', 'label_12')   # 常态留空，不写占位文字

        except Exception as e:
            print(f"[calculate_age_from_id] 错误: {e}")
            self._set_status('年龄计算错误', 'red', 'label_12')

    def get_data(self, key: str, default: Any = None) -> Any:
        """统一的数据访问方法"""
        model_value = self._get_from_data_model(key)
        if model_value is not None:
            if key not in self._template_dict or self._template_dict[key] != model_value:
                self._template_dict[key] = model_value
            return model_value

        dict_value = self._template_dict.get(key)
        if dict_value is not None:
            return dict_value

        return default

    def set_data(self, key: str, value: Any, category: str = "auto") -> None:
        """统一的数据设置方法"""
        if value is None:
            value = ""

        if category == "auto":
            category = self._detect_data_category(key)

        self._store_to_data_model(key, value, category)
        self._template_dict[key] = value
        self._handle_special_keys(key, value)

    def _handle_special_keys(self, key: str, value: Any):
        """处理特殊键的同步逻辑"""
        if key == "当前时期":
            if not value:
                current_date = _date_now()
                current_time = _time_now()
                self.set_data(key, f"{current_date}{current_time}", 'output')

    def _detect_data_category(self, key: str) -> str:
        """自动检测数据类别"""
        role_prefixes = ['本人', '证人', '法人', '家属']
        for prefix in role_prefixes:
            if key.startswith(prefix):
                base_key = key[len(prefix):]
                return self._detect_base_category(base_key)
        return self._detect_base_category(key)

    def _detect_base_category(self, key: str) -> str:
        """检测基础键名的类别"""
        if key in ['姓名', '年龄', '性别', '身份证号', '身份证地址', '手机号', '岗位', '职务', '身份']:
            return 'basic'
        elif key in ['用工单位', '用人单位', '工地名称']:
            return 'company'
        elif key in ['案件性质', '申请类型', '案本号', '案件类型', '单位性质']:
            return 'case'
        elif key in ['当前日期', '当前时间', '当前时期']:
            return 'output'
        else:
            return 'investigation'

    def _get_from_data_model(self, key: str) -> Any:
        """从数据模型的各个部分获取数据

        注意：_store_to_data_model 存储时保留完整 key（含角色前缀），
        此处 first check 即可命中，不需要再去前缀查找。
        """
        if key in self.data_model.basic_info:
            return self.data_model.basic_info[key]

        for category in (
            self.data_model.company_info,
            self.data_model.case_info,
            self.data_model.investigation,
            self.data_model.output_config,
        ):
            if key in category:
                return category[key]

        return None

    def _store_to_data_model(self, key: str, value: Any, category: str) -> None:
        """存储数据到数据模型"""
        role_prefixes = ['本人', '证人', '法人', '家属']
        for prefix in role_prefixes:
            if key.startswith(prefix):
                self.data_model.basic_info[key] = value
                return

        category_map = {
            'basic': self.data_model.basic_info,
            'company': self.data_model.company_info,
            'case': self.data_model.case_info,
            'investigation': self.data_model.investigation,
            'output': self.data_model.output_config,
        }

        if category in category_map:
            category_map[category][key] = value
        else:
            self.data_model.basic_info[key] = value

    def clear_fields(self):
        """清空所有输入控件"""
        fields = [
            self.name_pane, self.age_pane, self.lineEdit,
            self.idnumer_pane, self.textEdit, self.lineEdit_4, self.lineEdit_5
        ]
        if hasattr(self, 'identity_edit'):
            fields.append(self.identity_edit)
        for field in fields:
            if isinstance(field, QLineEdit):
                field.clear()
            elif isinstance(field, QTextEdit):
                field.clear()

        # 清空右侧辅助面板
        if hasattr(self, 'statement_edit'):
            self.statement_edit.clear()
        if hasattr(self, 'material_list'):
            self.material_list.clear()

        # 两处提示位都清空：方框常态就是空的，不该写占位文字
        # （左上角已有静态的「信息提示：」标签，再写一遍会重复）
        self._set_status('', 'black', 'label_14')
        self._set_status('', 'black', 'label_12')
    def _read_all_transcripts(self, case_folder: str) -> str:
        """读取案件目录下所有谈话笔录（本人/证人/法人）全文，按文件名分隔"""
        try:
            if not case_folder or not os.path.exists(case_folder):
                return ""
            exclude_kw = ("审批表", "告知书", "通知书", "发送给AI")
            parts = []
            for fname in sorted(os.listdir(case_folder)):
                if not fname.endswith('.docx'):
                    continue
                if any(k in fname for k in exclude_kw):
                    continue
                fpath = os.path.join(case_folder, fname)
                try:
                    doc = Document(fpath)
                    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
                    if text.strip():
                        parts.append(f"=== {fname} ===\n{text}")
                except Exception:
                    continue
            if not parts:
                logger.warning("⚠️ 目录下没有可用的谈话笔录")
                return ""
            print(f"📚 读取全部笔录: {len(parts)} 份")
            return "\n\n".join(parts)
        except Exception as e:
            logger.warning(f"⚠️ 读取全部笔录失败: {e}")
            return ""

    def approve(self):
        """生成案件审批表 — 读取案本号 → JSON查数据 → 渲染模板"""
        try:
            # ── 1. 读取主界面上的案本号 ──
            case_number = self.lineEdit_2.text().strip()
            if not case_number:
                self._set_status('未找到案本号', 'red')
                QMessageBox.warning(self, "提示", "请先输入或生成案本号")
                return

            # ── 2. 在 cases_data.json 里查找对应数据 ──
            case_obj = self._load_cases_data().get(case_number)
            if not case_obj:
                self._set_status('JSON中未找到该案本号', 'red')
                QMessageBox.warning(self, "提示", f"未在数据中找到案本号：{case_number}")
                return
            # 用案件数据构建 case_data（字段名兼容原有索引字段，后面逻辑不变）
            case_data = {
                'case_id': case_obj.get('case_id', ''),
                'person_name': case_obj.get('name', ''),
                'company_name': case_obj.get('labor_unit', ''),
                'applicant_name': case_obj.get('applicant_name', ''),
                'regulation': _regulation_full_for_unit(
                    case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
                    case_obj.get('proposed_article', '')),
                'folder_name': case_obj.get('folder_name', ''),
                'person_gender': case_obj.get('gender', ''),
                'id_card': case_obj.get('id_card', ''),
            }

            person_name = case_data.get('person_name', '')
            company_name = case_data.get('company_name', '')

            # ── 3. 申请人名称（从JSON读取，保存时已按申请类型算好）──
            applicant_name = case_data.get('applicant_name', '')
            self.set_data('申请人名称', applicant_name, 'case')

            # ── 4. 确定案件文件夹（笔录保存在 <BASE_PATH>/<年份>/<案本号>/ 下）──
            case_folder = self._case_dir(case_number)
            if not os.path.exists(case_folder):
                # 兼容旧数据：JSON folder_name 或当前案件文件夹或兜底
                folder_name = case_data.get('folder_name', '')
                if folder_name:
                    case_folder = os.path.join(self.BASE_PATH, folder_name)
                elif self.current_case_folder and os.path.exists(self.current_case_folder):
                    case_folder = self.current_case_folder
                else:
                    case_folder = str(path_utils.get_storage_path(
                        f"{person_name}-工伤案件" if person_name else "未命名案件"
                    ))

            # ── 4.0 检查目录下是否已有审批表文件 ──
            existing_approval = None
            if os.path.isdir(case_folder):
                for fname in sorted(os.listdir(case_folder)):
                    if fname.endswith('.docx') and '审批表' in fname:
                        existing_approval = os.path.join(case_folder, fname)
                        break
            if existing_approval:
                msg = QMessageBox(self)
                msg.setWindowTitle("审批表已存在")
                msg.setText("该案件目录下已有审批表文件，如何处理？")
                view_btn = msg.addButton("查看已有审批表", QMessageBox.AcceptRole)
                new_btn = msg.addButton("新建一个审批表", QMessageBox.ActionRole)
                msg.exec_()
                if msg.clickedButton() == view_btn:
                    success, _ = self.file_service.open_document(existing_approval)
                    if success:
                        self._set_status('已打开已有审批表', 'green')
                    else:
                        self._set_status('打开已有审批表失败', 'orange')
                    return
                # 选择新建：继续走新建流程（文件名会自动加一）

            # ── 4.1 医疗结论 / 受伤经过（AI 分析全部笔录 → 认定/不予认定决策）──
            medical_conclusion = ''
            injury_process = ''
            conclusion = "予以认定"

            if not self.ai_service:
                # 无 AI：提醒并停止（不生成审批表）
                QMessageBox.warning(self, "提示", "未配置AI，无法分析认定/不予认定，不能生成案件审批表。")
                return

            # 解析适用条款情形，供 AI 突出关键证据要素（按规范短名取，15条等全部情形均可用）
            regulation = case_data.get('regulation', '')
            _reg = CaseClassifier.REGULATIONS.get(case_obj.get('proposed_article', ''), {})
            reg_desc = _reg.get('desc', '')
            reg_elements = _reg.get('elements', [])

            # 读取该案全部谈话笔录（本人/证人/法人）
            all_text = self._read_all_transcripts(case_folder)
            if not all_text:
                QMessageBox.warning(self, "提示", "该案件目录下没有找到谈话笔录，无法分析。\n请先生成本人/证人/法人谈话笔录。")
                return

            # AI 分析 → 认定/不予认定 偏向
            self._set_status('正在AI分析笔录...', 'black')
            QApplication.processEvents()
            analysis = self.ai_service.analyze_approval_transcripts(
                all_text,
                case_id=case_number,
                regulation_text=regulation,
                regulation_desc=reg_desc,
                regulation_elements=reg_elements,
            )
            if not analysis:
                QMessageBox.warning(self, "提示", "AI 分析失败，请重试。")
                return

            # 展示对话框（认定工伤 / 不予认定工伤 / 保存）
            dlg = ApprovalDecisionDialog(analysis, self)
            dlg.exec_()
            choice = dlg.get_choice()

            if choice == "保存":
                # 保存分析结果到 JSON，不生成审批表
                self._update_case_field(case_number,
                                        analysis_result=analysis.get("分析", ""),
                                        conclusion_bias=analysis.get("偏向", ""))
                self._set_status('已保存 AI 分析结果', 'green')
                return

            # 生成「调查核实情况」段落（受伤经过）
            self._set_status('正在AI生成调查核实情况...', 'black')
            QApplication.processEvents()
            gen_result = self.ai_service.generate_injury_and_conclusion(
                all_text,
                regulation_text=regulation,
                regulation_desc=reg_desc,
                regulation_elements=reg_elements,
            )
            if gen_result:
                injury_process = gen_result.get("受伤经过", "") or ""
                medical_conclusion = gen_result.get("诊断结论", "") or ""
            if not injury_process:
                QMessageBox.warning(self, "提示", "生成调查核实情况失败，请重试。")
                return

            if choice == "不予认定":
                # 不予认定：在调查核实后面追加关键理由
                reasons = analysis.get("关键理由", []) or []
                if reasons:
                    injury_process += "\n不予认定的关键理由：\n" + "\n".join(
                        f"{i + 1}. {r}" for i, r in enumerate(reasons))
                conclusion = "不予认定"
            else:
                conclusion = "予以认定"

            # 回写 JSON：受伤经过 / 诊断结论 / 结论
            self.set_data('本人受伤经过', injury_process, 'investigation')
            self.set_data('认定结论', conclusion, 'case')
            self._update_case_field(case_number,
                                    injury_process=injury_process,
                                    medical_conclusion=medical_conclusion,
                                    conclusion=conclusion)

            # 医疗结论写入数据模型
            if medical_conclusion:
                self.data_model.investigation['医院诊断'] = medical_conclusion
                self.data_model.investigation['医疗结论'] = medical_conclusion

            # ── 4.2 用JSON数据构建模板变量（{{受伤经过}}先不替换）──
            template_data = {
                '用人单位': company_name,
                '申请人名称': applicant_name,
                '本人姓名': person_name,
                '本人性别': case_data.get('person_gender', ''),
                '本人身份证号': case_data.get('id_card', ''),
                '受伤经过': injury_process,
                '医疗结论': medical_conclusion,
                '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
                '引用条例': case_data.get('regulation', ''),
                '申请时间': self._resolve_date_input(self.apply_time_edit.text()),
                '受理时间': self._resolve_date_input(self.accept_time_edit.text()),
            }

            # ── 5. 获取模板路径 ──
            template_path = str(path_utils.get_document_template_path(
                '工伤案件审批表（模板）.docx'
            ))
            if not os.path.exists(template_path):
                self._set_status('模板文件不存在', 'red')
                QMessageBox.critical(self, "错误",
                    f"找不到模板文件:\n{template_path}")
                return

            # ── 5. 预处理模板：根据结论在「认定工伤/不予认定工伤」方框打勾 ──
            import tempfile
            import shutil
            temp_template = os.path.join(tempfile.gettempdir(), '_temp_approval_table.docx')
            shutil.copy2(template_path, temp_template)

            from docx import Document as DocxEditor
            doc_edit = DocxEditor(temp_template)
            check_confirm = (conclusion == "予以认定")
            for table in doc_edit.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for p in cell.paragraphs:
                            if check_confirm and "□认定工伤" in p.text:
                                full = p.text.replace("□认定工伤", "☑认定工伤", 1)
                            elif (not check_confirm) and "□不予认定工伤" in p.text:
                                full = p.text.replace("□不予认定工伤", "☑不予认定工伤", 1)
                            else:
                                continue
                            if p.runs:
                                p.runs[0].text = full
                                for r in p.runs[1:]:
                                    r.text = ""
                            break
            doc_edit.save(temp_template)

            # ── 6. 渲染模板（仅替换带 {{}} 外框的占位符）──
            word = DocxTemplate(temp_template)
            word.render(template_data)

            # ── 6. 保存 ──
            os.makedirs(case_folder, exist_ok=True)

            file_name = f"{person_name}案件审批表.docx" if person_name else "案件审批表.docx"
            target_path = os.path.join(case_folder, file_name)
            counter = 2
            while os.path.exists(target_path):
                file_name = f"{person_name}案件审批表({counter}).docx"
                target_path = os.path.join(case_folder, file_name)
                counter += 1

            word.save(target_path)
            print(f"✅ 案件审批表已保存: {target_path}")

            # ── 清理临时模板 ──
            try:
                os.remove(temp_template)
            except Exception:
                pass

            # ── 9. 打开 ──
            success, message = self.file_service.open_document(target_path)
            if success:
                self._set_status('案件审批表生成成功', 'green')
            else:
                self._set_status(f'审批表已生成，打开失败: {message}', 'orange')

        except Exception as e:
            logger.error(f"❌ 生成审批表异常: {e}")
            import traceback
            traceback.print_exc()
            self._set_status(f'生成审批表失败: {str(e)}', 'red')

    def _on_name_pane_changed(self):
        """当 name_pane 输入完成时，如果是本人角色，自动生成案本号"""
        try:
            role = self.get_current_role_type()
            if role != "本人":
                return
            name = self.name_pane.text().strip()
            if not name:
                return
            # 只有当前没有案本号时才自动生成（避免覆盖用户手动编辑）
            current = self.lineEdit_2.text().strip()
            if not current:
                id_card = self.idnumer_pane.text().strip()
                case_num = self._auto_generate_case_number(name, id_card)
                self.lineEdit_2.setText(case_num)
                self.set_data('案本号', case_num, 'case')
        except Exception as e:
            print(f"自动生成案本号失败: {e}")

    def _auto_generate_case_number(self, person_name: str, id_card: str = "") -> str:
        """根据本人姓名和身份证后四位自动生成案本号"""
        is_death = self.death_case_checkbox.isChecked()
        prefix = "工亡" if is_death else "案本"
        date = datetime.datetime.now().strftime("%Y%m%d")
        id_last4 = id_card[-4:] if id_card and len(id_card) >= 4 else "xxxx"
        return f"{person_name}-{prefix}{date}{id_last4}"
    def init_comboboxes(self):
        """初始化所有组合框"""
        self.init_combobox(self.company_pane, self.items_list1)
        self.init_combobox(self.construction_company, self.items_list)
        self.init_combobox(self.construction_plant, self.items_list2)

        self.company_pane.setCurrentIndex(-1)
        self.construction_company.setCurrentIndex(-1)
        self.construction_plant.setCurrentIndex(-1)

    def company(self):
        """「用人单位」控件变化时同步数据。

        本人角色 → 案件级用人单位（全案文书共用）；其余角色 → 该人记录里的 unit。
        证人/家属的工作单位不必与案件用人单位相同，故不能一律写入案件级。
        """
        name = self.company_pane.currentText().strip()
        try:
            role = self.get_current_role_type()
        except Exception:
            role = "本人"
        if role == "本人":
            self.set_data('用人单位', name, 'company')
        else:
            self.set_data(person_flat_key(role, 'unit'), name, 'basic')

    def sync_employer_to_dict(self):
        """更新用工单位信息（construction_company 现为用工单位）"""
        company_name = self.construction_company.currentText().strip()
        self.set_data('用工单位', company_name, 'company')

    def c_plant(self):
        """更新工地名称信息"""
        site_name = self.construction_plant.currentText().strip()
        self.set_data('工地名称', site_name, 'company')

    def id_clicked(self):
        """读取身份证信息"""
        try:
            dll = windll.LoadLibrary("./sdtapi.dll")
            port = c_int32(1001)
            ifopen = c_int32(1)
            pucManaInfo = create_string_buffer(4)
            pucManaMsg = create_string_buffer(8)
            dll.SDT_StartFindIDCard(port, pucManaInfo, ifopen)
            dll.SDT_SelectIDCard(port, pucManaMsg, ifopen)
            pucCHMsg = create_unicode_buffer(256)
            pucPHMsg = create_string_buffer(1024)
            puiCHMsgLen = c_uint(0)
            puiPHMsgLen = c_uint(0)
            ret = dll.SDT_ReadBaseMsg(port, pucCHMsg, byref(puiCHMsgLen), pucPHMsg,
                                      byref(puiPHMsgLen), ifopen)
            if ret == 65:
                return
            dll.SDT_ClosePort(port)
            self.set_data('当前时期', _date_now() + _time_now(), 'output')

            role = self.get_current_role_type()
            self.process_id(pucCHMsg, role)

        except Exception as e:
            print(f"读取身份证信息时出错: {str(e)}")

    def process_id(self, pucCHMsg, role):
        """处理身份证信息"""
        try:
            name = pucCHMsg.value[0:15].strip()
            self.data_model.update_basic_info(role, {'姓名': name})
            self.set_data(f"{role}姓名", name, 'basic')
            self.name_pane.setText(name)

            if len(pucCHMsg.value) >= 79:
                id_number = pucCHMsg.value[61:79].strip()
                self.data_model.update_basic_info(role, {'身份证号': id_number})
                self.set_data(f"{role}身份证号", id_number, 'basic')
                self.idnumer_pane.setText(id_number)

            if len(pucCHMsg.value) >= 61:
                address = pucCHMsg.value[26:61].strip()
                self.data_model.update_basic_info(role, {'身份证地址': address})
                self.set_data(f"{role}身份证地址", address, 'basic')
                self.textEdit.setText(address)

            self.process_id_info(role)
            self.calculate_age_from_id(role)

        except Exception as e:
            import traceback
            traceback.print_exc()

    def update_role_info(self, role):
        """按角色把共享表单内容写入兼容扁平键（统一人记录 schema），并处理角色化副作用"""
        try:
            self._save_date_inputs()
            person = self._read_form_as_person()
            self._person_to_flat(role, person)

            if role == "本人":
                # 本人的「单位」即案件级用人单位（其余角色只写各自的 单位名称）
                self.set_data('用人单位', person.get('unit', ''), 'company')
                # 自动生成案本号
                current_case = self.lineEdit_2.text().strip()
                if not current_case:
                    case_num = self._auto_generate_case_number(
                        person.get('name', ''), person.get('id_card', '')
                    )
                    self.lineEdit_2.setText(case_num)
                    self.set_data('案本号', case_num, 'case')

            if role == "法人":
                company_name = self.get_data('用工单位', '')
                if not company_name:
                    company_name = self.construction_company.currentText().strip()
                    if company_name:
                        self.set_data('用工单位', company_name, 'company')

        except Exception as e:
            import traceback
            traceback.print_exc()
    def process_id_info(self, role):
        """处理身份证信息并更新性别显示（使用DataService）"""
        try:
            idcard = self.get_data(f"{role}身份证号", "")
            if not idcard:
                return

            # 使用DataService提取性别
            gender = self.data_service.extract_gender_from_idcard(idcard)
            if gender is None:
                print(f"[process_id_info] 无法从身份证提取性别: {idcard}")
                return

            # 设置性别数据
            self.set_data(f"{role}性别", gender, 'basic')
            self.lineEdit.setText(gender)

        except Exception as e:
            print(f"[process_id_info] 错误: {e}")
            import traceback
            traceback.print_exc()

    def get_current_role_type(self) -> str:
        """获取当前选中的角色类型"""
        if self.radioButton.isChecked():
            return "本人"
        elif self.radioButton_2.isChecked():
            return "证人"
        elif self.radioButton_3.isChecked():
            return "法人"
        elif hasattr(self, 'radioButton_4') and self.radioButton_4.isChecked():
            return "家属"
        return "本人"
    def smart_search_cases(self):
        """按案本号在 cases_data.json 中模糊搜索，并回填主界面"""
        try:
            keyword = self.lineEdit_2.text().strip()
            if not keyword:
                keyword = self.get_data("本人姓名", "")
            if not keyword:
                keyword = self.name_pane.text().strip()
            if not keyword:
                QMessageBox.warning(self, "提示", "请输入案本号进行搜索")
                return

            cases = self._load_cases_data()  # {case_id: case_obj}
            if not cases:
                QMessageBox.information(self, "提示", "还没有保存任何案件数据")
                return

            # 模糊匹配：案本号包含关键字
            kw = keyword.lower()
            matched = [(cid, obj) for cid, obj in cases.items()
                       if kw in str(cid).lower()]

            if not matched:
                QMessageBox.information(self, "提示", f"未找到案本号包含「{keyword}」的案件")
                return

            if len(matched) == 1:
                self._load_case_to_form(matched[0][1])
            else:
                self._show_case_search_dialog(matched)

        except Exception as e:
            QMessageBox.critical(self, "错误", f"搜索失败: {str(e)}")
            import traceback
            traceback.print_exc()

    def _load_case_to_form(self, case_obj: Dict[str, Any]):
        """把单个案件 JSON 回填主界面与数据模型"""
        # 切回本人角色再回填
        self.radioButton.setChecked(True)
        self.clear_role_fields()
        self._apply_case_object(case_obj)
        # 恢复该案件的证人列表到内存（统一人记录 schema），供后续切换/生成使用
        self.data_model.witnesses = [dict(w) for w in case_obj.get('witnesses', [])]
        self.data_model.current_witness_index = -1
        if hasattr(self, 'witness_combo'):
            self._refresh_witness_combo()
        self.current_case_id = str(case_obj.get('case_id', ''))
        self._set_status(f"已加载案件：{self.current_case_id}", 'green')
        QMessageBox.information(
            self, "加载成功",
            f"已加载案件数据：\n案本号：{self.current_case_id}\n姓名：{case_obj.get('name', '')}"
        )

    def _show_case_search_dialog(self, matched):
        """弹出窗口列出匹配案件供选择"""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"选择案件（{len(matched)} 条）")
        dialog.resize(720, 460)

        layout = QVBoxLayout()
        title = QLabel(f"找到 {len(matched)} 条匹配案件，请选择：")
        layout.addWidget(title)

        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(['案本号', '姓名', '案件性质', '申请类型', '用人单位', '拟用条例'])
        table.setRowCount(len(matched))
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)

        for i, (case_id, case_obj) in enumerate(matched):
            table.setItem(i, 0, QTableWidgetItem(str(case_id)))
            table.setItem(i, 1, QTableWidgetItem(str(case_obj.get('name', ''))))
            table.setItem(i, 2, QTableWidgetItem(str(case_obj.get('case_nature', ''))))
            table.setItem(i, 3, QTableWidgetItem(str(case_obj.get('applicant_type', ''))))
            table.setItem(i, 4, QTableWidgetItem(str(case_obj.get('labor_unit', ''))))
            table.setItem(i, 5, QTableWidgetItem(str(case_obj.get('proposed_article', ''))))

        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        if table.rowCount() > 0:
            table.selectRow(0)
        layout.addWidget(table)

        # 双击直接选择
        table.cellDoubleClicked.connect(
            lambda r, c: self._on_case_search_selected(matched, r, dialog)
        )

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        select_btn = QPushButton("选择")
        select_btn.clicked.connect(
            lambda: self._on_case_search_selected(matched, table.currentRow(), dialog)
        )
        btn_layout.addWidget(select_btn)
        layout.addLayout(btn_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def _on_case_search_selected(self, matched, row: int, dialog: QDialog):
        """用户选中某条案件后的处理"""
        if row < 0 or row >= len(matched):
            QMessageBox.warning(dialog, "提示", "请先选择一行")
            return
        case_obj = matched[row][1]
        dialog.accept()
        self._load_case_to_form(case_obj)



if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())