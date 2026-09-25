# -*- coding: utf-8 -*-
"""
条例与证据清单（单一事实源）
供拟用条例下拉、案件法律要件、证据清单、案件审批表、告知书使用。

**条例目录**：键 = 规范短名（如 第十四条第（一）项），即 case_obj.proposed_article
存储格式。每项：
- text:     条例全称（《工伤保险条例》第X条第一款第X项）
- desc:     法条原文（用于审批表 / AI 分析）
- elements: 法律要件清单（案件 JSON 的 proposed_article_elements、AI 追问依据）
- evidence: 该条例**特有**的书面证据

  required = 必要（缺了办不下去），possible = 可能（视案情而定）。
  只列该条例特有的——所有工伤申请通用的《条例》第18条材料
  （身份证复印件 / 劳动合同 / 医院诊断证明书）不在这里，见本模块 compose_evidence 的 L1。
  工亡、个人申请、家属代为申请还有各自的追加项，也在那里叠加。

注：第十四条第（七）项（法律行政法规规定的其他情形）实际极少碰到，已整体移除
（目录、下拉框、AI 判定用的对照表都去掉了）。

**其余内容**（2026-09 从 app_main.py 搬来）：单位性质/身份常量、条例格式互转、
书面证据清单 `compose_evidence`、以及由它们派生的表述句。两个模块原先一个装
目录、一个装用法，现在合在一处。
"""

import re
from typing import List, Tuple


class CaseClassifier:
    """条例目录：按规范短名有序存放（插入顺序即下拉框顺序）"""

    REGULATIONS = {
        "第十四条第（一）项": {
            "text": "《工伤保险条例》第十四条第一款第一项",
            "desc": "在工作时间和工作场所内，因工作原因受到事故伤害",
            "elements": ["工作时间", "工作场所", "因工作原因受到事故伤害"],
            "evidence": {
                "required": [],
                "possible": ["工资发放记录", "考勤记录", "证人证言",
                             "事故现场照片", "监控录像", "病历资料"],
            },
        },
        "第十四条第（二）项": {
            "text": "《工伤保险条例》第十四条第一款第二项",
            "desc": "工作时间前后在工作场所内，从事与工作有关的预备性或收尾性工作受到事故伤害",
            "elements": ["工作时间前后", "工作场所内", "从事与工作有关的预备性或收尾性工作", "受到事故伤害"],
            "evidence": {
                "required": [],
                "possible": ["考勤记录", "工资发放记录", "证人证言",
                             "派工单或工作安排记录", "事故现场照片", "监控录像"],
            },
        },
        "第十四条第（三）项": {
            "text": "《工伤保险条例》第十四条第一款第三项",
            "desc": "在工作时间和工作场所内，因履行工作职责受到暴力等意外伤害",
            "elements": ["工作时间", "工作场所内", "因履行工作职责", "受到暴力等意外伤害"],
            "evidence": {
                "required": ["公安报案回执"],   # 暴力伤害的成立前提
                "possible": ["证人证言", "监控录像", "公安处理文书（行政处罚/调解书）",
                             "事故现场照片", "病历资料"],
            },
        },
        "第十四条第（四）项": {
            "text": "《工伤保险条例》第十四条第一款第四项",
            "desc": "患职业病",
            "elements": ["患职业病（须符合国家职业病目录）"],
            "evidence": {
                "required": ["职业病诊断证明"],
                "possible": ["职业史与职业病危害接触史证明", "工作场所职业病危害因素检测报告",
                             "职业健康监护档案", "工资发放记录", "考勤记录"],
            },
        },
        "第十四条第（五）项": {
            "text": "《工伤保险条例》第十四条第一款第五项",
            "desc": "因工外出期间，由于工作原因受到伤害或发生事故下落不明",
            "elements": ["因工外出期间", "由于工作原因受到伤害 / 发生事故下落不明"],
            "evidence": {
                "required": [],
                "possible": ["因工外出证明（出差审批单/派工单）", "交通票据",
                             "事故证明（公安或交管出具）", "下落不明的公安证明",
                             "证人证言", "病历资料"],
            },
        },
        "第十四条第（六）项": {
            "text": "《工伤保险条例》第十四条第一款第六项",
            "desc": "在上下班途中，受到非本人主要责任的交通事故或城市轨道交通、客运轮渡、火车事故伤害",
            "elements": ["上下班途中", "非本人主要责任", "交通事故（含轨道交通、客运轮渡、火车事故）"],
            "evidence": {
                "required": ["道路交通事故认定书"],   # 「非本人主要责任」靠它认定
                "possible": ["上下班路线与时间合理性证明（居住证明/考勤）", "交通票据",
                             "证人证言", "监控录像", "病历资料"],
            },
        },
        "第十五条第（一）项": {
            "text": "《工伤保险条例》第十五条第一款第一项",
            "desc": "在工作时间和工作岗位，突发疾病死亡或者在48小时之内经抢救无效死亡",
            "elements": ["工作时间", "工作岗位", "突发疾病死亡 / 48小时内经抢救无效死亡"],
            "evidence": {
                # 48 小时时限靠抢救病历上的时间记录
                "required": ["死亡证明", "抢救病历（含抢救时间记录）"],
                "possible": ["考勤记录", "证人证言", "监控录像",
                             "医疗机构出具的发病与抢救过程说明"],
            },
        },
        "第十五条第（二）项": {
            "text": "《工伤保险条例》第十五条第一款第二项",
            "desc": "在抢险救灾等维护国家利益、公共利益活动中受到伤害",
            "elements": ["在抢险救灾等维护国家利益、公共利益活动中受到伤害"],
            "evidence": {
                "required": [],
                "possible": ["县级以上政府或相关部门的表彰/认定文件", "活动组织证明",
                             "证人证言", "事故现场照片", "病历资料"],
            },
        },
        "第十五条第（三）项": {
            "text": "《工伤保险条例》第十五条第一款第三项",
            "desc": "职工原在军队服役，因战、因公负伤致残，已取得革命伤残军人证，到用人单位后旧伤复发",
            "elements": ["原在军队服役", "因战/因公负伤致残", "已取得革命伤残军人证", "到用人单位后旧伤复发"],
            "evidence": {
                "required": ["革命伤残军人证", "旧伤复发的医疗机构诊断证明"],
                "possible": ["退役军人事务部门出具的负伤性质证明",
                             "服役期间病历", "既往伤残等级鉴定材料"],
            },
        },
    }


