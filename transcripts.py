# -*- coding: utf-8 -*-
"""谈话笔录：模板占位符数据 + 提示词填充数据 + 笔录 docx 渲染。

原先这些都挂在 MainWindow 上（2026-09 抽出来）。它们要的那点「环境」——
当前用户名、当前证人记录、扁平键读取——都改成显式参数传进来，
于是不构造窗口也能单测。

编排（选哪个模板、什么时候生成、状态栏说什么）仍留在 MainWindow。
"""

import logging
import os

from docx.shared import Pt
from docxtpl import DocxTemplate

from case_classifier import (
    DEFAULT_IDENTITY, DEFAULT_UNIT_TYPE, UNIT_TYPE_APPELLATION, compose_checks,
    compose_witness_questions, person_pronoun, regulation_elements, unit_appellation,
)
from services import date_now, format_compact_time, time_now

logger = logging.getLogger(__name__)


def identity_wording_hint(unit_type: str, identity: str, role: str) -> str:
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


# ============================================================================
# 模板占位符数据
# ============================================================================

def checked_checks(case_obj: dict) -> list:
    """面板上**勾了**的核实要点（勾 = 该情形成立）；不是要收的材料。

    核实要点也存在 `materials` 里（面板上混在一起显示），靠名字和
    `compose_checks` 对上来区分。两处都要用（`{{核实要点}}` 和证人提示词），
    所以在这一处算好。
    """
    check_names = set(compose_checks(case_obj.get('proposed_article', '')))
    return [_m['name'] for _m in (case_obj.get('materials') or [])
            if isinstance(_m, dict) and _m.get('provided')
            and _m.get('name') in check_names]


def build_unified_template_data(case_obj: dict, username: str = "",
                                current_period: str = "") -> dict:
    """构建统一的模板渲染字典。

    中文 key 为主（模板占位符统一用中文），同时附带英文 case_obj 字段名 key，
    模板里写中文或英文占位符都能被替换。
    """
    elements = case_obj.get('proposed_article_elements', []) or []
    materials = case_obj.get('materials', []) or []
    # {{已提供材料}} 只列**已勾选**的：未勾选的也列进去，AI 会以为证据已经齐了。
    # 每条带上备注——备注里常写着这份材料的问题（如「受伤时间不在合同期限内」），
    # 提示词那条「与已提供证据材料不一致的地方应追问核实」要靠它才落得实。
    # 分行列、不用顿号连成一行：材料名本身就可能带括号
    # （如「近亲属关系证明（户口簿/结婚证等）」），再套括号会分不清哪段是备注。
    # 核实要点不是材料：面板上它和材料混在一起显示（勾选后也会存进 materials），
    # 但 {{已提供材料}} 只该列真正的书面证据，所以按名字把它们滤掉
    check_names = set(compose_checks(case_obj.get('proposed_article', '')))

    # 核实要点：**面板上勾了哪条，提示词里就出哪条**（勾 = 该情形成立）。
    # 没勾的不列——尤其二选一的项（开工前的准备工作 / 收工后的收尾工作）：
    # 两条都发的话 AI 会先问一种、再问另一种，而案子只可能是其中一种。
    checked = checked_checks(case_obj)

    material_lines = []
    for _m in materials:
        if not (isinstance(_m, dict) and _m.get('name') and _m.get('provided')):
            continue
        if _m['name'] in check_names:
            continue
        _note = str(_m.get('notes') or '').strip()
        material_lines.append(f"  · {_m['name']}（备注：{_note}）" if _note
                              else f"  · {_m['name']}")
    from services import format_compact_time
    injury_time = format_compact_time(case_obj.get('injury_time', ''))
    visit_time = format_compact_time(case_obj.get('visit_time', ''))
    recorder = case_obj.get('recorder', '') or username

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
        '已提供材料': '\n'.join(material_lines),
        # 面板上勾了的核实要点（不是要收的材料）——勾了哪项就按哪项问
        '核实要点': '\n'.join(f"  · {c}" for c in checked),
        '记录人': recorder,
        '申请人名称': case_obj.get('applicant_name', ''),
        '用户名': username,
        '当前时期': current_period or (date_now() + time_now()),
    }

    return zh


# ============================================================================
# 「发给AI」提示词的填充数据
# ============================================================================

