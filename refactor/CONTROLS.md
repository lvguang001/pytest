# CONTROLS.md — 主界面控件对照表（布局管理器版）

> 本文件是主界面所有控件的「单一事实源」。
> 任何界面改动都必须先读本文件，并保证下面列出的
> **变量名一个都不改、一个都不漏**——业务代码有 80+ 处直接读 `self.xxx`。
>
> **2026-09-15 更新**：主界面已从「绝对坐标 + 十几个 `_move_xxx` 补丁」
> 改成布局管理器，坐标列随之作废，改为「所在行 / 布局容器 / 固定尺寸」。

---

## 〇、总览

主界面控件来自两处：

1. **`ui_main_window.py`**（Qt Designer 生成，仍是 `setGeometry` 绝对坐标）
   —— 初始底图；这些坐标**已全部被布局覆盖**，只是控件变量名的来源。
2. **`ui_main_build.py`** 的 `MainWindowUI` 用代码创建
   —— 原先散在 MainWindow 各 `_setup_xxx_ui` 里的控件（单位性质、身份、证人、
   时间字段、顶栏、右栏、状态框、待办看板等），现在统一在这里建。

**构建与业务的分工**：

```
MainWindowUI (ui_main_build.py)   建控件 + 用 QVBoxLayout/QHBoxLayout 摆位置，不连信号
        ↑ 继承
MainWindow   (app_main.py)        业务逻辑；信号统一在 _connect_signals() 里接
```

- **窗口尺寸固定 870 × 850**（最小/最大都锁死，`MainWindowUI._apply_window_size`）。
- **布局参数**（`MainWindowUI` 的类常量）：左栏占位宽 478、右栏宽 382、
  行距 18、左栏顶部留白 24、右栏顶部留白 3、右栏底部留白 56、
  右栏两个分组框高度比 435 : 321。
- **所有控件的宽高都显式钉死在 `MainWindowUI._SIZES` 里**。
  不加布局前是 `.ui` 的绝对坐标给的尺寸；改布局后若不钉住，Qt 会按 `sizeHint`
  收缩/撑开，各行高度全变。
- 两个浮层（`api_group`、`todo_board`）**仍是绝对定位**，但位置用
  `self.width()` 算，不写死坐标。

---

## 一、左栏控件（x < 478）

左栏结构：`left_column`（QWidget，固定宽 478）→ `QVBoxLayout`，
`contentsMargins(10, 24, 10, 0)`、`setSpacing(18)`，末尾 `addStretch(1)`。
下面每一「行」是一个 `QHBoxLayout`，行内间距按重构前的实测横向位置给。

> 尺寸一律是 `宽 × 高`；「间距」指本控件与下一个控件之间。

### ① 提示区

| 变量名 | 类型 | 含义 | 尺寸 | 来源 |
|---|---|---|---|---|
| `label_15` | QLabel | 「信息提示：」 | 60 × 16 | .ui |
| `status_box` | QFrame | `label_14` 的边框容器 | 353 × 63 | ui_main_build |
| `label_14` | QLabel | 主状态提示（动态，`_set_status` 写这里） | 341 × 51 | .ui |

- 「信息提示：」装在一个匿名 `QWidget` 里，顶部留 6px，与框内首行对齐。
- `status_box` 用 `QVBoxLayout`（内边距 6）装着 `label_14`。
  **边框样式在 QFrame 上、颜色样式在 QLabel 上**——`_set_status()` 每次给标签
  `setStyleSheet("QLabel{color:...}")` 是整体替换，边框若放在标签上会被冲掉。

### ② 案件类型（行首缩进 70）

| 变量名 | 类型 | 含义 | 尺寸 | 行内间距 | 来源 |
|---|---|---|---|---|---|
| `deathCaseCheckbox` | QCheckBox | 工亡案件 | 91 × 31 | 19 | .ui |
| `personalApplicationCheckbox` | QCheckBox | 个人案件 | 111 × 31 | — | .ui |

### ③ 案本号

| 变量名 | 类型 | 含义 | 尺寸 | 行内间距 | 来源 |
|---|---|---|---|---|---|
| `label_10` | QLabel | 「案本号：」（文字由代码改过） | 60 × 16 | 10 | .ui |
| `lineEdit_2` | QLineEdit | 案本号输入（代码把宽从 81 改成 180） | 180 × 20 | 40 | .ui |
| `pushButton_6` | QPushButton | 搜索 → `smart_search_cases` | 131 × 23 | — | .ui |

