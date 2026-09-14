# -*- coding: utf-8 -*-
"""发给 AI 的提示词渲染回归测试（纯函数，不依赖 Qt）。

render_prompt_template 是「模板 + 占位符 → 最终提示词」的唯一出口，两种错法都很安静：

- 空值占位符独占的那一行没删干净 → 提示词里留下「- 称谓提示：」这种只有标签、
  没有内容的空壳行（AI 会照着一个空冒号写话）；
- 删行删过头 → 把行内别的占位符的值一起吃掉，等于悄悄删掉半句话。

format_compact_time 守着受伤/就诊时间的展示格式：输入不合法时必须原样返回，
不能猜一个日期出来——猜错了 AI 会照着编笔录里的时间。

本文件只测纯函数，不依赖 Qt，跑得很快。
"""
import logging

import pytest

from app_main import format_compact_time, render_prompt_template


class TestFormatCompactTime:
    """受伤/就诊时间：YYYYMMDDHHMM ↔「2026年07月20日16时20分」"""

    def test_正常12位转中文(self):
        assert format_compact_time('202607201620') == '2026年07月20日16时20分'

    def test_月日时分补零与申请受理时间同口径(self):
        # _resolve_date_input 处理 申请/受理时间 也是补零口径，两处要对齐
        assert format_compact_time('202607200905') == '2026年07月20日09时05分'

    def test_首尾空格剥掉后照样识别(self):
        assert format_compact_time('  202607201620  ') == '2026年07月20日16时20分'

    def test_空值与None返回空串(self):
        assert format_compact_time('') == ''
        assert format_compact_time(None) == ''
        assert format_compact_time('   ') == ''

    @pytest.mark.parametrize('bad', [
        '20260720162',        # 11 位，少一位
        '2026072016200',      # 13 位，多一位
        '20260720162a',       # 含非数字
        'abcdefghijkl',       # 全非数字
        '2026年7月20日',        # 已经是中文，不重复格式化
        '2026-07-20 16:20',   # 别的分隔格式
    ])
    def test_长度或字符合法性不符时原样返回(self, bad):
        # 不许猜、不许截断：宁可原样透出，也不能编一个看上去对的日期
        assert format_compact_time(bad) == bad


class TestRenderPromptTemplate:
    """模板占位符替换 + 空值占位符整行删除"""

    def test_普通占位符替换(self):
        out = render_prompt_template('姓名：{{本人姓名}}', {'本人姓名': '张三'})
        assert out == '姓名：张三'

    def test_空值占位符独占一行时整行删掉(self):
        tpl = '【信息】\n- 手机号：{{本人手机号}}\n- 姓名：{{本人姓名}}\n'
        out = render_prompt_template(tpl, {'本人手机号': '', '本人姓名': '张三'})
        assert out == '【信息】\n- 姓名：张三\n'
        assert '手机号' not in out
        assert '\n\n' not in out          # 不许留下空行

    def test_称谓提示样式的那一行整行删掉(self):
        # 企业案 _identity_wording_hint 返回空串，这行不该以空壳形式留下
        tpl = '【重要要求】\n- 称谓提示：{{身份话术}}\n- 其他要求。\n'
        out = render_prompt_template(tpl, {'身份话术': ''})
        assert out == '【重要要求】\n- 其他要求。\n'
        assert '称谓提示' not in out

    def test_末行没有换行的空值占位符也删掉(self):
        out = render_prompt_template('A：{{a}}\nB：{{b}}', {'a': '甲', 'b': ''})
        assert out == 'A：甲\n'

    def test_CRLF模板也能删行(self):
        out = render_prompt_template('A：{{a}}\r\nB：{{b}}\r\n', {'a': '甲', 'b': ''})
        assert out == 'A：甲\r\n'

    def test_空值占位符在句中时只替成空串不吃掉整句(self):
        tpl = '本案申请类型为 {{申请类型}}，请据此设计问题。\n'
        out = render_prompt_template(tpl, {'申请类型': ''})
        assert out == '本案申请类型为 ，请据此设计问题。\n'

    def test_同行多个占位符其中为空时不连累已有的值(self):
        # 后面那个为空，不能把整行（连同前面的「张三」）一起删掉
        tpl = '- {{本人姓名}}{{本人手机号}}\n'
        out = render_prompt_template(tpl, {'本人姓名': '张三', '本人手机号': ''})
        assert out == '- 张三\n'

    def test_删行结果与data遍历顺序无关(self):
        # 同一份模板，dict 顺序颠倒也要得到同样结果（旧实现会看顺序）
        tpl = '- {{本人姓名}}{{本人手机号}}\n'
        assert render_prompt_template(tpl, {'本人姓名': '张三', '本人手机号': ''}) == '- 张三\n'
        assert render_prompt_template(tpl, {'本人手机号': '', '本人姓名': '张三'}) == '- 张三\n'

    def test_None值不替换并记告警(self, caplog):
        # None 表示「代码没给这个 key 填值」，保留原样 + 告警，别静默替成 "None"
        with caplog.at_level(logging.WARNING):
            out = render_prompt_template('- 姓名：{{本人姓名}}', {'本人姓名': None}, '本人')
        assert out == '- 姓名：{{本人姓名}}'
        assert any('本人姓名' in r.message for r in caplog.records)

    def test_模板里代码没填的占位符记告警(self, caplog):
        with caplog.at_level(logging.WARNING):
            out = render_prompt_template('- 姓名：{{本人姓名}}', {}, '本人')
        assert out == '- 姓名：{{本人姓名}}'
        assert any('本人姓名' in r.message for r in caplog.records)
