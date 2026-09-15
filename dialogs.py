# -*- coding: utf-8 -*-
"""主窗口之外的对话框。

原先定义在 app_main.py 里（2026-09 搬出来）——它们只依赖 Qt 与 case_store，
跟 MainWindow 没有关系，独立成文件后既好找也好单测外围逻辑。
"""

import json
from typing import Any, Dict, Optional

from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QWidget, QTabWidget, QMessageBox,
)

from case_store import pack_case, unpack_case


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