### ④ 单位性质 / 证人编号（同一行，均常显）

| 变量名 | 类型 | 含义 | 尺寸 | 行内间距 | 来源 |
|---|---|---|---|---|---|
| `unit_type_label` | QLabel | 「单位性质：」 | 64 × 16 | 6 | ui_main_build |
| `unit_type_combo` | QComboBox | 案件级单位性质（可编辑）→ `_refresh_evidence_list` | 100 × 24 | 8 | ui_main_build |
| `witness_label` | QLabel | 「证人编号：」 | 60 × 20 | 6 | ui_main_build |
| `witness_combo` | QComboBox | 证人下拉 → `_on_witness_selected` | 110 × 24 | 6 | ui_main_build |
| `add_witness_btn` | QPushButton | 添加证人 → `_add_witness` | 68 × 24 | — | ui_main_build |

> `unit_type_combo` 的选项（企业/机关/事业单位）和当前值由
> `MainWindow.__init__` 填——`ui_main_build.py` 不引用 `app_main.py` 的常量
> （那边 import 它会成环）。

### ⑤ 角色单选（行首缩进 62）

| 变量名 | 类型 | 含义 | 尺寸 | 行内间距 |
|---|---|---|---|---|
| `radioButton` | QRadioButton | 本人（默认选中） | 47 × 16 | 28 |
| `radioButton_2` | QRadioButton | 证人 | 47 × 16 | 27 |
| `radioButton_3` | QRadioButton | 法人 | 47 × 16 | 28 |
| `radioButton_4` | QRadioButton | 家属 | 60 × 16 | — |

四个都 → `clear_role_fields`。

### ⑥ 姓名 / 年龄 / 性别

| 变量名 | 类型 | 含义 | 尺寸 | 行内间距 |
|---|---|---|---|---|
| `label_2` | QLabel | 「姓    名：」 | 60 × 16 | 6 |
| `name_pane` | QLineEdit | 姓名 → `_on_name_pane_changed` | 66 × 20 | 28 |
| `label` | QLabel | 「年龄：」 | 36 × 16 | 14 |
| `age_pane` | QLineEdit | 年龄（只读，身份证自动填） | 65 × 20 | 35 |
| `label_11` | QLabel | 「性别：」 | 36 × 16 | 14 |
| `lineEdit` | QLineEdit | 性别（只读，身份证自动填） | 66 × 20 | — |

### ⑦ 身份证号

| 变量名 | 类型 | 含义 | 尺寸 | 行内间距 | 来源 |
|---|---|---|---|---|---|
| `label_3` | QLabel | 「身份证号：」 | 60 × 16 | 7 | .ui |
| `idnumer_pane` | QLineEdit | 身份证号 → `on_id_input_finished` | 191 × 20 | 22 | .ui |
| `status_box_id` | QFrame | `label_12` 的边框容器 | 149 × 28 | — | ui_main_build |
| `label_12` | QLabel | 校验提示位（常态为空） | 141 × 20 | — | .ui |

- `status_box_id` 内边距 4，同 ① 所述边框/颜色分离。

### ⑧ 住址

| 变量名 | 类型 | 含义 | 尺寸 | 行内间距 |
|---|---|---|---|---|
| `label_4` | QLabel | 「住    址：」（**行内顶对齐**） | 60 × 16 | 7 |
| `textEdit` | QTextEdit | 住址（多行控件，实际单行用） | 361 × 49 | — |

> 这一行高 49px，标签必须顶对齐，否则被行内垂直居中、比输入框低 15px。

### ⑨ 电话 / 岗位 / 身份

| 变量名 | 类型 | 含义 | 尺寸 | 行内间距 | 来源 |
|---|---|---|---|---|---|
| `label_5` | QLabel | 「电    话：」 | 60 × 16 | 6 | .ui |
| `lineEdit_4` | QLineEdit | 电话 | 96 × 20 | 12 | .ui |
| `label_13` | QLabel | 「岗    位：」 | 60 × 16 | 6 | .ui |
| `lineEdit_5` | QLineEdit | 岗位／**法人角色下是职务** | 70 × 20 | 12 | .ui |
| `identity_label` | QLabel | 「身份：」／**家属角色下是「关系：」** | 44 × 16 | 6 | ui_main_build |
| `identity_edit` | QLineEdit | 身份／与死者关系 | 57 × 20 | — | ui_main_build |

> `identity_label` 的文字随角色变（`on_role_changed`），**标签槽宽固定 44px**，
> 不按文本长度调宽，否则切到长标签的角色会把左边的岗位输入框压住。

