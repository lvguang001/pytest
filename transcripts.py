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
)
from services import date_now, time_now

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
    checks = compose_checks(case_obj.get('proposed_article', ''))
    check_names = set(checks)

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
        # 必须问清的事实（不是要收的材料）——按条例算，与案卷无关
        '核实要点': '\n'.join(f"  · {c}" for c in checks),
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
    """四个角色共用的那几项"""
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
        '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
    }


def prompt_fill_data(role: str, case_obj: dict, get_data, witness: dict = None,
                     username: str = "", current_period: str = "") -> dict:
    """返回用于填充该角色「发给AI」提示词的数据。

    `get_data` 是扁平键读取器（`app_main` 传 `MainWindow.get_data`）：
    当前人记录里没有的字段，回退到数据模型的兼容扁平键（如「证人姓名」）。
    `witness` 只在 role='证人' 时用得上，由调用方先 `_ensure_current_witness()` 取好。
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
            '身份话术': identity_wording_hint(ut, ident, '证人'),
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
# 渲染笔录 docx
# ============================================================================

#: 模板里告知程序结束的位置；它之后的样例问答会被删掉，换成 AI 生成的内容
ANCHOR_TEXT = '答：听清楚了，不申请回避'


def render_transcript(template_path: str, template_data: dict, content: str,
                      out_dir: str, file_base: str, label: str = "") -> str:
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