# ============================================================================
# 单位性质 / 身份（案件级）
# ============================================================================

# 用人单位性质（企业 / 机关（公务员） / 事业单位），默认企业
UNIT_TYPES = ["企业", "机关（公务员）", "事业单位"]
DEFAULT_UNIT_TYPE = "企业"
DEFAULT_IDENTITY = "职工"

# 单位性质 → 该单位人员的中文称谓（用于给 AI 的「称谓提示」）。
# 下拉框的标签（如「机关（公务员）」）带括号，不能直接拼进句子里，故单独映射；企业档不提示。
UNIT_TYPE_APPELLATION = {
    "机关（公务员）": "机关工作人员",
    "事业单位": "事业单位工作人员",
}


def person_pronoun(gender: str) -> str:
    """人记录里的 gender → 第三人称代词（笔录问答里「他/她」）。

    只认「女」为「她」，其余一律「他」——性别没填时用「他」这个默认写法，
    不要为空白再造一个「TA」之类的中性词，笔录里不兴那么写。
    """
    return "她" if str(gender or "").strip() == "女" else "他"


# 单位性质 → **单位本身**的称谓。注意和上面的 UNIT_TYPE_APPELLATION 不是一回事：
# 那是**人**的称谓（机关工作人员），这是把「你们公司/你们单位」填对。
# 只有企业说「公司」；机关、事业单位（幼儿园、医院、学校）都说「单位」——
# 样本里幼儿园案件写的正是「你们单位员工工作时间是怎么安排的」。
# 未知/空值也回退到「单位」：叫「单位」在任何性质下都不算错。
UNIT_APPELLATION = {"企业": "公司"}


def unit_appellation(unit_type: str) -> str:
    """单位性质 → 单位称谓（「公司」/「单位」），用于笔录问答的措辞。"""
    return UNIT_APPELLATION.get(str(unit_type or "").strip(), "单位")


# ============================================================================
# 拟用条例 选项与格式互转
# ============================================================================

# 从条例目录派生：顺序 = 下拉框顺序；要素与 CaseClassifier 同源
REGULATION_CATALOG = CaseClassifier.REGULATIONS
REGULATION_OPTIONS = list(REGULATION_CATALOG.keys())
REGULATION_ELEMENTS = {
    key: list(reg.get('elements', []))
    for key, reg in REGULATION_CATALOG.items()
}