### ⑩ 拟用条例

| 变量名 | 类型 | 含义 | 尺寸 | 行内间距 |
|---|---|---|---|---|
| `label_6` | QLabel | 「拟用条例：」 | 60 × 16 | 9 |
| `comboBox` | QComboBox | 拟用条例 → `_refresh_evidence_list` | 361 × 20 | — |

### ⑪⑫ 时间字段（两行，每行两列）

| 变量名 | 类型 | 含义 | 尺寸 | 行内间距 |
|---|---|---|---|---|
| `lbl_apply` / `apply_time_edit` | QLabel / QLineEdit | 申请时间 | 60×20 / 142×22 | 7 / 14 |
| `lbl_accept` / `accept_time_edit` | QLabel / QLineEdit | 受理时间 | 60×20 / 142×22 | 6 / — |
| `lbl_injury` / `injury_time_edit` | QLabel / QLineEdit | 受伤时间 | 60×20 / 142×22 | 7 / 14 |
| `lbl_visit` / `visit_time_edit` | QLabel / QLineEdit | 就诊时间 | 60×20 / 142×22 | 6 / — |

四个输入框都在 `editingFinished` → `_save_date_inputs`。

### ⑬ 操作按钮（行首缩进 60）

| 变量名 | 类型 | 含义 | 尺寸 | 行内间距 |
|---|---|---|---|---|
| `pushButton_4` | QPushButton | 身份证导入 → `id_clicked` | 75 × 23 | 35 |
| `pushButton` | QPushButton | 谈话笔录 → `on_talk_button_clicked` | 75 × 23 | 35 |
| `pushButton_ai_review` | QPushButton | AI审查 → `ai_review_document` | 75 × 23 | — |

> **本次重构的有意变化**：「谈话笔录」原来是 141 宽（`.ui` 初值，一直没改），
> 比同行两个宽一倍，把「AI审查」挤到 x=356。现已统一成 75，「AI审查」回到 x=290。

### ⑭⑮⑯ 单位信息三行

| 行 | 标签 | 下拉框 | 保存按钮 | 尺寸 |
|---|---|---|---|---|
| 用人单位 | `label_7` | `company_pane` → `company` | `pushButton_2` → `save_company` | 54×12 / 301×22 / 75×23 |
| 用工单位 | `label_8` | `construction_company` → `sync_employer_to_dict` | `pushButton_3` → `save_construction_company` | 同上 |
| 工地名称 | `label_9` | `construction_plant` → `c_plant` | `pushButton_5` → `save_construction_plant` | 同上 |

行内间距：标签 7、下拉 9。三个下拉框都是可编辑的 `QComboBox`。

> **`company_pane` 是坑点**：四个角色共用，切换角色时靠
> `_restore_role_unit()` 回填「该角色自己的单位」；本人角色下才是案件级用人单位。
> 不要在任何地方无条件把它的值写成案件级用人单位，否则会污染全案文书。

### ⑰ 底部按钮（行首缩进 60，左对齐，与重构前一致）

| 变量名 | 类型 | 含义 | 尺寸 | 行内间距 |
|---|---|---|---|---|
| `pushButton_11` | QPushButton | 案件审批表 → `approve` | 75 × 23 | 19 |
| `pushButton_12` | QPushButton | 谈话通知书 → `on_pushButton_12_clicked` | 81 × 23 | 19 |
| `pushButton_7` | QPushButton | 工伤告知书 → `generate_injury_notice` | 75 × 23 | — |

---

## 二、右栏控件

右栏结构：`right_column`（QWidget，固定宽 382）→ `QVBoxLayout`，
`contentsMargins(0, 3, 0, 56)`、`setSpacing(7)`，
两个分组框按 **435 : 321** 的比例分配高度，底边正好与左栏最后一行齐平。

| 变量名 | 类型 | 含义 | 尺寸 | 来源 |
|---|---|---|---|---|
| `statement_group` | QGroupBox | 「案件申请陈述」（内部 `QVBoxLayout`，内边距 7/0/7/5、行距 4） | 382 × 435 | ui_main_build |
| ├ `statement_edit` | QTextEdit | 陈述输入（**已从数据链路断开**） | 拉伸 | ui_main_build |
| ├ `stmt_copy_btn` | QPushButton | 复制 → `_copy_statement` | 45 × 23 | ui_main_build |
| └ `stmt_clear_btn` | QPushButton | 清空 → `statement_edit.clear` | 45 × 23 | ui_main_build |
| `material_group` | QGroupBox | 「目前提供的材料分类」（同上内边距） | 382 × 321 | ui_main_build |
| ├ `material_list` | MaterialListWidget | 材料清单（**已移到 `material_list.py`**） | 拉伸 | ui_main_build |
| ├ `mat_copy_btn` | QPushButton | 复制 → `_copy_material` | 45 × 23 | ui_main_build |
| ├ `mat_clear_btn` | QPushButton | 清空 → `material_list.clear` | 45 × 23 | ui_main_build |
| └ `mat_add_btn` | QPushButton | 新增 → `material_list.add_row` | 45 × 23 | ui_main_build |

