# -*- coding: utf-8 -*-
"""主界面布局回归测试（布局管理器版）。

界面已从「绝对坐标 + 十几个 _move_xxx 补丁」改成布局管理器，背景见
`refactor/CONTROLS.md`。这里锁两样东西：

1. **结构** —— 17 行的顺序、18px 行距、右栏与左栏底边齐平、控件不重叠、
   没有控件漏在布局外面。结构是布局摆出来的，改坏了就该报错。
2. **外观** —— 每个具名控件的最终矩形，存在 `tests/ui_geometry_baseline.json`。
   这一条故意做得敏感：任何没有预期的像素位移都会失败。

**有意改了界面之后**，用下面这条重新生成外观基准，并在提交信息里写清楚
动了哪里、为什么：

    UI_BASELINE_UPDATE=1 python -m pytest tests/test_ui_layout.py -k baseline
"""

import os
from pathlib import Path

import pytest

from ui_helpers import (
    BASELINE, LEFT_WIDTH, LEFT_TOP, RIGHT_WIDTH, ROW_GAP, WINDOW_SIZE,
    abs_rect, all_rows, left_column, load_baseline, named_rects,
    row_rects, save_baseline,
)

ROOT = Path(__file__).resolve().parent.parent


# ============================================================================
# 结构
# ============================================================================

def test_window_size_is_locked(window):
    """窗口固定 870×850，最小/最大都锁死（布局不该被拉伸）。"""
    assert (window.width(), window.height()) == WINDOW_SIZE
    assert window.minimumSize() == window.maximumSize() == window.size()


def test_top_bar_is_28px_and_holds_three_controls(window):
    x, y, w, h = abs_rect(window.top_bar, window)
    assert (x, y) == (0, 0)
    assert h == 28, "顶栏高度变了，左栏首行位置会跟着变"
    for name in ("config_toggle_btn", "top_status_label", "todo_btn"):
        r = abs_rect(getattr(window, name), window)
        assert r[1] >= 0 and r[1] + r[3] <= 28, f"{name} 跑到顶栏外面了: {r}"
    # 待办按钮贴右
    tx, _, tw, _ = abs_rect(window.todo_btn, window)
    assert window.width() - (tx + tw) <= 8


def test_left_column_starts_below_top_bar(window):
    assert abs_rect(left_column(window), window)[:2] == (0, 28)
    assert left_column(window).width() == LEFT_WIDTH
    first = all_rows(window)[0]
    assert first[1] == LEFT_TOP, f"左栏首行顶边是 {first[1]}，应为 {LEFT_TOP}"


def test_left_column_rows_are_in_the_documented_order(window):
    """17 行的上下顺序必须与 CONTROLS.md §五 一致。"""
    names = [name for name, _, _ in all_rows(window)]
    assert names == [row[0] for row in _documented_rows()]


def test_left_column_row_gap_is_uniform(window):
    """相邻行之间一律留 18px —— 这正是 _uniform_row_spacing 当年想做的事。"""
    rows = all_rows(window)
    for (n1, _, bottom), (n2, top, _) in zip(rows, rows[1:]):
        assert top - bottom == ROW_GAP, (
            f"「{n1}」底边 {bottom} 到「{n2}」顶边 {top} 是 {top - bottom}px，"
            f"应为 {ROW_GAP}px")


def test_left_column_rows_do_not_overlap(window):
    rows = all_rows(window)
    for (n1, _, bottom), (n2, top, _) in zip(rows, rows[1:]):
        assert bottom <= top, f"「{n1}」和「{n2}」在纵向上叠住了"


def test_widgets_in_a_row_do_not_overlap(window):
    """同一行里的控件横向不能叠（布局接管后这是最容易出的错）。"""
    for name, rects in row_rects(window):
        ordered = sorted(rects, key=lambda r: r[0])
        for a, b in zip(ordered, ordered[1:]):
            assert a[0] + a[2] <= b[0], (
                f"「{name}」行里 x={a[0]}（宽 {a[2]}）与 x={b[0]} 的控件叠住了")


def test_left_column_stays_left_of_right_column(window):
    rx = abs_rect(window.right_column, window)[0]
    assert rx == LEFT_WIDTH
    assert window.right_column.width() == RIGHT_WIDTH
    from PyQt5.QtWidgets import QWidget
    for c in left_column(window).children():
        if isinstance(c, QWidget):
            r = abs_rect(c, window)
            assert r[0] + r[2] <= rx, f"{c.objectName()} 越过了右栏左缘"