def _common(case_obj: dict) -> dict:
    """四个角色共用的那几项

    **受伤时间 / 就诊时间 必须在这里**：四个角色的提示词里都有时间核对块
    `{% if 受伤时间 and 就诊时间 %}`，而 Jinja 用的是 StrictUndefined——条件里
    引用到没填的键会**直接抛 UndefinedError**，不是静默当假。少填这两个键，
    证人/法人/家属三种笔录点一下就崩（本人走 build_unified_template_data，自成一路，
    所以只有它一直没事）。两个字段和别处同口径，走 format_compact_time 归一。

    拟用条例 / 法律要件 / 核实要点是给证人提示词用的（要按要件设计提问），
    法人/家属拿到这几项用不上，但富余键替换无害——多填比少填安全。
    """
    return {
        '案本号': case_obj.get('case_id', ''),
        '案件性质': case_obj.get('case_nature', ''),
        '申请类型': case_obj.get('applicant_type', ''),
        '本人姓名': case_obj.get('name', ''),
        '本人性别': case_obj.get('gender', ''),
        '本人身份证号': case_obj.get('id_card', ''),
        '本人年龄': case_obj.get('age', ''),
        '本人岗位': case_obj.get('position', ''),
        '用人单位': case_obj.get('labor_unit', ''),
        '用工单位': case_obj.get('employer', ''),
        '工地名称': case_obj.get('site', ''),
        '受伤时间': format_compact_time(case_obj.get('injury_time', '')),
        '就诊时间': format_compact_time(case_obj.get('visit_time', '')),
        '受伤经过': case_obj.get('injury_description', ''),
        '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
        '拟用条例': case_obj.get('proposed_article', ''),
        '法律要件': ' + '.join(
            regulation_elements(case_obj.get('proposed_article', ''))),
        '核实要点': '\n'.join(f"  · {c}" for c in checked_checks(case_obj)),
        '单位称谓': unit_appellation(case_obj.get('unit_type', DEFAULT_UNIT_TYPE)),
    }


def prompt_fill_data(role: str, case_obj: dict, get_data, witness: dict = None,
                     username: str = "", current_period: str = "",
                     main_transcript: str = "") -> dict:
    """返回用于填充该角色「发给AI」提示词的数据。

    `get_data` 是扁平键读取器（`app_main` 传 `MainWindow.get_data`）：
    当前人记录里没有的字段，回退到数据模型的兼容扁平键（如「证人姓名」）。
    `witness` 只在 role='证人' 时用得上，由调用方先 `_ensure_current_witness()` 取好。
    `main_transcript` 是本案**本人笔录的正文**（role='证人' 时用）——由调用方读好传进来，
    这里不做文件 IO（本模块是纯函数，才能不建窗口直接单测）。
    """
    ut = case_obj.get('unit_type', DEFAULT_UNIT_TYPE)
    data = _common(case_obj)

    if role == '证人':
        w = witness or {}
        ident = w.get('identity') or get_data('证人身份', '') or DEFAULT_IDENTITY
        data.update({
            '证人姓名': w.get('name', '') or get_data('证人姓名', ''),
            '证人身份证号': w.get('id_card', '') or get_data('证人身份证号', ''),
            '证人岗位': w.get('position', '') or get_data('证人岗位', ''),
            '证人身份': ident,
            '证人性别': w.get('gender', '') or get_data('证人性别', ''),
            '身份话术': identity_wording_hint(ut, ident, '证人'),
            # 本人笔录正文（没有就空串）：提示词里靠 {% if 本人笔录 %} 分成两支——
            # 有它才谈得上「与本人陈述不一致处追问核实」
            '本人笔录': main_transcript or '',
        })
        return data

    if role == '法人':
        ident = get_data('法人身份', '') or DEFAULT_IDENTITY
        data.update({
            '法人姓名': get_data('法人姓名', ''),
            '法人职务': get_data('法人职务', ''),
            '法人身份证号': get_data('法人身份证号', ''),
            '法人身份': ident,
            '身份话术': identity_wording_hint(ut, ident, '法人'),
        })
        return data

    if role == '家属':
        fam_ident = get_data('家属身份', '')
        fam_unit = get_data('家属单位名称', '')
        data.update({
            '家属姓名': get_data('家属姓名', ''),
            '家属身份证号': get_data('家属身份证号', ''),
            '与死者关系': fam_ident,
            '家属单位名称': fam_unit,
            # 家属没单位 → 岗位一并留空（与笔录表头同一口径）
            '家属岗位': get_data('家属岗位', '') if fam_unit else '',
            '家属身份': fam_ident,
            '身份话术': identity_wording_hint(ut, fam_ident, '家属'),
        })
        return data

    # 本人：复用统一模板数据（中文+英文 key 富余项替换无害），并附身份话术
    base = build_unified_template_data(case_obj, username, current_period)
    base['身份话术'] = identity_wording_hint(
        ut, case_obj.get('identity', DEFAULT_IDENTITY), '本人')
    return base