> **注意 QGroupBox 的内边距**：分组框的内容区本来就落在标题下方约 18px 处，
> layout 的上边距要写 **0**，写 18 会白多一段。
>
> 「案件申请陈述」这一栏已从数据链路上断开——框里填什么都不再进入案件数据，
> 也不再回填。界面暂时保留待重新设计。
>
> 五个按钮原先都是匿名 + `lambda`，本次重构补上了变量名（原来的 lambda 写法
> 无法在别处引用，也没法统一接信号）。

`MaterialListWidget` 每行是 `[☑ 复选框] [材料名称] [备注]`，
`materials_changed` 是它的自定义信号；自动生成的行（`generated=True`）
在换条例时会被重建，案件自带和手工添加的不会。见 `material_list.py`。

---

## 三、顶栏与浮层

| 变量名 | 类型 | 含义 | 尺寸 | 位置 |
|---|---|---|---|---|
| `top_bar` | QLabel | 顶栏背景条 | 870 × 28 | 顶部 |
| `config_toggle_btn` | QPushButton | ⚙ 配置开关 → `_toggle_config_panel` | 28 × 24 | 顶栏内 `QHBoxLayout`，左边距 4 |
| `top_status_label` | QLabel | AI 状态文字（`_update_api_status` 写这里） | 430 × 20 | 紧跟 ⚙ |
| `todo_btn` | QPushButton | 待办事项(N) → `_toggle_todo_panel` | 128 × 24 | 顶栏最右（弹簧推到右边） |
| `api_group` | QFrame | 用户配置浮层（默认隐藏） | 858 × 150 | 绝对定位 (6, 30) |
| `api_user_combo` | QComboBox | 用户下拉 → `_on_user_combo_changed` | 拉伸 | 浮层内 |
| `api_key_input` | PasswordLineEdit | API 密钥 → `_on_api_edited` | 拉伸 | 浮层内 |
| `todo_board` | TodoBoard | 待办看板（默认隐藏） | 858 × 240 | 绝对定位 (6, 32) |

> **`api_group` 的 objectName 是 `apiPanel`**（不是 `api_group`），
> 样式表按 `QFrame#apiPanel` 写。
> **`todo_board` 的 objectName 是 `TodoBoard`**，由 `TodoBoard.__init__` 自己设，
> 样式表按 `QFrame#TodoBoard` 写——**不要改它**，改了会丢边框和底色。
> `label_14` 开了 `setWordWrap(True)`，长消息换行而不是被裁掉。

---

## 四、信号连接完整清单

### 4.1 来自 `ui_main_window.py`（生成代码，本次未动）

| 控件 | 信号 | 槽 |
|---|---|---|
| `idnumer_pane` | `editingFinished` | `on_id_input_finished` |
| `pushButton_4` | `clicked` | `id_clicked` |
| `pushButton_2` | `clicked` | `save_company` |
| `pushButton_3` | `clicked` | `save_construction_company` |
| `pushButton_5` | `clicked` | `save_construction_plant` |
| `radioButton` ~ `radioButton_4` | `clicked` | `clear_role_fields` |
| `pushButton_12` | `clicked` | `on_pushButton_12_clicked` |

### 4.2 来自 `MainWindow._connect_signals()`（本次重构收敛到这里）