def test_right_panel_bottom_aligns_with_left_column(window):
    """右栏两个分组框的底边要与左栏最后一行齐平（_grow_right_panels 当年的目标）。"""
    left_bottom = max(bottom for _, _, bottom in all_rows(window))
    g = window.material_group
    r = abs_rect(g, window)
    right_bottom = r[1] + r[3]
    assert abs(left_bottom - right_bottom) <= 2, (
        f"左栏底边 {left_bottom}，右栏底边 {right_bottom}")


def test_no_orphan_widgets(window):
    """任何控件都不能漏在布局外面。

    漏掉的控件会保持 `ui_main_window.py` 里的绝对坐标、parent 还是窗口本身，
    于是浮在所有东西上面 —— 这是从绝对坐标改布局时最典型的翻车方式。
    """
    from PyQt5.QtWidgets import QWidget
    children = {c.objectName() for c in window.children() if isinstance(c, QWidget)}
    assert children == {"top_bar", "left_column", "right_column",
                        "apiPanel", "TodoBoard"}, (
        "窗口的直接子控件变了：多出来的多半是没加进任何布局的漏网控件")


def test_left_column_holds_exactly_the_documented_widgets(window):
    """左栏直接子控件应与 CONTROLS.md 的行表对得上，不多不少。"""
    from PyQt5.QtWidgets import QWidget
    documented = {m for _, members in _documented_rows() for m in members}
    actual = {c.objectName() for c in left_column(window).children()
              if isinstance(c, QWidget)}
    assert actual - documented == set(), "有控件没被登记进行表（新增控件请补表）"
    assert documented - actual == set(), "行表里的控件不见了"


def test_overlays_are_hidden_and_span_window_width(window):
    for name in ("api_group", "todo_board"):
        wdg = getattr(window, name)
        assert wdg.isHidden(), f"{name} 默认应该是收起的"
        r = abs_rect(wdg, window)
        assert r[2] == window.width() - 12
        assert r[1] == 28 + (2 if name == "api_group" else 4)


def test_overlay_object_names_are_load_bearing(window):
    """两个浮层的 objectName 被样式表的选择器用着，改了会掉边框和底色。"""
    assert window.todo_board.objectName() == "TodoBoard"   # QFrame#TodoBoard{...}
    assert window.api_group.objectName() == "apiPanel"     # QFrame#apiPanel{...}


def test_business_file_has_no_layout_code():
    """`app_main.py` 里不该再有坐标代码——布局归 ui_main_build.py。

    剩下 4 个 `resize()` 都在独立对话框上（数据核对/审批/AI结果/案件搜索），
    不是主窗口。
    """
    src = (ROOT / "app_main.py").read_text(encoding="utf-8")
    assert "setGeometry" not in src, "业务文件里出现了 setGeometry"
    assert ".move(" not in src, "业务文件里出现了 move()"


# ============================================================================
# 外观（几何快照）
# ============================================================================

def test_geometry_matches_baseline(window):
    """逐控件比对最终矩形。

    失败时先看下面列出的差异：既可能是改界面时手滑挪了东西，也可能是**有意**
    改动 —— 后者请确认无误后执行

        UI_BASELINE_UPDATE=1 python -m pytest tests/test_ui_layout.py -k baseline
    """
    actual = named_rects(window)
    if os.environ.get("UI_BASELINE_UPDATE"):
        save_baseline(actual)
        pytest.skip(f"已重新生成基准：{BASELINE}")
    expected = load_baseline()

    missing = sorted(set(expected) - set(actual))
    added = sorted(set(actual) - set(expected))
    moved = {k: (expected[k], actual[k]) for k in set(expected) & set(actual)
             if expected[k] != actual[k]}
    assert not (missing or added or moved), (
        "界面几何与基准不一致：\n"
        f"  消失的控件: {missing}\n"
        f"  新增的控件: {added}\n"
        + "\n".join(f"  {k}: {v[0]} → {v[1]}" for k, v in sorted(moved.items())))


def _documented_rows():
    from ui_helpers import LEFT_ROWS
    return LEFT_ROWS
