# -*- coding: utf-8 -*-
"""材料清单控件（原先定义在 app_main.py 里）。

单独成文件的原因：界面构建拆到了 `ui_main_build.py`，而它需要建这个控件。
`ui_main_build.py` 反过来被 `app_main.py` 导入，两边互相 import 会成环——
尤其是 `python app_main.py` 直接启动时该模块名是 `__main__`，延迟 import
"app_main" 会再加载一份模块、造出两个不同的类。
"""

from typing import Any, Dict, List

from PyQt5.Qt import *  # noqa: F401,F403
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QCheckBox, QLineEdit,
)


class MaterialListWidget(QWidget):
    """替代原有 QTextEdit 的材料管理组件。

    每行: [☑/☐ 复选框] [材料名称] [备注输入框]
    - 勾选 = 已提供
    - 未勾选 = 缺失，生成笔录时AI会追问
    - 备注 = 对该材料的补充说明
    """

    materials_changed = pyqtSignal()  # 材料变更信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = []  # [{name, provided, notes, widget_refs}]
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        # 滚动区域
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("""
            QScrollArea {
                border: 1px solid #ccc;
                border-radius: 2px;
                background-color: #fafafa;
            }
        """)

        # 内容容器
        self._container = QWidget()
        self._container.setStyleSheet("background-color: #fafafa;")
        self._row_layout = QVBoxLayout(self._container)
        self._row_layout.setContentsMargins(4, 2, 4, 2)
        self._row_layout.setSpacing(2)
        self._row_layout.addStretch()  # 底部弹簧，把行推到顶部

        self.scroll.setWidget(self._container)
        layout.addWidget(self.scroll)

    REQUIRED_COLOR = "#e75480"    # 必要证据：粉红
    POSSIBLE_COLOR = "#000000"    # 可能证据：黑

    @staticmethod
    def _edit_css(color: str) -> str:
        return f"""
            QLineEdit {{
                font-size: 8pt;
                color: {color};
                border: 1px solid #ddd;
                border-radius: 1px;
                padding: 1px 3px;
                background-color: #fff;
            }}
            QLineEdit:focus {{
                border-color: #3498db;
            }}
        """

    def _make_row(self, name: str = "", provided: bool = False, notes: str = "",
                  required: bool = False):
        """创建一行材料条目；required=True 时名称用粉红标出"""
        row = QWidget()
        row.setFixedHeight(23)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(3)

        # 复选框
        cb = QCheckBox()
        cb.setChecked(provided)
        cb.setFixedWidth(18)
        cb.setToolTip("勾选=已提供  |  不勾选=缺失")
        cb.toggled.connect(self._on_changed)

        # 材料名称输入框（可编辑）
        name_edit = QLineEdit(name if name else "")
        name_edit.setPlaceholderText("材料名称...")
        name_edit.setStyleSheet(
            self._edit_css(self.REQUIRED_COLOR if required else self.POSSIBLE_COLOR))
        name_edit.setMinimumWidth(140)

        # 名字放不下时靠悬停看全（自动生成的证据名可以很长），随文本更新
        def _sync_tip(txt, edit=name_edit, req=required):
            edit.setToolTip(("必要证据\n" if req else "") + (txt or "材料名称"))

        _sync_tip(name)
        name_edit.textChanged.connect(_sync_tip)
        name_edit.textChanged.connect(self._on_changed)

        # 备注输入框（一律黑色，不跟着必要项变粉）
        note_edit = QLineEdit()
        note_edit.setText(notes)
        note_edit.setPlaceholderText("备注...")
        note_edit.setStyleSheet(self._edit_css(self.POSSIBLE_COLOR))
        note_edit.textChanged.connect(self._on_changed)

        h.addWidget(cb)
        # 名称栏要比备注宽：自动生成的证据名可能很长（如
        # 「近亲属关系证明（户口簿/结婚证等）」），挤窄了就看不清
        h.addWidget(name_edit, 3)
        h.addWidget(note_edit, 2)

        return row, cb, name_edit, note_edit

    def add_row(self, name: str = "", provided: bool = False, notes: str = "",
                required: bool = False, generated: bool = False):
        """在末尾添加一行。

        generated=True 表示这行是按证据清单自动生成（换条例时会被重建）；
        案件自带的、以及手工添加的行不是 generated，重建时保留。
        """
        row, cb, name_edit, note = self._make_row(name, provided, notes, required)
        # 在 stretch 之前插入
        self._row_layout.insertWidget(self._row_layout.count() - 1, row)

        item = {
            "name": name,
            "provided": provided,
            "notes": notes,
            "_cb": cb,
            "_name_edit": name_edit,
            "_note": note,
            "_row": row,
            "_required": required,
            "_generated": generated,
        }
        self._rows.append(item)

    def apply_evidence_list(self, items):
        """按证据清单重建「自动生成」的行；已勾选状态按名称沿用。

        案件自带/手工添加的行不动，同名项也不重复添加。
        """
        kept_state = {r["_name_edit"].text(): (r["_cb"].isChecked(), r["_note"].text())
                      for r in self._rows}

        for r in [r for r in self._rows if r.get("_generated")]:
            r["_row"].setParent(None)
            self._rows.remove(r)

        # 必须在删掉旧的自动行之后再算——否则刚被删掉的名字仍算「已存在」，
        # 新的清单里同名项会被跳过，永远加不回来（切到工亡时死亡证明就是这样丢的）
        existing = {r["_name_edit"].text() for r in self._rows}

        for name, required in items:
            if name in existing:
                continue
            provided, notes = kept_state.get(name, (False, ""))
            self.add_row(name, provided, notes, required=required, generated=True)
        self._on_changed()

    def set_materials(self, data: List[Dict[str, Any]]):
        """批量设置材料列表"""
        self.clear()
        for item in data:
            self.add_row(
                name=item.get("name", ""),
                provided=item.get("provided", False),
                notes=item.get("notes", "")
            )

    def get_materials(self) -> List[Dict[str, Any]]:
        """获取所有材料数据（同步UI状态）"""
        result = []
        for row in self._rows:
            result.append({
                "name": row["_name_edit"].text(),
                "provided": row["_cb"].isChecked(),
                "notes": row["_note"].text(),
            })
        return result

    def clear(self):
        """清空所有行"""
        for row in self._rows:
            row["_row"].setParent(None)
        self._rows.clear()

    def get_summary_text(self) -> str:
        """生成可复制的文本摘要"""
        lines = []
        for i, r in enumerate(self.get_materials(), 1):
            status = "✓" if r["provided"] else "✗"
            line = f"{status} {i}. {r['name']}"
            if r["notes"]:
                line += f"（{r['notes']}）"
            lines.append(line)
        return "\n".join(lines)

    def copy_to_clipboard(self):
        """复制材料摘要到剪贴板"""
        text = self.get_summary_text()
        QApplication.clipboard().setText(text)

    def _on_changed(self):
        """复选框或备注变更时发出信号"""
        self.materials_changed.emit()