| 控件 | 信号 | 槽 |
|---|---|---|
| `name_pane` | `editingFinished` | `_on_name_pane_changed` |
| `company_pane` | `currentTextChanged` | `company` |
| `construction_company` | `currentTextChanged` | `sync_employer_to_dict` |
| `construction_plant` | `currentTextChanged` | `c_plant` |
| `deathCaseCheckbox` | `stateChanged` | `on_case_type_changed`、`_refresh_evidence_list` |
| `personalApplicationCheckbox` | `stateChanged` | `on_case_type_changed`、`_refresh_evidence_list` |
| `comboBox` | `currentIndexChanged` | `_refresh_evidence_list` |
| `unit_type_combo` | `currentTextChanged` | `_refresh_evidence_list` |
| `apply/accept/injury/visit_time_edit` | `editingFinished` | `_save_date_inputs` |
| `witness_combo` | `currentIndexChanged` | `_on_witness_selected` |
| `add_witness_btn` | `clicked` | `_add_witness` |
| `config_toggle_btn` | `clicked` | `_toggle_config_panel` |
| `todo_btn` | `clicked` | `_toggle_todo_panel` |
| `todo_board` | `taskClicked` | `_on_board_task_click` |
| `api_user_combo` | `currentTextChanged` | `_on_user_combo_changed` |
| `api_user_combo.lineEdit()` | `editingFinished` | `_on_api_edited` |
| `api_key_input` | `editingFinished` | `_on_api_edited` |
| `pushButton_6` | `clicked` | `smart_search_cases` |
| `pushButton_7` | `clicked` | `generate_injury_notice` |
| `pushButton_11` | `clicked` | `approve` |
| `pushButton_ai_review` | `clicked` | `ai_review_document` |
| `pushButton_12` | `clicked` | 先 `disconnect()` 再连 `on_pushButton_12_clicked` |
| `pushButton` | `clicked` | 先 `disconnect()` 再连 `on_talk_button_clicked` |
| `stmt_copy_btn` / `stmt_clear_btn` | `clicked` | `_copy_statement` / `statement_edit.clear` |
| `mat_copy_btn` / `mat_clear_btn` / `mat_add_btn` | `clicked` | `_copy_material` / `material_list.clear` / `material_list.add_row` |
| `_todo_timer` | `timeout` | `_refresh_todo_board`（在 `_install_todo_kanban` 里） |

> ⚠️ **`pushButton_12` 和 `pushButton` 必须先 `disconnect()` 再连**。
> 这两个按钮在 `ui_main_window.py` 的 `setupUi()` 里已经接过了，直接再 `connect`
> 会让槽挂两遍。原先「谈话通知书」就中招了——实测**点一次跑 4 次**：
> `.ui` 显式 connect 1 条 + `QMetaObject.connectSlotsByName` 按命名约定又接了 2 条
> （`clicked()` / `clicked(bool)` 两个重载）+ `__init__` 里 1 条。现在统一
> 「先全部断开、再接一条」，跑 1 次。
>
> **`connectSlotsByName` 的坑**：它会把**任何** `on_<子控件objectName>_<信号>`
> 形状的方法自动接上。当前 `MainWindow` 里只有 `on_pushButton_12_clicked` 命中
> （`on_talk_button_clicked`、`on_role_changed`、`on_case_type_changed`、
> `on_id_input_finished` 都不对应任何子控件名，安全）。
> **以后新增方法别起成这个形状**，否则会被悄悄自动连接。

### 4.3 `MaterialListWidget` 内部（`material_list.py`，不动）

每行的复选框 `toggled`、名称 `textChanged`、备注 `textChanged` 都接到
`self._on_changed` → 发 `materials_changed`。

---

## 五、布局结构图

```
MainWindowUI (QWidget + Ui_Form)
└─ QVBoxLayout root (margins 0, spacing 0)
   ├─ top_bar (QLabel, 固定高 28)  背景条 + QHBoxLayout(4,2,6,2)
   │    [⚙ 28×24] [top_status_label 430×20] ──弹簧── [todo_btn 128×24]
   └─ QHBoxLayout body
      ├─ left_column (QWidget, 固定宽 478)      ← QVBoxLayout(10,24,10,0) spacing 18
      │    ① 提示区         [信息提示：][status_box: label_14]
      │    ② 案件类型       [工亡案件][个人案件]              （缩进 70）
      │    ③ 案本号         [案本号：][lineEdit_2][搜索]
      │    ④ 单位性质/证人  [单位性质：][下拉][证人编号：][下拉][添加证人]
      │    ⑤ 角色单选       [本人][证人][法人][家属]          （缩进 62）
      │    ⑥ 姓名/年龄/性别 [姓名：][名][年龄：][龄][性别：][别]
      │    ⑦ 身份证号       [身份证号：][idnumer_pane][status_box_id]
      │    ⑧ 住址           [住址：][textEdit 361×49]
      │    ⑨ 电话/岗位/身份 [电话：][话][岗位：][位][身份：][份]
      │    ⑩ 拟用条例       [拟用条例：][comboBox 整行]
      │    ⑪ 申请/受理时间  [申请时间：][框][受理时间：][框]
      │    ⑫ 受伤/就诊时间  [受伤时间：][框][就诊时间：][框]
      │    ⑬ 操作按钮       [身份证导入][谈话笔录][AI审查]     （缩进 60）
      │    ⑭⑮⑯ 单位信息    [用人单位：][下拉][保存] ×3
      │    ⑰ 底部按钮       [案件审批表][谈话通知书][工伤告知书]（缩进 60）
      │    addStretch(1)
      └─ right_column (QWidget, 固定宽 382)     ← QVBoxLayout(0,3,0,56) spacing 7
           statement_group  (拉伸比 435)
             statement_edit（拉伸）
             [复制][清空]
           material_group   (拉伸比 321)
             material_list（拉伸）
             [复制][清空][新增]
      └─ addStretch(1)  吃掉剩下的 10px（右栏左缘因此落在 x=478）

浮层（绝对定位，锚 self.width()，默认隐藏、show 时 raise_()）：
  api_group  ：(6, 30, 宽-12, 150)
  todo_board ：(6, 32, 宽-12, 240)
```

