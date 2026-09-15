# -*- coding: utf-8 -*-
"""主窗口之外的对话框。

原先定义在 app_main.py 里（2026-09 搬出来）——它们只依赖 Qt 与 case_store，
跟 MainWindow 没有关系，独立成文件后既好找也好单测外围逻辑。
"""

import json
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QWidget, QTabWidget, QMessageBox, QListWidget, QListWidgetItem,
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


class AIReviewResultDialog(QDialog):
    """AI 法律审查结果窗口：审查结果 / 缺失问题两个页签，可勾选问题插进笔录。

    原先这段是 `MainWindow.show_ai_review_result()` 里 129 行现场构建代码，而且
    把问题列表控件挂到 MainWindow 上（`self.question_list_widget`），再让
    `insert_selected_questions` 回头去读它——2026-09 收进这个类，列表控件归它自己。

    三个动作通过信号抛回调用方（插入 / 复制 / 保存），对话框本身不碰业务。
    """

    insertRequested = pyqtSignal()
    copyRequested = pyqtSignal(str)
    saveRequested = pyqtSignal(dict)

    def __init__(self, parsed_result: dict, parent=None):
        super().__init__(parent)
        self.parsed_result = parsed_result
        self._build_ui(parsed_result)

    def _build_ui(self, parsed_result: dict):
        self.setWindowTitle("AI法律审查结果")
        self.resize(700, 600)

        layout = QVBoxLayout(self)

        # 标题
        title = QLabel("AI法律审查报告")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # 创建标签页
        tab_widget = QTabWidget()

        # 标签1：审查结果
        review_tab = QWidget()
        review_layout = QVBoxLayout()

        review_label = QLabel("审查结果分析：")
        review_label.setStyleSheet("font-weight: bold;")
        review_layout.addWidget(review_label)

        # 审查结果显示区域
        review_text_edit = QTextEdit()
        review_text_edit.setReadOnly(True)
        review_text_edit.setPlainText(parsed_result["审查结果"])
        review_text_edit.setMinimumHeight(300)
        review_layout.addWidget(review_text_edit)

        review_tab.setLayout(review_layout)
        tab_widget.addTab(review_tab, "审查结果")

        # 标签2：缺失问题（如果有）
        if parsed_result["缺失问题"]:
            questions_tab = QWidget()
            questions_layout = QVBoxLayout()

            questions_label = QLabel(f"发现 {len(parsed_result['缺失问题'])} 个缺失问题，请勾选需要添加到笔录的问题：")
            questions_label.setStyleSheet("font-weight: bold; color: #e74c3c;")
            questions_layout.addWidget(questions_label)

            # 创建问题列表（带复选框）
            self.question_list_widget = QListWidget()

            for question in parsed_result["缺失问题"]:
                item = QListWidgetItem(question)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)  # 默认未选中
                self.question_list_widget.addItem(item)

            questions_layout.addWidget(self.question_list_widget)

            # 全选/全不选按钮
            select_buttons_layout = QHBoxLayout()

            btn_select_all = QPushButton("全选")
            btn_select_all.clicked.connect(lambda: self.select_all_questions(True))

            btn_select_none = QPushButton("全不选")
            btn_select_none.clicked.connect(lambda: self.select_all_questions(False))

            select_buttons_layout.addWidget(btn_select_all)
            select_buttons_layout.addWidget(btn_select_none)
            select_buttons_layout.addStretch()

            questions_layout.addLayout(select_buttons_layout)

            questions_tab.setLayout(questions_layout)
            tab_widget.addTab(questions_tab, f"缺失问题 ({len(parsed_result['缺失问题'])})")

        layout.addWidget(tab_widget)

        # 按钮区域
        button_layout = QHBoxLayout()

        # 插入到笔录按钮（只在有问题时显示）
        if parsed_result.get("缺失问题"):
            btn_insert = QPushButton("插入选中问题到笔录")
            btn_insert.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
            btn_insert.clicked.connect(self.insertRequested.emit)
            button_layout.addWidget(btn_insert)

        btn_copy = QPushButton("复制结果")
        btn_copy.clicked.connect(lambda: self.copyRequested.emit(parsed_result["审查结果"]))

        btn_save = QPushButton("保存报告")
        btn_save.clicked.connect(lambda: self.saveRequested.emit(parsed_result))

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)

        button_layout.addWidget(btn_copy)
        button_layout.addWidget(btn_save)
        button_layout.addWidget(btn_close)

        layout.addLayout(button_layout)

    def select_all_questions(self, select_all: bool):
        """全选或全不选问题"""
        if not hasattr(self, 'question_list_widget'):
            return

        for i in range(self.question_list_widget.count()):
            item = self.question_list_widget.item(i)
            item.setCheckState(Qt.Checked if select_all else Qt.Unchecked)

    def selected_questions(self) -> List[str]:
        """已勾选的问题文本（按列表顺序）"""
        if not hasattr(self, 'question_list_widget'):
            return []
        return [self.question_list_widget.item(i).text()
                for i in range(self.question_list_widget.count())
                if self.question_list_widget.item(i).checkState() == Qt.Checked]