# ============================================================================
# 证人笔录：本地拼装（不调 AI）
# ============================================================================

#: 证人笔录的固定骨架：(问句, 答句)。**取自 10 份实际证人笔录样本**的措辞，
#: 不是自己编的句子——改这里等于改办案口径，措辞请照样本核对。
#:
#: 答句三态：
#:   `""`   → 输出一行光杆「答：」，留给现场记录（事实部分一律这样）
#:   `None` → **不输出答句**：末问那行留给被谈话人亲笔写「以上笔录我看过……」并签名，
#:            程序不能代写（docx 模板末段也是同一用意，见 render_transcript 的 tail）
#:   其它   → 与案情无关、当场就能确定的答句（同事关系、身体正常、知道要如实回答）
#:
#: 占位符：{本人} 受伤职工姓名、{他} 代词、{单位} 公司/单位、{岗位} 受伤职工岗位
_WITNESS_SKELETON = (
    ("请介绍一下你的姓名、住址、工作单位以及从事的工作？", ""),
    ("请问你认识{本人}吗？{他}从事什么工作？", ""),
    ("请问你与{本人}是亲戚关系、朋友关系、同事关系还是其他什么关系？",
     "我和{本人}没有亲戚关系，我们就是同事关系。"),
    ("你们{单位}员工工作时间是怎么安排的？{本人}的工作时间是否是一样的？有考勤的吗？", ""),
    ("事故发生时，你是否在现场？当时在做什么？", ""),
    ("请你详细陈述一下你知道的{本人}受伤情况或者你看到的受伤经过？", ""),
    ("事故发生的时间，是否在正常的工作时间内？{本人}当时进行的工作是否是{单位}安排的本职工作？",
     "是的。"),
    ("{本人}受伤的现场当时还有谁在场吗？", ""),
    ("{本人}受伤后你有没有通知的领导？", ""),
    ("{本人}受伤当天你有没有看到{本人}？{他}当时的身体是正常的吗？",
     "我有看到过{他}的，{他}在受伤之前身体是正常的。"),
    ("你应当如实回答我们的询问并协助调查，不得提供虚假证言，否则将承担法律责任，你清楚吗？",
     "我知道的。"),
    ("{本人}受伤后去了哪个医院？谁送{他}去的？", "我不清楚。"),
    ("你还有没有需要补充？", "没有了。"),
    ("以上记录是否和你表达的意思一致？", None),
)

#: 追加问句插在骨架的第几问之后（0 起算）：第 6 问「详细陈述受伤经过」之后。
#: 顺序是有意的——经过问完，再追该条例特有的事实。
_WITNESS_EXTRA_AFTER = 5

#: 「请问你认识 XX 吗」那一问的下标——只有它的答句要按岗位变（见 compose_witness_qa）
_WITNESS_INTRO_INDEX = 1


def compose_witness_qa(case_obj: dict) -> str:
    """本地拼装一套证人笔录问答（**不调 AI、不联网**）。

    用于本案卷里**没有本人笔录**时：固定骨架 + 按拟用条例追加的问句。
    答句只填「不依赖案情就能确定」的部分，事实部分留白给现场记录。

    产出交给 `render_transcript` 时**必须**传 `keep_blank_answers=True`，
    否则那些故意留白的「答：」行会被当成 AI 输错的光秃行滤掉。
    """
    name = str(case_obj.get('name', '') or '').strip() or "受伤职工"
    pronoun = person_pronoun(case_obj.get('gender', ''))
    unit_word = unit_appellation(case_obj.get('unit_type', DEFAULT_UNIT_TYPE))
    position = str(case_obj.get('position', '') or '').strip()
    flavor = {
        '本人': name, '他': pronoun, '单位': unit_word, '岗位': position,
    }

    questions = []
    for i, (q, a) in enumerate(_WITNESS_SKELETON):
        if i == _WITNESS_INTRO_INDEX:
            # 答句要跟着岗位走：岗位没填时「做……的」缺个宾语，改成「工作的」
            a = (f"认识的，{name}是在我们{unit_word}做{position}的。" if position
                 else f"认识的，{name}是在我们{unit_word}工作的。")
        questions.append((q, a))

    extras = [(q, "") for q in compose_witness_questions(
        case_obj.get('proposed_article', ''), name, pronoun, unit_word)]
    questions[_WITNESS_EXTRA_AFTER + 1:_WITNESS_EXTRA_AFTER + 1] = extras

    lines = []
    for q, a in questions:
        lines.append("问：" + q.format(**flavor))
        if a is None:
            continue
        lines.append("答：" + (a.format(**flavor) if a else ""))
    return "\n".join(lines)


