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
"""
import logging
import os
from typing import Dict

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
