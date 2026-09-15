# -*- coding: utf-8 -*-
"""界面测试的公共工具：取绝对矩形、左栏行表、几何快照。

左栏行表是把 `refactor/CONTROLS.md` §五 的结构图变成数据 —— 行顺序和归属
一旦被改坏，测试就会报出来。
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "ui_geometry_baseline.json"

# 左栏 17 行：每行给出该行的「行首控件」和该行所有**直接子控件**的名字。
# 只列 left_column 的直接子控件（label_14 在 status_box 里、label_15 在
# alert_hint 里，都不是直接子控件，故不在此表）。
LEFT_ROWS = [
    ("提示区",         ("alert_hint", "status_box")),
    ("案件类型",       ("deathCaseCheckbox", "personalApplicationCheckbox")),
    ("案本号",         ("label_10", "lineEdit_2", "pushButton_6")),
    ("单位性质/证人",   ("unit_type_label", "unit_type_combo",
                        "witness_label", "witness_combo", "add_witness_btn")),
    ("角色单选",       ("radioButton", "radioButton_2",
                        "radioButton_3", "radioButton_4")),
    ("姓名/年龄/性别",  ("label_2", "name_pane", "label",
                        "age_pane", "label_11", "lineEdit")),
    ("身份证号",       ("label_3", "idnumer_pane", "status_box_id")),
    ("住址",           ("label_4", "textEdit")),
    ("电话/岗位/身份",  ("label_5", "lineEdit_4", "label_13",
                        "lineEdit_5", "identity_label", "identity_edit")),
    ("拟用条例",       ("label_6", "comboBox")),
    ("申请/受理时间",   ("lbl_apply", "apply_time_edit",
                        "lbl_accept", "accept_time_edit")),
    ("受伤/就诊时间",   ("lbl_injury", "injury_time_edit",
                        "lbl_visit", "visit_time_edit")),
    ("操作按钮",       ("pushButton_4", "pushButton", "pushButton_ai_review")),
    ("用人单位",       ("label_7", "company_pane", "pushButton_2")),
    ("用工单位",       ("label_8", "construction_company", "pushButton_3")),
    ("工地名称",       ("label_9", "construction_plant", "pushButton_5")),
    ("底部按钮",       ("pushButton_11", "pushButton_12", "pushButton_7")),
]

ROW_GAP = 18          # 左栏行距（MainWindowUI.ROW_GAP）
LEFT_TOP = 52         # 左栏首行的顶边 = 顶栏 28 + 留白 24
LEFT_WIDTH = 478      # 左栏占位宽，右栏从这里开始
RIGHT_WIDTH = 382
WINDOW_SIZE = (870, 850)


def abs_rect(widget, target):
    """控件相对 `target` 的矩形 (x, y, w, h)。

    直接读 `geometry()` 拿到的是相对**父控件**的坐标；控件被布局管理器塞进
    不同层级的容器之后，只有 `mapTo` 才是能跨版本比较的绝对位置。
    """
    from PyQt5.QtCore import QPoint
    p = widget.mapTo(target, QPoint(0, 0))
    g = widget.geometry()
    return p.x(), p.y(), g.width(), g.height()


def left_column(window):
    return window.left_column


def row_bounds(window, row):
    """一行的上下边（相对窗口）。row 是 LEFT_ROWS 里的一项。"""
    name, members = row
    rects = [abs_rect(getattr(window, m), window) for m in members]
    top = min(r[1] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    return name, top, bottom


def all_rows(window):
    return [row_bounds(window, row) for row in LEFT_ROWS]


def row_rects(window):
    """每一行里各控件的矩形，供「行内不重叠」用。"""
    out = []
    for name, members in LEFT_ROWS:
        out.append((name, [abs_rect(getattr(window, m), window) for m in members]))
    return out


#: 快照要跳过的浮层：它们是绝对定位的浮层，**内部**控件由各自的小布局管，
#: 而且只有被 show() 过一次之后内部布局才激活 —— 把内部算进快照就会让
#: 结果依赖「别的用例有没有点开过浮层」。浮层本身的位置仍然照常快照。
#: 注意这里写的是 objectName，不是变量名：`api_group` 的 objectName 是
#: `apiPanel`，`todo_board` 的是 `TodoBoard`（后者被 `QFrame#TodoBoard` 样式用着）。
OVERLAY_NAMES = ("apiPanel", "TodoBoard")


def named_rects(window):
    """主界面具名控件的绝对矩形，用于几何快照。

    跳过两类：
    - `qt_*` 开头的 Qt 内部控件（滚动条容器之类），它们的 objectName 会重复、
      而且尺寸完全跟着父控件走；
    - 两个浮层的**内部**（见 OVERLAY_NAMES）。
    """
    from PyQt5.QtWidgets import QWidget
    out = {}

    def walk(w):
        for c in w.children():
            if not isinstance(c, QWidget):
                continue
            name = c.objectName()
            if name and not name.startswith("qt_"):
                out[name] = list(abs_rect(c, window))
            if c is not window and name in OVERLAY_NAMES:
                continue          # 浮层只记它自己，不进内部
            walk(c)

    walk(window)
    return dict(sorted(out.items()))


def load_baseline():
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def save_baseline(data):
    BASELINE.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
