# -*- coding: utf-8 -*-
"""todo_board.py —— 主窗口顶端「待办事项」菜单按钮所用的下拉看板 及 文书送达相关弹窗。

交互：顶栏右侧的“待办事项(N)”按钮，点击后在顶栏下方展开一个下拉面板（TodoBoard），
面板纵向列出每个案件的完整任务文字（自动换行，不被截断），点击某条即进入处理。
依赖 service_flow.py 的常量与日期工具（仅纯函数），不依赖 app_main，避免循环导入。
弹窗风格沿用 app_main 现有约定：QVBoxLayout(self) + 手动按钮行 + 绿色主按钮(#27ae60)。
"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import (
    QFrame, QLabel, QPushButton, QScrollArea, QWidget,
    QHBoxLayout, QVBoxLayout, QRadioButton, QButtonGroup,
    QLineEdit, QMessageBox, QDialog,
)

from service_flow import DOC_LABEL, METHODS, normalize_date, today_iso

# “带方框”的单选观感（圆点改方框）
_RADIO_SQUARE_QSS = """
QRadioButton::indicator { width: 13px; height: 13px; border: 1px solid #888;
                          border-radius: 2px; background: #fff; }
QRadioButton::indicator:hover { border-color: #27ae60; }
QRadioButton::indicator:checked { background: #27ae60; border-color: #27ae60; }
"""
_GREEN_BTN_QSS = "QPushButton{background:#27ae60;color:#fff;border:none;border-radius:3px;padding:5px 16px;font-weight:bold;} QPushButton:hover{background:#1e9a50;}"


# ===========================================================================
# 下拉待办看板（纵向列表，整条文字完整显示，可滚动）
# ===========================================================================
class TodoBoard(QFrame):
    taskClicked = pyqtSignal(str)  # 只发 case_id，由 MainWindow 重读 JSON 并 derive

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TodoBoard")
        self.setAttribute(Qt.WA_StyledBackground, True)  # 确保不透明背景生效
        self.setStyleSheet(
            "QFrame#TodoBoard{background:#ffffff;border:1px solid #b8d0b8;border-radius:4px;}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        head = QLabel("文书送达待办 — 点击任务进入处理（每小时自动刷新）")
        head.setStyleSheet("color:#2c5f2d;font-weight:bold;background:transparent;")
        outer.addWidget(head)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet("background:transparent;")

        self._inner = QWidget()
        self._inner.setStyleSheet("background:transparent;")
        self._col = QVBoxLayout(self._inner)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(4)
        self._col.setAlignment(Qt.AlignTop)
        self._scroll.setWidget(self._inner)
        outer.addWidget(self._scroll, 1)

        self.set_tasks([])

    # ---- 样式 ----
    @staticmethod
    def _btn_style(phase: str) -> str:
        base = ("QPushButton{background:#f2faf2;border:1px solid #27ae60;color:#1d6b1d;"
                "border-radius:4px;padding:4px 10px;text-align:left;}")
        hover = "QPushButton:hover{background:#27ae60;color:#fff;}"
        if phase == "decision_ask" or phase.endswith("_expired"):
            base = ("QPushButton{background:#fff7e0;border:1px solid #e0a800;color:#7a5200;"
                    "border-radius:4px;padding:4px 10px;text-align:left;}")
            hover = "QPushButton:hover{background:#e0a800;color:#fff;}"
        return base + hover

    def _clear(self):
        while self._col.count():
            item = self._col.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)  # 立即脱离父级，避免残留
                w.deleteLater()

    def set_tasks(self, rows):
        """rows: list[(case_id, derive结果dict)]；空 → 灰字占位。"""
        self._clear()
        if not rows:
            ph = QLabel("暂无文书送达待办事项")
            ph.setStyleSheet("color:#999;background:transparent;padding:6px;")
            self._col.addWidget(ph)
            return

        for case_id, d in rows:
            label = d.get("label") or case_id
            btn = QPushButton(self._inner)
            btn.setStyleSheet(self._btn_style(d.get("phase", "")))
            btn.setCursor(Qt.PointingHandCursor)
            # QPushButton 不支持自动换行：按可用宽度手动插入 \n 折行，保证完整显示
            fm = QFontMetrics(btn.font())
            avail = max(self.width() - 46, 200)
            cur, txt_lines = "", []
            for ch in label:
                if cur and fm.horizontalAdvance(cur + ch) > avail:
                    txt_lines.append(cur)
                    cur = ch
                else:
                    cur += ch
            if cur:
                txt_lines.append(cur)
            btn.setText("\n".join(txt_lines))
            btn.setToolTip(f"{label}\n点击进入处理")
            btn.setFixedHeight(len(txt_lines) * 22 + 10)
            btn.clicked.connect(
                lambda _=False, cid=case_id: self.taskClicked.emit(cid))
            self._col.addWidget(btn)


# ===========================================================================
# 送达确认（举证通知书 / 工伤认定告知书 复用）
# ===========================================================================
class DeliveryConfirmDialog(QDialog):
    def __init__(self, case_number: str, doc: str, parent=None,
                 prefill_method: str = None, prefill_deliver: str = "",
                 prefill_send: str = ""):
        super().__init__(parent)
        self.case_number = case_number
        self.doc = doc
        self._method = prefill_method if prefill_method in METHODS else ""
        self._deliver = ""
        self._send = ""

        self.setWindowTitle(f"{DOC_LABEL.get(doc, doc)}送达确认")
        self.resize(460, 300)
        self.setMinimumWidth(420)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        head = QLabel(f"案本号：{case_number}　·　{DOC_LABEL.get(doc, doc)}送达确认")
        head.setStyleSheet("color:#2c5f2d;font-weight:bold;")
        lay.addWidget(head)

        tip = QLabel(self._deadline_tip(doc))
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#888;font-size:9pt;")
        lay.addWidget(tip)

        # ---- 送达方式（带方框单选框）----
        group = QWidget(self)
        hl = QHBoxLayout(group)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        self._radio_map = {}
        self._btn_grp = QButtonGroup(self)
        for m in METHODS:
            rb = QRadioButton(m, group)
            rb.setStyleSheet(_RADIO_SQUARE_QSS)
            rb.toggled.connect(lambda _on, mm=m: self._on_method_toggled(mm, _on))
            self._radio_map[m] = rb
            self._btn_grp.addButton(rb)
            hl.addWidget(rb)
        hl.addStretch(1)
        lay.addWidget(group)

        # ---- 寄送时间（仅邮寄显示）----
        self.send_row = QWidget(self)
        sh = QHBoxLayout(self.send_row)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.addWidget(QLabel("寄送时间："))
        self.send_edit = QLineEdit(self.send_row)
        self.send_edit.setPlaceholderText("留空=不记录（仅邮寄可填，不影响倒计时）")
        sh.addWidget(self.send_edit, 1)
        lay.addWidget(self.send_row)

        # ---- 送达时间（选填；未填则只记录送达方式，不开始倒计时）----
        dr = QHBoxLayout()
        dr.addWidget(QLabel("送达时间："))
        self.deliver_edit = QLineEdit(self)
        self.deliver_edit.setPlaceholderText("选填，格式如 20260907；不填可点取消")
        dr.addWidget(self.deliver_edit, 1)
        lay.addLayout(dr)

        # ---- 按钮 ----
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel_btn = QPushButton("取消", self)
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        save_btn = QPushButton("保存", self)
        save_btn.setStyleSheet(_GREEN_BTN_QSS)
        save_btn.clicked.connect(self._on_save)
        btns.addWidget(save_btn)
        lay.addLayout(btns)

        # 预填
        if self._method:
            self._radio_map[self._method].setChecked(True)
        else:
            self._radio_map["现场"].setChecked(True)
        if prefill_deliver:
            self.deliver_edit.setText(prefill_deliver)
        if prefill_send:
            self.send_edit.setText(prefill_send)
        self._apply_send_visibility()

    @staticmethod
    def _deadline_tip(doc: str) -> str:
        if doc == "proof":
            return "期限：现场/邮寄/留置 = 送达时间 + 15 天；公告 = 送达时间 + 25 天（10 公告期 + 15 举证期）"
        return "期限：现场/邮寄/留置 = 送达时间 + 3 日；公告 = 送达时间 + 13 日（10 公告期 + 3 日）"

    def _on_method_toggled(self, method: str, on: bool):
        if on:
            self._method = method
            self._apply_send_visibility()

    def _apply_send_visibility(self):
        self.send_row.setVisible(self._method == "邮寄")
        if self._method != "邮寄":
            self.send_edit.clear()

    def _on_save(self):
        if not self._method:
            QMessageBox.warning(self, "提示", "请选择送达方式。")
            return
        raw = self.deliver_edit.text().strip()
        if raw:
            try:
                self._deliver = normalize_date(raw)
            except Exception as e:
                QMessageBox.warning(self, "送达时间格式错误", f"{e}")
                return
        else:
            self._deliver = ""  # 选填：不填则只记录送达方式，不开始倒计时
        send_raw = self.send_edit.text().strip()
        self._send = ""
        if self._method == "邮寄" and send_raw:
            try:
                self._send = normalize_date(send_raw)
            except Exception as e:
                QMessageBox.warning(self, "寄送时间格式错误", f"{e}")
                return
        self.accept()

    def get_method(self) -> str:
        return self._method

    def get_send_time(self) -> str:
        return self._send

    def get_deliver_time(self) -> str:
        return self._deliver


# ===========================================================================
# 邮寄送达状态追踪（仅邮寄、倒计时期间）
# ===========================================================================
class PostalTrackingDialog(QDialog):
    def __init__(self, case_number: str, doc: str, parent=None):
        super().__init__(parent)
        self.case_number = case_number
        self.doc = doc
        self._action = ""
        self._note = ""
        self._new_deliver = ""

        self.setWindowTitle(f"{DOC_LABEL.get(doc, doc)}送达状态追踪")
        self.resize(460, 250)
        self.setMinimumWidth(420)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        head = QLabel(f"案本号：{case_number}　·　{DOC_LABEL.get(doc, doc)}（邮寄）是否已经送达？")
        head.setStyleSheet("color:#2c5f2d;font-weight:bold;")
        lay.addWidget(head)

        opts = [
            ("resend", "1. 未送达，再次寄送"),
            ("switch", "2. 未送达，改用其它方式"),
            ("delivered", "3. 已经送达"),
        ]
        self._radios = {}
        self._grp = QButtonGroup(self)
        for key, text in opts:
            rb = QRadioButton(text, self)
            rb.setStyleSheet(_RADIO_SQUARE_QSS)
            rb.toggled.connect(lambda _on, k=key: self._on_option(k, _on))
            self._radios[key] = rb
            self._grp.addButton(rb)
            lay.addWidget(rb)

        # 备注（①②）
        self.note_row = QWidget(self)
        nh = QHBoxLayout(self.note_row)
        nh.setContentsMargins(0, 0, 0, 0)
        nh.addWidget(QLabel("备注："))
        self.note_edit = QLineEdit(self.note_row)
        self.note_edit.setPlaceholderText("选 1/2 时填写（选 3 时可不填）")
        nh.addWidget(self.note_edit, 1)
        lay.addWidget(self.note_row)

        # 新的送达时间（③）
        self.time_row = QWidget(self)
        th = QHBoxLayout(self.time_row)
        th.setContentsMargins(0, 0, 0, 0)
        th.addWidget(QLabel("新的送达时间："))
        self.time_edit = QLineEdit(self.time_row)
        self.time_edit.setPlaceholderText("YYYY-MM-DD 或 20260907")
        th.addWidget(self.time_edit, 1)
        lay.addWidget(self.time_row)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel_btn = QPushButton("取消", self)
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        save_btn = QPushButton("保存", self)
        save_btn.setStyleSheet(_GREEN_BTN_QSS)
        save_btn.clicked.connect(self._on_save)
        btns.addWidget(save_btn)
        lay.addLayout(btns)

        self.note_row.hide()
        self.time_row.hide()

    def _on_option(self, key: str, on: bool):
        if not on:
            return
        self._action = key
        self.note_row.setVisible(key in ("resend", "switch"))
        self.time_row.setVisible(key == "delivered")
        if key != "delivered":
            self.time_edit.clear()

    def _on_save(self):
        if not self._action:
            QMessageBox.warning(self, "提示", "请选择一项：①再次寄送 ②改用其它方式 ③已经送达。")
            return
        self._note = self.note_edit.text().strip()
        if self._action == "delivered":
            raw = self.time_edit.text().strip()
            try:
                self._new_deliver = normalize_date(raw)
            except Exception as e:
                QMessageBox.warning(self, "送达时间格式错误", f"{e}")
                return
        self.accept()

    def get_action(self) -> str:          # "resend" | "switch" | "delivered"
        return self._action

    def get_note(self) -> str:
        return self._note

    def get_new_deliver_time(self) -> str:  # ③ 使用
        return self._new_deliver


# ===========================================================================
# 制作工伤认定决定书确认（是/否）
# ===========================================================================
class DecisionConfirmDialog(QDialog):
    def __init__(self, case_number: str, parent=None):
        super().__init__(parent)
        self.case_number = case_number
        self._choice = False

        self.setWindowTitle("制作工伤认定决定书确认")
        self.resize(480, 170)
        self.setMinimumWidth(440)

        lay = QVBoxLayout(self)
        msg = QLabel(
            f"案本号 {case_number}\n\n"
            "该案件的《工伤认定告知书》已送达且期限届满。\n"
            "是否制作工伤认定决定书（案件审批表）？"
        )
        msg.setWordWrap(True)
        lay.addWidget(msg)

        btns = QHBoxLayout()
        btns.addStretch(1)
        no_btn = QPushButton("否", self)
        no_btn.clicked.connect(self.reject)
        btns.addWidget(no_btn)
        yes_btn = QPushButton("是", self)
        yes_btn.setStyleSheet(_GREEN_BTN_QSS)
        yes_btn.clicked.connect(self._on_yes)
        btns.addWidget(yes_btn)
        lay.addLayout(btns)

    def _on_yes(self):
        self._choice = True
        self.accept()

    def get_choice(self) -> bool:
        return self._choice
