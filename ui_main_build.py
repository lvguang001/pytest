# -*- coding: utf-8 -*-
"""主界面构建（布局管理器版）。

历史包袱：主界面原先由 Qt Designer 的绝对坐标打底，再靠十几个
`_move_xxx / _compact_xxx / _align_xxx / _grow_xxx / _relocate_xxx` 补丁函数
手工挪控件，那些函数全堆在 MainWindow 里，还有隐式的调用顺序依赖
（见旧 `__init__` 末尾的注释）。本次改成由本类一次性用布局管理器摆好。

本类只做两件事：

1. 建控件 —— `.ui` 底图的控件 + 原先散在各 `_setup_xxx_ui` 里代码创建的控件；
2. 摆位置 —— QVBoxLayout / QHBoxLayout / QGridLayout。

**不含业务逻辑、不连任何信号**（信号统一在 `MainWindow._connect_signals()`）。
控件**变量名与重构前完全一致**——业务代码有 80+ 处直接读 `self.xxx`。

横向位置沿用重构前的实测值（用行内 spacing 表达），纵向结构改成
「17 个行布局 + 18px 均匀行距」。两处相对重构前的**有意变化**：

- 「谈话笔录」按钮宽度 141 → 75（与同行两个按钮一致，AI审查 因此左移到 x=290）；
- 提示区/身份证提示位的方框（status_box / status_box_id）不再靠 `raise_()`
  垫在标签下面，改成「QFrame 装 QLabel」——这样 `_set_status()` 给标签设
  颜色样式时不会把边框冲掉。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox,
    QTextEdit, QFrame, QGroupBox, QVBoxLayout, QHBoxLayout,
)

from ui_main_window import Ui_Form
from todo_board import TodoBoard
from main import PasswordLineEdit


class MainWindowUI(QWidget, Ui_Form):
    """主界面：只建控件、只摆位置。"""

    WIN_W, WIN_H = 870, 850
    TOP_BAR_H = 28
    LEFT_W = 478          # 左栏占位宽（右栏从这里开始）
    RIGHT_W = 382         # 右栏宽
    ROW_GAP = 18          # 左栏行距
    LEFT_TOP_PAD = 24     # 左栏顶部留白（顶栏之下）
    RIGHT_TOP_PAD = 3     # 右栏顶部留白
    # 右栏底部留白：让两个分组框的底边与左栏最后一行齐平
    RIGHT_BOTTOM_PAD = 56
    RIGHT_GROUP_SPLIT = (435, 321)   # 两个分组框的高度比（重构前的实测值）

    # 各控件重构前的高度（.ui 用绝对坐标给的，改成布局后必须显式钉住，
    # 否则布局按 sizeHint 收缩/撑开，各行高度全变）
    _SIZES = {
        # —— 顶部提示区 ——
        'label_15': (60, 16),
        'label_14': (341, 51),
        # —— 案件类型 ——
        'deathCaseCheckbox': (91, 31),
        'personalApplicationCheckbox': (111, 31),
        # —— 案本号 ——
        'label_10': (60, 16),
        'lineEdit_2': (180, 20),
        'pushButton_6': (131, 23),
        # —— 单位性质 / 证人 ——
        'unit_type_label': (64, 16),
        'unit_type_combo': (100, 24),
        'witness_label': (60, 20),
        'witness_combo': (110, 24),
        'add_witness_btn': (68, 24),
        # —— 角色单选 ——
        'radioButton': (47, 16),
        'radioButton_2': (47, 16),
        'radioButton_3': (47, 16),
        'radioButton_4': (60, 16),
        # —— 姓名 / 年龄 / 性别 ——
        'label_2': (60, 16), 'name_pane': (66, 20),
        'label': (36, 16), 'age_pane': (65, 20),
        'label_11': (36, 16), 'lineEdit': (66, 20),
        # —— 身份证号 ——
        'label_3': (60, 16), 'idnumer_pane': (191, 20),
        'label_12': (141, 20),
        # —— 住址 ——
        'label_4': (60, 16), 'textEdit': (361, 49),
        # —— 电话 / 岗位 / 身份 ——
        'label_5': (60, 16), 'lineEdit_4': (96, 20),
        'label_13': (60, 16), 'lineEdit_5': (70, 20),
        'identity_label': (44, 16), 'identity_edit': (57, 20),
        # —— 拟用条例 ——
        'label_6': (60, 16), 'comboBox': (361, 20),
        # —— 申请 / 受理 / 受伤 / 就诊 时间 ——
        'lbl_apply': (60, 20), 'apply_time_edit': (142, 22),
        'lbl_accept': (60, 20), 'accept_time_edit': (142, 22),
        'lbl_injury': (60, 20), 'injury_time_edit': (142, 22),
        'lbl_visit': (60, 20), 'visit_time_edit': (142, 22),
        # —— 操作按钮（谈话笔录 141→75，与同行一致）——
        'pushButton_4': (75, 23), 'pushButton': (75, 23),
        'pushButton_ai_review': (75, 23),
        # —— 单位信息三行 ——
        'label_7': (54, 12), 'company_pane': (301, 22), 'pushButton_2': (75, 23),
        'label_8': (54, 12), 'construction_company': (301, 22), 'pushButton_3': (75, 23),
        'label_9': (54, 12), 'construction_plant': (301, 22), 'pushButton_5': (75, 23),
        # —— 底部按钮 ——
        'pushButton_11': (75, 23), 'pushButton_12': (81, 23), 'pushButton_7': (75, 23),
        # —— 顶栏（横排布局不会自动给出这些尺寸）——
        'config_toggle_btn': (28, 24), 'top_status_label': (430, 20),
        'todo_btn': (128, 24),
    }

    def __init__(self, parent=None, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.setupUi(self)            # .ui 底图（里面的 setGeometry 随后被布局接管）
        self._apply_window_size()
        self._create_extra_widgets()  # .ui 之外的控件
        self._pin_sizes()             # 钉住宽高
        self._build_layout()          # 布局管理器摆放

    # ========================================================================
    # 尺寸
    # ========================================================================

    def _apply_window_size(self):
        """窗口固定 870×850（最小/最大都锁死，与重构前一致）"""
        self.setMinimumSize(self.WIN_W, self.WIN_H)
        self.setMaximumSize(self.WIN_W, self.WIN_H)
        self.resize(self.WIN_W, self.WIN_H)

    def _pin_sizes(self):
        for name, (w, h) in self._SIZES.items():
            getattr(self, name).setFixedSize(w, h)

    # ========================================================================
    # .ui 之外的控件（原先由 MainWindow 的 _setup_xxx_ui 创建，现统一搬到这里）
    # 文案/选项等依赖 app_main 常量的内容由 MainWindow 在 __init__ 里补
    # ========================================================================

    def _create_extra_widgets(self):
        self._create_unit_identity_widgets()
        self._create_witness_widgets()
        self._create_time_widgets()
        self._create_top_bar_widgets()
        self._create_config_panel_widgets()
        self._create_right_panel_widgets()
        self._create_todo_widgets()

    def _create_unit_identity_widgets(self):
        """案件级「单位性质」下拉 + 共享人字段「身份」输入行"""
        self.unit_type_label = QLabel("单位性质：", self)
        self.unit_type_label.setObjectName("unit_type_label")

        self.unit_type_combo = QComboBox(self)
        self.unit_type_combo.setObjectName("unit_type_combo")
        self.unit_type_combo.setEditable(True)
        self.unit_type_combo.setInsertPolicy(QComboBox.NoInsert)
        self.unit_type_combo.setToolTip("案件用人单位性质：企业 / 机关（公务员） / 事业单位")

        self.identity_label = QLabel("身份：", self)
        self.identity_label.setObjectName("identity_label")

        self.identity_edit = QLineEdit(self)
        self.identity_edit.setObjectName("identity_edit")
        self.identity_edit.setToolTip("该谈话人身份：职工 / 公务员 / 事业编制工作人员 等")

    def _create_witness_widgets(self):
        """证人编号下拉 + 「添加证人」按钮（常显，不随角色隐藏）"""
        self.witness_label = QLabel("证人编号：", self)
        self.witness_label.setObjectName("witness_label")

        self.witness_combo = QComboBox(self)
        self.witness_combo.setObjectName("witness_combo")

        self.add_witness_btn = QPushButton("添加证人", self)
        self.add_witness_btn.setObjectName("add_witness_btn")

    def _create_time_widgets(self):
        """四个案件时间字段（申请 / 受理 / 受伤 / 就诊）"""
        self.lbl_apply = QLabel("申请时间：", self)
        self.lbl_apply.setObjectName("lbl_apply")
        self.apply_time_edit = QLineEdit(self)
        self.apply_time_edit.setObjectName("apply_time_edit")
        self.apply_time_edit.setToolTip("输入8位日期如20260816；留空则使用系统当前日期")

        self.lbl_accept = QLabel("受理时间：", self)
        self.lbl_accept.setObjectName("lbl_accept")
        self.accept_time_edit = QLineEdit(self)
        self.accept_time_edit.setObjectName("accept_time_edit")
        self.accept_time_edit.setToolTip("输入8位日期如20260816；留空则使用系统当前日期")

        self.lbl_injury = QLabel("受伤时间：", self)
        self.lbl_injury.setObjectName("lbl_injury")
        self.injury_time_edit = QLineEdit(self)
        self.injury_time_edit.setObjectName("injury_time_edit")
        self.injury_time_edit.setToolTip("年月日时分，如 202609051105；留空则不填")

        self.lbl_visit = QLabel("就诊时间：", self)
        self.lbl_visit.setObjectName("lbl_visit")
        self.visit_time_edit = QLineEdit(self)
        self.visit_time_edit.setObjectName("visit_time_edit")
        self.visit_time_edit.setToolTip("年月日时分，如 202609051105；留空则不填")

    def _create_top_bar_widgets(self):
        """顶栏：⚙ 配置开关 + 状态文字 + 待办事项按钮"""
        self.config_toggle_btn = QPushButton("⚙")
        self.config_toggle_btn.setObjectName("config_toggle_btn")
        self.config_toggle_btn.setToolTip("显示/隐藏用户配置")
        self.config_toggle_btn.setCursor(Qt.PointingHandCursor)
        self.config_toggle_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; font-size: 14px; }"
            "QPushButton:hover { background-color: #d0d0d0; border-radius: 3px; }"
        )

        self.top_status_label = QLabel("")
        self.top_status_label.setObjectName("top_status_label")
        self.top_status_label.setStyleSheet(
            "color: #888; background: transparent; border: none;")

        self.todo_btn = QPushButton("待办事项(0)")
        self.todo_btn.setObjectName("todo_btn")
        self.todo_btn.setToolTip("展开/收起文书送达待办事项")
        self.todo_btn.setCursor(Qt.PointingHandCursor)
        self.todo_btn.setStyleSheet(
            "QPushButton{background:#eef6ee;border:1px solid #27ae60;border-radius:4px;"
            "color:#1d6b1d;font-weight:bold;}"
            "QPushButton:hover{background:#27ae60;color:#fff;}"
        )

    def _create_config_panel_widgets(self):
        """右上角「用户配置」下拉面板（浮层，默认隐藏）"""
        self.api_group = QFrame(self)
        self.api_group.setObjectName("apiPanel")
        self.api_group.setAttribute(Qt.WA_StyledBackground, True)
        self.api_group.setStyleSheet(
            "QFrame#apiPanel{background:#ffffff;border:1px solid #b8d0b8;border-radius:4px;}"
        )

        panel = QVBoxLayout(self.api_group)
        panel.setContentsMargins(12, 10, 12, 10)
        panel.setSpacing(6)

        head = QLabel("用户配置（修改后自动保存）", self.api_group)
        head.setStyleSheet("color:#2c5f2d;font-weight:bold;background:transparent;")
        panel.addWidget(head)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("用户：", self.api_group))
        self.api_user_combo = QComboBox(self.api_group)
        self.api_user_combo.setObjectName("api_user_combo")
        self.api_user_combo.setEditable(True)
        self.api_user_combo.setPlaceholderText("输入用户名")
        row1.addWidget(self.api_user_combo, 1)
        panel.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("密钥：", self.api_group))
        self.api_key_input = PasswordLineEdit(self.api_group)
        self.api_key_input.setObjectName("api_key_input")
        self.api_key_input.setPlaceholderText("输入API密钥")
        row2.addWidget(self.api_key_input, 1)
        panel.addLayout(row2)

        hint = QLabel("输入后自动保存，下次启动自动使用；仅在你修改输入时更新。",
                      self.api_group)
        hint.setStyleSheet("color:#888;font-size:9pt;background:transparent;")
        panel.addWidget(hint)

    def _create_right_panel_widgets(self):
        """右栏两个分组框：案件申请陈述 / 目前提供的材料分类"""
        # —— 案件申请陈述 ——
        # 注意：这一栏已从数据链路上断开——框里填什么都不再进入案件数据，
        # 也不再回填。受伤经过改从数据模型的「受伤经过」取。界面暂留待重新设计。
        self.statement_group = QGroupBox("案件申请陈述", self)
        self.statement_group.setObjectName("statement_group")
        self.statement_group.setFont(QFont("微软雅黑", 9))

        # 上边距给 0：QGroupBox 的内容区本来就落在标题下方约 18px 处，
        # 再给 18 会白多一段（分组框的标题高度不在 layout 的 margin 里）
        stmt = QVBoxLayout(self.statement_group)
        stmt.setContentsMargins(7, 0, 7, 5)
        stmt.setSpacing(4)

        self.statement_edit = QTextEdit(self.statement_group)
        self.statement_edit.setObjectName("statement_edit")
        self.statement_edit.setPlaceholderText("在此输入案件申请陈述...")
        self.statement_edit.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ccc;
                border-radius: 2px;
                background-color: #fafafa;
                font-size: 9pt;
            }
            QTextEdit:focus {
                border-color: #3498db;
                background-color: #fff;
            }
        """)
        stmt.addWidget(self.statement_edit, 1)

        stmt_btns = QHBoxLayout()
        stmt_btns.setSpacing(5)
        self.stmt_copy_btn = QPushButton("复制", self.statement_group)
        self.stmt_copy_btn.setFixedSize(45, 23)
        self.stmt_clear_btn = QPushButton("清空", self.statement_group)
        self.stmt_clear_btn.setFixedSize(45, 23)
        stmt_btns.addWidget(self.stmt_copy_btn)
        stmt_btns.addWidget(self.stmt_clear_btn)
        stmt_btns.addStretch(1)
        stmt.addLayout(stmt_btns)

        # —— 目前提供的材料分类 ——
        self.material_group = QGroupBox("目前提供的材料分类", self)
        self.material_group.setObjectName("material_group")
        self.material_group.setFont(QFont("微软雅黑", 9))

        mat = QVBoxLayout(self.material_group)
        mat.setContentsMargins(7, 0, 7, 5)   # 上边距同上：标题高度由 QGroupBox 自己占
        mat.setSpacing(4)

        from app_main import MaterialListWidget  # noqa: F401  （延迟导入避免循环依赖）

        self.material_list = MaterialListWidget(self.material_group)
        self.material_list.setObjectName("material_list")
        mat.addWidget(self.material_list, 1)

        mat_btns = QHBoxLayout()
        mat_btns.setSpacing(5)
        self.mat_copy_btn = QPushButton("复制", self.material_group)
        self.mat_copy_btn.setFixedSize(45, 23)
        self.mat_clear_btn = QPushButton("清空", self.material_group)
        self.mat_clear_btn.setFixedSize(45, 23)
        self.mat_add_btn = QPushButton("新增", self.material_group)
        self.mat_add_btn.setFixedSize(45, 23)
        mat_btns.addWidget(self.mat_copy_btn)
        mat_btns.addWidget(self.mat_clear_btn)
        mat_btns.addWidget(self.mat_add_btn)
        mat_btns.addStretch(1)
        mat.addLayout(mat_btns)

    def _create_todo_widgets(self):
        """待办看板浮层（默认隐藏；定时刷新与信号由 MainWindow 负责）

        注意：不要改 todo_board 的 objectName——TodoBoard 自己在 __init__ 里设成
        「TodoBoard」并用 `QFrame#TodoBoard{...}` 定样式，改了就丢边框和底色。
        """
        self.todo_board = TodoBoard(self)

    # ========================================================================
    # 布局
    # ========================================================================

    def _row(self, *pairs, left=0):
        """横排一行。pairs 为 (控件, 它后面的间距) —— 沿用重构前的实测横向位置。

        left 是行首缩进（少数几行不从最左边开始，如两个按钮行、案件类型行）。
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)          # 间距一律显式给，避免与 spacing 叠加
        if left:
            row.addSpacing(left)
        for i, (widget, gap) in enumerate(pairs):
            row.addWidget(widget)
            if i < len(pairs) - 1:
                row.addSpacing(gap)
        row.addStretch(1)
        return row

    def _build_layout(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        root.addLayout(self._build_body(), 1)
        self._build_overlays()

    # —— 顶栏 ——

    def _build_top_bar(self):
        self.top_bar = QLabel(self)
        self.top_bar.setObjectName("top_bar")
        self.top_bar.setFixedHeight(self.TOP_BAR_H)
        self.top_bar.setStyleSheet(
            "background-color: #e8e8e8; border-bottom: 1px solid #ccc;")

        bar = QHBoxLayout(self.top_bar)
        bar.setContentsMargins(4, 2, 6, 2)
        bar.setSpacing(5)
        bar.addWidget(self.config_toggle_btn)          # 28×24
        bar.addWidget(self.top_status_label)
        bar.addStretch(1)
        bar.addWidget(self.todo_btn)                   # 128×24，贴右
        return self.top_bar

    # —— 主体：左栏 + 右栏 ——

    def _build_body(self):
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_left_column())
        body.addWidget(self._build_right_column())
        body.addStretch(1)          # 剩下的 10px 留在最右侧（右栏左缘因此对齐 x=478）
        return body

    def _build_left_column(self):
        left = QWidget(self)
        left.setObjectName("left_column")
        left.setFixedWidth(self.LEFT_W)

        col = QVBoxLayout(left)
        col.setContentsMargins(10, self.LEFT_TOP_PAD, 10, 0)
        col.setSpacing(self.ROW_GAP)

        col.addLayout(self._row_alert())            # ① 提示区
        col.addLayout(self._row_case_type())        # ② 工亡 / 个人案件
        col.addLayout(self._row_case_no())          # ③ 案本号
        col.addLayout(self._row_unit_witness())     # ④ 单位性质 / 证人编号
        col.addLayout(self._row_roles())            # ⑤ 角色单选
        col.addLayout(self._row_name())             # ⑥ 姓名 / 年龄 / 性别
        col.addLayout(self._row_id_card())          # ⑦ 身份证号
        col.addLayout(self._row_address())          # ⑧ 住址
        col.addLayout(self._row_contact())          # ⑨ 电话 / 岗位 / 身份
        col.addLayout(self._row_regulation())       # ⑩ 拟用条例
        col.addLayout(self._row_time(0))            # ⑪ 申请 / 受理时间
        col.addLayout(self._row_time(1))            # ⑫ 受伤 / 就诊时间
        col.addLayout(self._row_action_buttons())   # ⑬ 身份证导入 / 谈话笔录 / AI审查
        col.addLayout(self._row_unit('company'))    # ⑭ 用人单位
        col.addLayout(self._row_unit('employer'))   # ⑮ 用工单位
        col.addLayout(self._row_unit('plant'))      # ⑯ 工地名称
        col.addLayout(self._row_doc_buttons())      # ⑰ 案件审批表 / 谈话通知书 / 工伤告知书
        col.addStretch(1)
        return left

    # —— 左栏逐行 ——

    def _row_alert(self):
        """① 信息提示区：「信息提示：」+ 带框的主状态区（label_14）"""
        self.status_box = QFrame(self)
        self.status_box.setObjectName("status_box")
        self.status_box.setFixedSize(353, 63)
        self.status_box.setStyleSheet(
            "#status_box { border: 1px solid #b8b8b8; border-radius: 4px;"
            " background: #fcfcfc; }")
        box_lay = QVBoxLayout(self.status_box)
        box_lay.setContentsMargins(6, 6, 6, 6)   # 框比标签大 6px（重构前的 pad）
        box_lay.addWidget(self.label_14)

        # 左侧「信息提示：」与框内首行对齐（不参与行内垂直居中）
        hint = QWidget(self)
        hint_lay = QVBoxLayout(hint)
        hint_lay.setContentsMargins(0, 6, 0, 0)
        hint_lay.setSpacing(0)
        hint_lay.addWidget(self.label_15)
        hint_lay.addStretch(1)

        return self._row((hint, 4), (self.status_box, 0))

    def _row_case_type(self):
        """② 工亡案件 / 个人案件"""
        return self._row((self.deathCaseCheckbox, 19),
                         (self.personalApplicationCheckbox, 0), left=70)

    def _row_case_no(self):
        """③ 案本号：标签 + 输入框 + 搜索"""
        return self._row((self.label_10, 10), (self.lineEdit_2, 40),
                         (self.pushButton_6, 0))

    def _row_unit_witness(self):
        """④ 单位性质 + 证人编号 + 添加证人（同一行，均常显）"""
        return self._row((self.unit_type_label, 6), (self.unit_type_combo, 8),
                         (self.witness_label, 6), (self.witness_combo, 6),
                         (self.add_witness_btn, 0), left=0)

    def _row_roles(self):
        """⑤ 本人 / 证人 / 法人 / 家属"""
        return self._row((self.radioButton, 28), (self.radioButton_2, 27),
                         (self.radioButton_3, 28), (self.radioButton_4, 0), left=62)

    def _row_name(self):
        """⑥ 姓名 / 年龄 / 性别"""
        return self._row((self.label_2, 6), (self.name_pane, 28),
                         (self.label, 14), (self.age_pane, 35),
                         (self.label_11, 14), (self.lineEdit, 0))

    def _row_id_card(self):
        """⑦ 身份证号 + 校验提示位（带框）"""
        self.status_box_id = QFrame(self)
        self.status_box_id.setObjectName("status_box_id")
        self.status_box_id.setFixedSize(149, 28)
        self.status_box_id.setStyleSheet(
            "#status_box_id { border: 1px solid #b8b8b8; border-radius: 4px;"
            " background: #fcfcfc; }")
        box_lay = QVBoxLayout(self.status_box_id)
        box_lay.setContentsMargins(4, 4, 4, 4)   # 框比标签大 4px（重构前的 pad）
        box_lay.addWidget(self.label_12)

        return self._row((self.label_3, 7), (self.idnumer_pane, 22),
                         (self.status_box_id, 0))

    def _row_address(self):
        """⑧ 住址（多行输入）。这一行有 49px 高，标签要贴顶，
        否则会被行内垂直居中、比输入框低 15px。"""
        row = self._row((self.label_4, 7), (self.textEdit, 0))
        row.setAlignment(self.label_4, Qt.AlignTop)
        return row

    def _row_contact(self):
        """⑨ 电话 / 岗位 / 身份（三对并排）"""
        return self._row((self.label_5, 6), (self.lineEdit_4, 12),
                         (self.label_13, 6), (self.lineEdit_5, 12),
                         (self.identity_label, 6), (self.identity_edit, 0))

    def _row_regulation(self):
        """⑩ 拟用条例（整行占满）"""
        return self._row((self.label_6, 9), (self.comboBox, 0))

    def _row_time(self, which):
        """⑪⑫ 时间字段：一行两列（左列申请/受伤，右列受理/就诊）"""
        if which == 0:
            l1, e1, l2, e2 = (self.lbl_apply, self.apply_time_edit,
                              self.lbl_accept, self.accept_time_edit)
        else:
            l1, e1, l2, e2 = (self.lbl_injury, self.injury_time_edit,
                              self.lbl_visit, self.visit_time_edit)
        return self._row((l1, 7), (e1, 14), (l2, 6), (e2, 0))

    def _row_action_buttons(self):
        """⑬ 身份证导入 / 谈话笔录 / AI审查"""
        return self._row((self.pushButton_4, 35), (self.pushButton, 35),
                         (self.pushButton_ai_review, 0), left=60)

    def _row_unit(self, kind):
        """⑭⑮⑯ 单位信息三行：标签 + 可编辑下拉 + 保存"""
        if kind == 'company':
            lab, combo, btn = self.label_7, self.company_pane, self.pushButton_2
        elif kind == 'employer':
            lab, combo, btn = self.label_8, self.construction_company, self.pushButton_3
        else:
            lab, combo, btn = self.label_9, self.construction_plant, self.pushButton_5
        return self._row((lab, 7), (combo, 9), (btn, 0))

    def _row_doc_buttons(self):
        """⑰ 案件审批表 / 谈话通知书 / 工伤告知书（左对齐，与重构前一致）"""
        return self._row((self.pushButton_11, 19), (self.pushButton_12, 19),
                         (self.pushButton_7, 0), left=60)

    # —— 右栏 ——

    def _build_right_column(self):
        right = QWidget(self)
        right.setObjectName("right_column")
        right.setFixedWidth(self.RIGHT_W)

        col = QVBoxLayout(right)
        col.setContentsMargins(0, self.RIGHT_TOP_PAD, 0, self.RIGHT_BOTTOM_PAD)
        col.setSpacing(7)
        # 用高度比而不是等分：两个框的底边要与左栏最后一行齐平
        col.addWidget(self.statement_group, self.RIGHT_GROUP_SPLIT[0])
        col.addWidget(self.material_group, self.RIGHT_GROUP_SPLIT[1])
        return right

    # —— 浮层（绝对定位，锚窗口尺寸而不是写死坐标）——

    def _build_overlays(self):
        w = self.width() or self.WIN_W
        self.api_group.setGeometry(6, self.TOP_BAR_H + 2, w - 12, 150)
        self.api_group.hide()

        self.todo_board.setGeometry(6, self.TOP_BAR_H + 4, w - 12, 240)
        self.todo_board.hide()