---

## 六、改界面时的硬性约束

```
1. 不要改任何控件变量名（见第一、二、三节的完整清单）。
2. 不要改任何业务逻辑方法的签名和内部逻辑（approve、on_talk_button_clicked、
   _save_cases_data 等一律不动）。
3. 不要删除文件，只新建或原地修改。
4. 界面改动只改 ui_main_build.py / ui_main_window.py；
   业务文件 app_main.py 里不要出现 setGeometry / move / resize。
5. 新加控件：在 ui_main_build._create_extra_widgets 里建、在对应 _row_xxx()
   里摆、把尺寸加进 _SIZES；信号在 MainWindow._connect_signals() 里接。
6. 每改一处，告诉对方"改了哪个文件、哪几行、为什么"。
7. 改完必须跑一下 ui_main_build 的构造（`python -c "import app_main"` 不够，
   要真的建一次 MainWindow），或跑程序看界面。
8. ui_main_build.py 不要 import app_main.py——app_main 反过来 import 它，
   会成环；`python app_main.py` 直接启动时模块名是 __main__，延迟 import
   "app_main" 还会加载出第二份模块、造出两个不同的类。
   依赖 app_main 常量的文案/选项，由 MainWindow 在 __init__ 里补。
9. 浮层控件（api_group、todo_board）保留绝对定位，但位置要用
   self.width() / self.height() 算，不要写死坐标。
10. 证人编号行（witness_label / witness_combo / add_witness_btn）是常显的，
    不要加回"随角色隐藏"的逻辑。
```

---

## 七、重构完成状态

- [x] 所有 `_move_xxx / _compact_xxx / _align_xxx / _grow_xxx / _relocate_xxx /
      _uniform_row_spacing / _frame_for_label / _fit_group_content` 补丁已删除
- [x] `_setup_api_config_ui / _setup_unit_identity_ui / _setup_witness_ui /
      _setup_status_boxes / _setup_radio_connections / _reconnect_approval_button`
      已删除（建控件搬到 ui_main_build，连信号搬到 _connect_signals）
- [x] 信号收敛到 `MainWindow._connect_signals()`（`.ui` 生成的那批未动）
- [x] 五个匿名按钮已命名
- [x] 修掉「谈话通知书」按钮的重复连接（点一次跑 4 次 → 1 次）
- [x] `.ui` 之外的控件补齐 objectName
- [x] 逐控件比对改造前后的实际坐标：除下列有意变化外，偏差均 ≤ 3px

**验收时要留意的、与重构前的差异**：

| 位置 | 差异 | 原因 |
|---|---|---|
| 谈话笔录 / AI审查 按钮 | 谈话笔录 141→75 宽；AI审查 356→290 | 有意统一（本文件 §1.13） |
| 用人单位/用工单位/工地名称 三个标签 | 上移 6px | 由「手工贴齐下拉框文字」改为行内垂直居中 |
| 身份证号右侧提示框 | 右移 4px | 由「标签坐标减 4」改为间距常量 |
| 住址标签 | 位置不变（384） | 显式顶对齐——不这样会低 15px |
| 各行控件 | ≤3px | 布局接管后由整数像素舍入产生 |
| **（行为）谈话通知书按钮** | 点一次生成函数由 **4 次 → 1 次** | 修掉重复连接，见 §4.2 |

---

*本文件最后更新：2026-09-15（随布局管理器重构同步重写）*