def regulation_elements(short: str) -> list:
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
#
# 名字按**实际收件的口径**写，不按法条的通用名：
# 实际收的都是复印件（原件与复印件的比对在收件时就做完了），
# 所以「身份证复印件」才是要归档的那份证据；写「身份证」的话，
# 案卷里录的「身份证复印件」和这里算出来的「身份证」会被当成两样，
# 材料面板上出现两条（一条"该收"、一条"已收到"）。
# 常见的其它叫法见下面的 EVIDENCE_ALIASES，那层是兜底的。
_BASE_EVIDENCE_REQUIRED = ("身份证复印件", "劳动合同", "医院诊断证明书")

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


# 同一份证据的其它常见叫法。键 = 上面的规范名，值 = 见过的别的叫法。
#
# 为什么需要：材料面板上有一半的行是「按条例算出来的该收项」，另一半是
# 「案卷里存的实收项」（见 material_list.apply_evidence_list），两边**靠名字联结**。
# 案卷里的材料名是手输的，叫法一岔开，同一份东西就会被当成两样、列成两条。
# 这层只兜常见的变体；**精确比对，不做子串匹配**——子串会让
# 「死亡证明」和「死亡原因证明」这种不同的东西被合掉。
EVIDENCE_ALIASES = {
    "身份证复印件": ("身份证", "身份证复件", "身份证正反面复印件", "身份证正反复印件"),
    "医院诊断证明书": ("医院诊断证明", "医疗诊断证明", "医疗诊断证明书",
                       "诊断证明", "诊断证明书"),
    "劳动合同": ("劳动合同书", "劳动合同复印件"),
    "死亡证明": ("死亡医学证明", "死亡医学证明书", "死亡证明书"),
    "公安报案回执": ("报案回执", "受案回执", "公安受案回执"),
    "道路交通事故认定书": ("交通事故认定书", "事故认定书", "交通责任认定书"),
    "职业病诊断证明": ("职业病诊断证明书",),
    "抢救病历（含抢救时间记录）": ("抢救病历",),
    "近亲属关系证明（户口簿/结婚证等）": ("近亲属关系证明",),
    "因工外出证明（出差审批单/派工单）": ("因工外出证明",),
    "上下班路线与时间合理性证明（居住证明/考勤）": ("上下班路线与时间合理性证明",),
    "工作场所职业病危害因素检测报告": ("职业病危害因素检测报告",),
}


def _norm(name) -> str:
    """比对用的形态：去掉首尾与中间的空白（全角半角都去）"""
    return str(name or "").strip().replace(" ", "").replace("　", "")


def evidence_key(name) -> str:
    """材料名 → 比对用的键：认得出别名就归到规范名，认不出就用原名（去空白）。

    材料面板靠它判断「这条案卷里已经有了」——见 material_list.apply_evidence_list。
    """
    n = _norm(name)
    if not n:
        return ""
    for canon, aliases in EVIDENCE_ALIASES.items():
        if n == _norm(canon) or n in {_norm(a) for a in aliases}:
            return canon
    return n


# 各条例的「核实要点」：不是要收的书面材料，而是**必须问清楚的事实**。
#
# 它和证据一样进材料面板（那个面板实际上就是"这个案子要备什么、要核什么"的清单），
# 但渲染提示词时走 `{{核实要点}}` 那个占位符，不混进 `{{已提供材料}}`。
#
# 写成**问句形态**，一眼分得出不是材料。
#: 多数条例都要核的两条——**写在需要的条例里，不自动追加**：
#: 有的条例不需要（如第十四条第（一）项，见下）。
_COMMON_CHECKS = (
    "是否参加工伤保险？",     # 影响待遇由谁支付
    "事发前是否饮酒？",       # 第十六条：醉酒不予认定
)

REGULATION_CHECKS = {
    # 第十四条第（一）项不列核实要点：「三工」要件本身已经够明确，
    # 通用的参保/饮酒两条也一并不要（用户定的）
    "第十四条第（一）项": (),

    # 这一项是**二选一的两个选项**，写成陈述式：面板上勾哪个就是哪个。
    # 提示词里只出勾了的那条（见 transcripts），不勾的不出现——
    # 两条都发的话 AI 会先问一种、再问另一种，而案子只可能是其中一种，
    # 于是冒出「你收工后是否也需要做收尾工作？」这种没意义的问题。
    "第十四条第（二）项": ("开工前的准备工作", "收工后的收尾工作"),
    # 第十四条第（三）项不列核实要点（用户定的）：冲突起因、参保、饮酒都一并不要
    "第十四条第（三）项": (),
    "第十四条第（四）项": ("接触有害因素的工种与年限？",
                           "是否已由职业病诊断机构确诊？",
                           ) + _COMMON_CHECKS,
    "第十四条第（五）项": ("外出是否因工（谁安排、办什么事）？",
                           "受伤时是否正在办理该事务？",
                           ) + _COMMON_CHECKS,
    "第十四条第（六）项": ("是上班途中还是下班途中？",
                           "上下班路线是否合理（有无绕道办私事）？",
                           ) + _COMMON_CHECKS,
    "第十五条第（二）项": ("参加的是什么活动、由谁组织或号召？",
                           ) + _COMMON_CHECKS,
    "第十五条第（三）项": ("服役期间负伤是否因战/因公、有无革命伤残军人证？",
                           "这次为何属于旧伤复发？",
                           ) + _COMMON_CHECKS,
}