# ============================================================================
# 渲染笔录 docx
# ============================================================================

#: 模板里告知程序结束的位置；它之后的样例问答会被删掉，换成 AI 生成的内容
ANCHOR_TEXT = '答：听清楚了，不申请回避'


def render_transcript(template_path: str, template_data: dict, content: str,
                      out_dir: str, file_base: str, label: str = "",
                      keep_blank_answers: bool = False) -> str:
    """渲染谈话笔录模板，把 AI 问答插到告知程序之后，保存并返回文件路径（失败返回空串）。

    步骤：
    1. docxtpl 渲染占位符
    2. 定位锚点「答：听清楚了，不申请回避」
    3. 删掉锚点之后的模板样例问答（避免与 AI 问答重复），**但保留模板末段**
    4. 按锚点段落的格式插入 AI 每一行（15pt + 下划线），插在末段之前
    5. 存进 out_dir，文件名带序号避让重名

    **末段不动的原因**：它是「答：」+ 一串带下划线的空白，留给被谈话人亲笔写
    「以上笔录我看过，与我说的一样」并签字——不能由 AI 代写。各模板那串空白的
    长度是 58~66 个空格不等（作者凭手感填的），所以不另算长度，直接留用模板这一段。
    """
    if not os.path.exists(template_path):
        logger.warning(f"⚠️ 谈话模板不存在: {template_path}")
        return ""

    doc = DocxTemplate(template_path)
    doc.render(template_data)

    # ── 定位锚点 ──
    anchor_index = None
    anchor_pf = None
    for i, p in enumerate(doc.paragraphs):
        if ANCHOR_TEXT in p.text:
            anchor_index = i
            anchor_pf = p.paragraph_format
            break

    if anchor_index is None:
        logger.warning(f"⚠️ 未找到锚点「{ANCHOR_TEXT}」")
        return ""

    # ── 认一下模板末段（「答：」+ 纯空白）——下面删样例时要把它留下 ──
    tail = None
    _last = doc.paragraphs[-1] if doc.paragraphs else None
    if (_last is not None and _last.text.strip().startswith('答：')
            and not _last.text.strip()[2:].strip()):
        tail = _last

    # ── 删掉锚点之后的模板样例问答（锚点与末段都保留）──
    body = doc.element.body
    anchor_elem = doc.paragraphs[anchor_index]._element
    after_anchor = False
    for child in list(body):
        if child is anchor_elem:
            after_anchor = True
        elif after_anchor and (tail is None or child is not tail._element):
            body.remove(child)

    # ── 插入 AI 问答（插在末段之前）──
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if not keep_blank_answers:
        # 空问答行：AI 偶发会输出一个光秃的「问：」/「答：」（只有前缀、没内容）。
        # 提示词要求「最后一个问题只写问：……、不写对应的答：」，AI 有时在收尾问句后
        # 再补一个空的「问：」——渲染成笔录就成了最后一行空问题（实案见过）。滤掉。
        #
        # 本地拼装那条路（compose_witness_qa）恰恰**故意**留白「答：」给现场记录，
        # 所以它传 keep_blank_answers=True 绕开这一条。两者用意相反，不能合并。
        lines = [ln for ln in lines
                 if not (len(ln) >= 2 and ln[:2] in ('问：', '答：', '问:', '答:')
                         and not ln[2:].strip())]
    # 末段已经提供了最后一个「答：」，AI 再写一个就成了两个
    if tail is not None and lines and lines[-1].startswith('答：'):
        lines = lines[:-1]

    for line in lines:
        p = tail.insert_paragraph_before() if tail is not None else doc.add_paragraph()
        if anchor_pf is not None:      # 沿用锚点段落的排版
            p.paragraph_format.alignment = anchor_pf.alignment
            p.paragraph_format.first_line_indent = anchor_pf.first_line_indent
            p.paragraph_format.space_before = anchor_pf.space_before
            p.paragraph_format.space_after = anchor_pf.space_after
        run = p.add_run(line)
        run.font.size = Pt(15)
        run.underline = True

    # ── 保存（不覆盖已有笔录）──
    from documents import unique_path
    os.makedirs(out_dir, exist_ok=True)
    target_path = unique_path(out_dir, file_base)
    doc.save(target_path)
    print(f"✅ {label or file_base}已生成: {target_path}")
    return target_path
