# prompt_manager.py
"""
提示词管理器 - 把 AI 提示词外置为 resource/prompts/ 下的纯文本文件。

设计：
- 用户可直接用记事本编辑 resource/prompts/*.txt，改后保存即可，下次生成即用新提示词（无需重启）。
- **txt 文件就是唯一事实源**：文件缺失、为空、或读不出来时直接抛 PromptError，不再回退到
  代码里的内置副本。以前那套「静默回退」的结果是「提示词被删了 → 拿一份很旧的提示词继续
  生成」，界面上完全看不出来；宁可当场报错。
- 动态数据用 {{...}} 标记（如 {{笔录全文}}）；4 份「发送给AI提示词」还会被 Jinja2 渲染，
  里面可以写 {% if %} / {# #}，其余几份仍由调用方用 str.replace 替换。
- 「读」和「渲染」都在本模块：`load_prompt` 取 txt，`render_prompt_template` 填值。
  （渲染原先在 app_main.py 里，2026-09 搬过来——它本来就只依赖 txt 内容。）
"""
import logging
import os
import re
from typing import Any, Dict

from jinja2 import Environment, StrictUndefined

logger = logging.getLogger(__name__)


class PromptError(RuntimeError):
    """提示词文件缺失、为空、或读不出来。

    以前这里会静默回退到代码里的内置副本，后果是「提示词被删了 → 拿一份很旧的提示词继续
    生成」，界面上看不出任何异常。现在直接抛错，让问题当场暴露。
    """


# 提示词 key → 可编辑文件名
PROMPT_FILES: Dict[str, str] = {
    "review": "智能补问.txt",
    "injury_and_conclusion": "受伤经过与诊断结论.txt",
    "witness_send_to_ai": "证人发送给AI提示词.txt",
    "legal_send_to_ai": "法人发送给AI提示词.txt",
    "approval_analysis": "审批表分析提示词.txt",
    "self_send_to_ai": "本人发送给AI提示词.txt",
    "family_send_to_ai": "家属发送给AI提示词.txt",
}


def _prompts_dir():
    """提示词目录 resource/prompts/"""
    try:
        from path_utils import path_utils
        base = path_utils.resource_dir
    except Exception:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resource")
    prompts_dir = os.path.join(str(base), "prompts")
    os.makedirs(prompts_dir, exist_ok=True)
    return prompts_dir


def load_prompt(key: str) -> str:
    """读取指定提示词的 txt 全文（strip 后）。

    txt 是唯一事实源：文件不存在 / 为空 / 读不出来，一律抛 PromptError，不回退也不重建
    （utf-8-sig 兼容记事本存的 BOM）。

    调用方要负责兜住这个异常：PyQt 槽函数里漏出去的异常会被 qFatal 中止进程
    （见 log_utils.install_excepthook 的说明），得自己 try 一下给用户提示。
    """
    filename = PROMPT_FILES.get(key)
    if filename is None:
        raise KeyError(f"提示词 {key} 未在 PROMPT_FILES 里配文件名")

    path = os.path.join(_prompts_dir(), filename)
    if not os.path.exists(path):
        raise PromptError(f"提示词文件不存在：{path}")

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            content = f.read().strip()
    except OSError as e:
        raise PromptError(f"提示词文件读不出来：{path}（{e}）") from e

    if not content:
        raise PromptError(f"提示词文件是空的：{path}")

    return content


# ============================================================================
# 模板渲染（把 load_prompt 读回来的 txt 填上值）
# ============================================================================

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