def compose_checks(regulation_short: str) -> List[str]:
    """返回该条例要核实的「事实要点」（问句）；表里没写（或写空的）就返回空列表。

    与 `compose_evidence` 分开：两者的去处不同——证据进 `{{已提供材料}}`，
    核实要点进 `{{核实要点}}`（虽然都显示在材料面板上）。
    """
    return list(REGULATION_CHECKS.get((regulation_short or "").strip(), ()))


# ============================================================================
# 证人笔录：按拟用条例追加的问句
# ----------------------------------------------------------------------------
# 用途和上面的 REGULATION_CHECKS 不同，别混：
#   REGULATION_CHECKS  → 材料面板上「待核事实」的条目名（名词短语/简称都有）
#   WITNESS_EXTRA_QUESTIONS → **能直接印在证人笔录上**的完整问句
# 所以这张表里全是问句、且带占位符（要填被询问人姓名、按性别选代词）。
#
# 插在证人笔录固定骨架的第 6 问之后（详细经过问完，接着追该条例特有的事实）。
# 表里没写的条例（如第十四条第（一）项）不追加——「三工」要件骨架里已经问到了。
#
# 占位符（用 str.format，见 compose_witness_questions）：
#   {本人} 受伤职工姓名   {他} 受伤职工的代词（他/她）   {单位} 公司/单位
# ⚠️ 改这里的措辞时，回头看一眼 REGULATION_CHECKS 的同一条例——两张表该同步。
# ============================================================================

#: 多数条例都要问的两条，**写在需要的条例里，不自动追加**——
#: 和 REGULATION_CHECKS 的 _COMMON_CHECKS 同一口径：十四(一)(二)(三)、十五(一) 不加。
_EXTRA_COMMON = (
    "{本人}有没有参加工伤保险？你知道吗？",
    "出事前{他}有没有喝过酒？",
)

WITNESS_EXTRA_QUESTIONS = {
    "第十四条第（二）项": (
        "{本人}当天是在上班前做准备工作，还是下班后做收尾工作？具体做的是什么？",
        "这件事是谁安排的？",
    ),
    "第十四条第（三）项": (
        "{本人}当天是因为什么事情、和谁起的冲突？",
        "冲突是谁先动的手？{本人}当时有没有还手？",
        "你当时在不在现场？看到的情况是怎样的？",
    ),
    "第十四条第（四）项": (
        "{本人}在{单位}从事的是什么工种？平时接触哪些有害因素？",
        "{他}在这个岗位上干了多久了？",
        "{他}以前有没有在别的单位接触过同样的有害因素？",
    ) + _EXTRA_COMMON,
    "第十四条第（五）项": (
        "{本人}这次外出是去哪里、办什么事？是谁安排的？",
        "{他}外出期间你有没有联系过？知道{他}出了什么事吗？",
    ) + _EXTRA_COMMON,
    "第十四条第（六）项": (
        "{本人}平时上下班的路线是怎样的？从{单位}到家大概多远？",
        "出事那天{他}是上班去还是下班回？大概几点？",
        "{他}当天是怎么走的——自己开车、骑车还是坐车？",
        # 这一条对着要件里的「非本人主要责任」，是这项能否成立的关键
        "公安机关交通管理部门是否处理了此事？你是否了解责任划分情况？",
    ) + _EXTRA_COMMON,
    "第十五条第（一）项": (
        "{本人}当天是在什么情况下发病的？当时在不在工作岗位上？",
        "发病后是谁先发现的？什么时候送去医院的？",
        "从发病到送医、抢救的经过你了解吗？",
    ),
    "第十五条第（二）项": (
        "{本人}参加的是什么活动？是谁组织或者号召的？",
        "{他}当时在活动里做什么？是怎么受的伤？",
    ) + _EXTRA_COMMON,
    "第十五条第（三）项": (
        "{本人}以前在部队服役时受过伤吗？你是什么时候知道的？",
        "{他}这次旧伤复发是什么时候、在什么情况下发生的？",
        "{他}有没有革命伤残军人证，你见过吗？",
    ) + _EXTRA_COMMON,
}


def compose_witness_questions(regulation_short: str, worker_name: str,
                              pronoun: str = "他", unit_word: str = "单位") -> List[str]:
    """返回该条例下证人笔录要**追加**的问句（已填好姓名/代词/单位称谓）。

    表里没写（或写空的）条例返回空列表——调用方照常输出固定骨架即可。
    原文里的 `{`/`}` 只有这几个占位符，所以直接用 str.format；写错占位符名会
    在这里抛 KeyError 而不是悄悄留个 `{本人}` 在笔录上——这正是想要的。
    """
    rows = WITNESS_EXTRA_QUESTIONS.get((regulation_short or "").strip(), ())
    return [q.format(本人=worker_name, 他=pronoun, 单位=unit_word) for q in rows]


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

    reg = REGULATION_CATALOG.get((regulation_short or "").strip(), {})
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


# ============================================================================
# 中文数字 ↔ 整数（条例的「第X条」「（X）项」互转）
# ============================================================================

_REG_CN_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def cn_num_to_int(text: str):
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


def int_to_cn_num(n) -> str:
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


def regulation_full_to_short(text: str) -> str:
    """《工伤保险条例》第十四条第一款第一项 → 第十四条第（一）项"""
    if not text:
        return ""
    m = re.search(
        r"第([一二三四五六七八九十]+)条第[一二三四五六七八九十]+款第([一二三四五六七八九十]+)项",
        text,
    )
    if m:
        return f"第{m.group(1)}条第（{m.group(2)}）项"
    return text


def regulation_short_to_full(short: str) -> str:
    """第十四条第（一）项 → 《工伤保险条例》第十四条第一款第一项"""
    if not short:
        return ""
    m = re.match(r"^第([一二三四五六七八九十]+)条第（([一二三四五六七八九十]+)）项$", short)
    if m:
        art = cn_num_to_int(m.group(1))
        item = cn_num_to_int(m.group(2))
        if art and item:
            return f"《工伤保险条例》第{int_to_cn_num(art)}条第一款第{int_to_cn_num(item)}项"
    return short


# ============================================================================
# 单位性质 → 表述（审批表「引用条例」、告知书依据句、受伤职工所属表述）
# ============================================================================

def unit_is_non_enterprise(unit_type: str) -> bool:
    """单位性质是否为 机关（公务员）/事业单位（非企业）。"""
    return (unit_type or "").strip() not in ("", DEFAULT_UNIT_TYPE)


def regulation_full_for_unit(unit_type: str, short: str) -> str:
    """审批表“引用条例”表述：机关/事业单位案件在《工伤保险条例》前加“参照”。"""
    full = regulation_short_to_full(short or "")
    if not full:
        return full
    return ("参照" + full) if unit_is_non_enterprise(unit_type) else full


def person_affiliation(case_obj: dict) -> str:
    """受伤职工“所属表述”：企业→「{单位}职工」；机关（公务员）/事业单位→按身份表述。"""
    ut = str(case_obj.get('unit_type', DEFAULT_UNIT_TYPE) or DEFAULT_UNIT_TYPE)
    unit = str(case_obj.get('labor_unit', '') or '')
    ident = str(case_obj.get('identity', '') or '').strip() or DEFAULT_IDENTITY
    if ut == "机关（公务员）":
        return f"{unit}（机关）{ident}" if unit else f"机关（公务员）{ident}"
    if ut == "事业单位":
        return f"{unit}（事业单位）{ident}" if unit else f"事业单位{ident}"
    return f"{unit}职工" if unit else "用人单位职工"


def notice_basis_sentence(case_obj: dict, deny: bool = False) -> str:
    """工伤认定告知书的结论依据句（数据驱动；机关/事业单位加“参照”）。"""
    full = regulation_full_for_unit(
        str(case_obj.get('unit_type', '') or ''),
        str(case_obj.get('proposed_article', '') or ''))
    if not full:
        return ""
    if deny:
        return f"不符合{full}认定工伤之规定，拟不予认定为工伤。"
    return f"符合{full}认定工伤之规定，现拟决定认定为工伤。"
