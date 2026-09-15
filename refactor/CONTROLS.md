# CONTROLS.md — 主界面控件对照表（完整版）

> 本文件是主界面所有控件的「单一事实源」。
> 任何界面重构（改用布局管理器、调整位置、增减控件）都必须先读本文件，
> 并保证下面列出的**变量名一个都不改、一个都不漏**。
> 业务代码（MainWindow 里的 2000+ 行）全靠这些变量名访问控件。

---

## 〇、总览

主界面控件来自两处：

1. **`ui_main_window.py`**（Qt Designer 生成，绝对坐标 `setGeometry`）
   —— 初始底图 470×650，控件名多为 `label_数字` / `pushButton_数字`。
2. **`MainWindow` 各 `_setup_xxx_ui` 用代码创建**
   —— 后期补的控件（单位性质、身份、证人、时间字段、顶栏、右侧面板、
   状态框、待办看板等），不在 `.ui` 文件里。

真实界面 = **第 1 处 + 第 2 处 + 十几个 `_move_xxx` / `_compact_xxx` 补丁**。

**最终窗口尺寸**：870 × 850（`_setup_api_config_ui` 里设定，最小/最大都锁死）。

**顶栏高度**：28px。`_setup_api_config_ui` 把所有 `.ui` 控件整体下移 28px。

**左栏宽度**：约 470px（`_setup_api_config_ui` 里右栏从 x=478 开始）。

---

## 一、来自 `ui_main_window.py` 的控件（必须保留原名）

### 1.1 顶部区域

| 变量名 | 类型 | 实际含义 | 初始坐标 (x,y,w,h) | 最终坐标 | 归属行列 | 备注 |
|---|---|---|---|---|---|---|
| `label_15` | QLabel | “信息提示：”静态标签 | (10, 50, 60, 16) | (10, 78, 60, 16) | 顶栏下第 1 行左 | 后被 `_setup_status_boxes` 对齐到 label_14 |
| `label_14` | QLabel | 主状态提示区（动态） | (80, 30, 341, 51) | (80, 58, 341, 51) | 顶栏下第 1 行右 | `_set_status` 写这里；有边框 `status_box` |
| `deathCaseCheckbox` | QCheckBox | “工亡案件”复选框 | (80, 90, 91, 31) | (80, 118, 91, 31) | 第 2 行 | 案件性质 |
| `personalApplicationCheckbox` | QCheckBox | “个人案件”复选框 | (190, 90, 111, 31) | (190, 118, 111, 31) | 第 2 行 | 申请类型 |
| `label_10` | QLabel | “案本号：”标签 | (10, 130, 60, 16) | (10, 158, 60, 16) | 第 3 行左 | MainWindow 把文字从“受伤职工：”改为“案本号：” |
| `lineEdit_2` | QLineEdit | 案本号输入框 | (80, 130, 81, 20) | (80, 158, 180, 20) | 第 3 行中 | MainWindow 里 resize 到 180 宽 |
| `pushButton_6` | QPushButton | “搜索”按钮 | (300, 130, 131, 23) | (300, 158, 131, 23) | 第 3 行右 | → `smart_search_cases` |

### 1.2 角色单选行

| 变量名 | 类型 | 实际含义 | 初始坐标 | 最终坐标 | 归属行列 |
|---|---|---|---|---|---|
| `radioButton` | QRadioButton | “本人” | (72, 174, 47, 16) | (72, 202, 47, 16) | 第 4 行左 |
| `radioButton_2` | QRadioButton | “证人” | (147, 174, 47, 16) | (147, 202, 47, 16) | 第 4 行 |
| `radioButton_3` | QRadioButton | “法人” | (221, 174, 47, 16) | (221, 202, 47, 16) | 第 4 行 |
| `radioButton_4` | QRadioButton | “家属” | (72, 193, 60, 16) | (296, 202, 60, 16) | 第 4 行右 | 被 `_align_role_radios` 抬到同一行 |
| `pushButton` | QPushButton | “谈话笔录”按钮 | (296, 171, 141, 23) | (180, 458, 75, 23) | 操作按钮行 | 被 `_relocate_talk_button` 移到 pushButton_4 之后 |

### 1.3 个人信息表单

| 变量名 | 类型 | 实际含义 | 初始坐标 | 最终坐标 | 归属行列 | 备注 |
|---|---|---|---|---|---|---|
| `label_2` | QLabel | “姓    名：” | (11, 211, 60, 16) | (11, 239, 60, 16) | 表单第 1 行 | |
| `name_pane` | QLineEdit | 姓名输入框 | (77, 211, 66, 20) | (77, 239, 66, 20) | 表单第 1 行 | → `_on_name_pane_changed` |
| `label` | QLabel | “年龄：” | (170, 210, 36, 16) | (170, 238, 36, 16) | 表单第 1 行 | |
| `age_pane` | QLineEdit | 年龄输入框（只读） | (220, 210, 65, 20) | (220, 238, 65, 20) | 表单第 1 行 | `setEnabled(False)` |
| `label_11` | QLabel | “性别：” | (320, 210, 36, 16) | (320, 238, 36, 16) | 表单第 1 行 | |
| `lineEdit` | QLineEdit | 性别输入框（只读） | (370, 210, 66, 20) | (370, 238, 66, 20) | 表单第 1 行 | `setEnabled(False)` |
| `label_3` | QLabel | “身份证号：” | (11, 251, 60, 16) | (11, 279, 60, 16) | 表单第 2 行 | |
| `idnumer_pane` | QLineEdit | 身份证号输入框 | (77, 251, 191, 20) | (77, 279, 191, 20) | 表单第 2 行 | → `on_id_input_finished` |
| `label_12` | QLabel | 身份证校验提示位 | (290, 252, 141, 20) | (290, 280, 141, 20) | 表单第 2 行右 | 常态为空；有边框 `status_box_id` |
| `label_4` | QLabel | “住    址：” | (11, 281, 60, 16) | (11, 309, 60, 16) | 表单第 3 行 | |
| `textEdit` | QTextEdit | 住址输入框（多行） | (77, 281, 361, 49) | (77, 309, 361, 49) | 表单第 3 行 | 实际是单行地址，但用了 QTextEdit |

### 1.4 电话 / 岗位 / 身份 / 条例

| 变量名 | 类型 | 实际含义 | 初始坐标 | 最终坐标 | 归属行列 | 备注 |
|---|---|---|---|---|---|---|
| `label_5` | QLabel | “电    话：” | (12, 351, 60, 16) | (12, 379, 60, 16) | 表单第 4 行 | |
| `lineEdit_4` | QLineEdit | 电话输入框 | (78, 351, 161, 20) | (78, 379, 96, 20) | 表单第 4 行 | 被 `_compact_identity_row` 缩窄到 96 |
| `label_13` | QLabel | “岗    位：” | (250, 350, 60, 16) | (186, 378, 60, 16) | 表单第 4 行 | 被 `_compact_identity_row` 左移 |
| `lineEdit_5` | QLineEdit | 岗位输入框 | (330, 350, 111, 20) | (252, 379, 70, 20) | 表单第 4 行 | 被 `_compact_identity_row` 缩窄 |
| `identity_label` | QLabel | “身份：/关系：” | — | (334, 378, 44, 16) | 表单第 4 行 | MainWindow 创建 |
| `identity_edit` | QLineEdit | 身份/关系输入框 | — | (384, 379, 57, 20) | 表单第 4 行 | MainWindow 创建 |
| `label_6` | QLabel | “拟用条例：” | (11, 391, 60, 16) | (11, 419, 60, 16) | 表单第 5 行 | |
| `comboBox` | QComboBox | 拟用条例下拉框 | (79, 391, 361, 20) | (79, 419, 361, 20) | 表单第 5 行 | → `_refresh_evidence_list` |

### 1.5 时间字段（后被 `_move_time_fields_up` 移到拟用条例下方）

| 变量名 | 类型 | 实际含义 | 初始坐标 | 最终坐标 | 归属行列 | 备注 |
|---|---|---|---|---|---|---|
| `lbl_apply` | QLabel | “申请时间：” | — | (11, 447, 60, 20) | 时间行 1 左 | MainWindow 创建 |
| `apply_time_edit` | QLineEdit | 申请时间输入 | — | (77, 445, 142, 22) | 时间行 1 左 | MainWindow 创建 |
| `lbl_accept` | QLabel | “受理时间：” | — | (233, 447, 60, 20) | 时间行 1 右 | MainWindow 创建 |
| `accept_time_edit` | QLineEdit | 受理时间输入 | — | (299, 445, 142, 22) | 时间行 1 右 | MainWindow 创建 |
| `lbl_injury` | QLabel | “受伤时间：” | — | (11, 473, 60, 20) | 时间行 2 左 | MainWindow 创建 |
| `injury_time_edit` | QLineEdit | 受伤时间输入 | — | (77, 471, 142, 22) | 时间行 2 左 | MainWindow 创建 |
| `lbl_visit` | QLabel | “就诊时间：” | — | (233, 473, 60, 20) | 时间行 2 右 | MainWindow 创建 |
| `visit_time_edit` | QLineEdit | 就诊时间输入 | — | (299, 471, 142, 22) | 时间行 2 右 | MainWindow 创建 |

### 1.6 单位性质 / 证人编号行（后由 `_move_unit_type_row` + `_move_witness_row_up` 合并到一行）

| 变量名 | 类型 | 实际含义 | 初始坐标 | 最终坐标 | 归属行列 | 备注 |
|---|---|---|---|---|---|---|
| `unit_type_label` | QLabel | “单位性质：” | — | (10, 186, 64, 16) | 单位性质行 | MainWindow 创建 |
| `unit_type_combo` | QComboBox | 单位性质下拉框 | — | (80, 182, 100, 24) | 单位性质行 | MainWindow 创建；→ `_refresh_evidence_list` |
| `witness_label` | QLabel | “证人编号：” | (70, 765, 70, 20) | (188, 188, 60, 20) | 单位性质行 | MainWindow 创建 |
| `witness_combo` | QComboBox | 证人下拉框 | (140, 763, 180, 24) | (254, 182, 110, 24) | 单位性质行 | MainWindow 创建 |
| `add_witness_btn` | QPushButton | “添加证人”按钮 | (330, 763, 80, 24) | (370, 182, 68, 24) | 单位性质行 | MainWindow 创建 |

### 1.7 操作按钮行

| 变量名 | 类型 | 实际含义 | 初始坐标 | 最终坐标 | 归属行列 | 备注 |
|---|---|---|---|---|---|---|
| `pushButton_4` | QPushButton | “身份证导入”按钮 | (70, 430, 75, 23) | (70, 458, 75, 23) | 操作按钮行 | → `id_clicked` |
| `pushButton` | QPushButton | “谈话笔录”按钮 | (296, 171, 141, 23) | (180, 458, 75, 23) | 操作按钮行 | → `on_talk_button_clicked` |
| `pushButton_ai_review` | QPushButton | “AI审查”按钮 | (180, 430, 75, 23) | (290, 458, 75, 23) | 操作按钮行 | → `ai_review_document` |

### 1.8 单位信息区

| 变量名 | 类型 | 实际含义 | 初始坐标 | 最终坐标 | 归属行列 | 备注 |
|---|---|---|---|---|---|---|
| `label_7` | QLabel | “用人单位：” | (10, 480, 54, 12) | (10, 508, 54, 12) | 单位行 1 | |
| `company_pane` | QComboBox | 用人单位下拉框（可编辑） | (70, 470, 301, 22) | (70, 498, 301, 22) | 单位行 1 | → `company` |
| `pushButton_2` | QPushButton | 用人单位“保存”按钮 | (380, 470, 75, 23) | (380, 498, 75, 23) | 单位行 1 | → `save_company` |
| `label_8` | QLabel | “用工单位：” | (10, 520, 54, 12) | (10, 548, 54, 12) | 单位行 2 | |
| `construction_company` | QComboBox | 用工单位下拉框（可编辑） | (70, 510, 301, 22) | (70, 538, 301, 22) | 单位行 2 | → `sync_employer_to_dict` |
| `pushButton_3` | QPushButton | 用工单位“保存”按钮 | (380, 510, 75, 23) | (380, 538, 75, 23) | 单位行 2 | → `save_construction_company` |
| `label_9` | QLabel | “工地名称：” | (10, 560, 54, 12) | (10, 588, 54, 12) | 单位行 3 | |
| `construction_plant` | QComboBox | 工地名称下拉框（可编辑） | (70, 550, 301, 22) | (70, 578, 301, 22) | 单位行 3 | → `c_plant` |
| `pushButton_5` | QPushButton | 工地名称“保存”按钮 | (380, 550, 75, 23) | (380, 578, 75, 23) | 单位行 3 | → `save_construction_plant` |

### 1.9 底部按钮行

| 变量名 | 类型 | 实际含义 | 初始坐标 | 最终坐标 | 归属行列 | 备注 |
|---|---|---|---|---|---|---|
| `pushButton_11` | QPushButton | “案件审批表”按钮 | (70, 590, 75, 23) | (70, 618, 75, 23) | 底部按钮行 | → `approve` |
| `pushButton_12` | QPushButton | “谈话通知书”按钮 | (250, 590, 81, 23) | (164, 618, 81, 23) | 底部按钮行 | → `on_pushButton_12_clicked`；被 `_compact_doc_buttons` 左移 |
| `pushButton_7` | QPushButton | “工伤告知书”按钮 | (350, 590, 75, 23) | (264, 618, 75, 23) | 底部按钮行 | → `generate_injury_notice`；被 `_compact_doc_buttons` 左移 |

---

## 二、`MainWindow` 代码创建、不在 `.ui` 里的控件

### 2.1 单位性质 / 身份（`_setup_unit_identity_ui`）

| 变量名 | 类型 | 实际含义 | 备注 |
|---|---|---|---|
| `unit_type_label` | QLabel | “单位性质：” | 后被 `_move_unit_type_row` 移到案本号下方 |
| `unit_type_combo` | QComboBox | 单位性质下拉框 | 可编辑；→ `_refresh_evidence_list` |
| `identity_label` | QLabel | “身份：/关系：” | 标签随角色变化 |
| `identity_edit` | QLineEdit | 身份/关系输入框 | 后并入电话/岗位行 |

### 2.2 证人编号（`_setup_witness_ui`）

| 变量名 | 类型 | 实际含义 | 备注 |
|---|---|---|---|
| `witness_label` | QLabel | “证人编号：” | 后被 `_move_witness_row_up` 移到单位性质行 |
| `witness_combo` | QComboBox | 证人下拉框 | 后被 `_move_witness_row_up` 移到单位性质行 |
| `add_witness_btn` | QPushButton | “添加证人”按钮 | 后被 `_move_witness_row_up` 移到单位性质行 |

### 2.3 时间字段（`_setup_api_config_ui`）

| 变量名 | 类型 | 实际含义 | 备注 |
|---|---|---|---|
| `lbl_apply` | QLabel | “申请时间：” | |
| `apply_time_edit` | QLineEdit | 申请时间输入 | → `_save_date_inputs` |
| `lbl_accept` | QLabel | “受理时间：” | |
| `accept_time_edit` | QLineEdit | 受理时间输入 | → `_save_date_inputs` |
| `lbl_injury` | QLabel | “受伤时间：” | |
| `injury_time_edit` | QLineEdit | 受伤时间输入 | → `_save_date_inputs` |
| `lbl_visit` | QLabel | “就诊时间：” | |
| `visit_time_edit` | QLineEdit | 就诊时间输入 | → `_save_date_inputs` |

### 2.4 顶栏（`_setup_api_config_ui`）

| 变量名 | 类型 | 实际含义 | 备注 |
|---|---|---|---|
| `top_bar` | QLabel | 顶栏背景条 | 28px 高 |
| `config_toggle_btn` | QPushButton | “⚙”配置按钮 | → `_toggle_config_panel` |
| `top_status_label` | QLabel | 顶栏状态文字 | `_update_api_status` 写这里 |

### 2.5 用户配置浮层（`_setup_api_config_ui`）

| 变量名 | 类型 | 实际含义 | 备注 |
|---|---|---|---|
| `api_group` | QFrame | 用户配置下拉面板 | 默认隐藏；`_toggle_config_panel` 控制显隐 |
| `api_user_combo` | QComboBox | 用户下拉框 | → `_on_user_combo_changed`；`editingFinished` → `_on_api_edited` |
| `api_key_input` | PasswordLineEdit | API 密钥输入 | `editingFinished` → `_on_api_edited` |

### 2.6 右侧面板（`_setup_api_config_ui`）

| 变量名 | 类型 | 实际含义 | 备注 |
|---|---|---|---|
| `statement_group` | QGroupBox | “案件申请陈述”分组框 | 后被 `_grow_right_panels` 加高 |
| `statement_edit` | QTextEdit | 案件申请陈述输入 | 已从数据链路断开 |
| `material_group` | QGroupBox | “目前提供的材料分类”分组框 | 后被 `_grow_right_panels` 加高 |
| `material_list` | MaterialListWidget | 材料清单控件 | 自定义控件 |

> `statement_group` 内部还有两个匿名按钮：“复制”（→ `_copy_statement`）和“清空”（局部 lambda）。
> `material_group` 内部还有三个匿名按钮：“复制”（→ `_copy_material`）、“清空”、“新增”。
> 重构时这些按钮也要保留，建议给它们命名，例如 `stmt_copy_btn` / `stmt_clear_btn` / `mat_copy_btn` / `mat_clear_btn` / `mat_add_btn`。

### 2.7 状态提示边框（`_setup_status_boxes`）

| 变量名 | 类型 | 实际含义 | 备注 |
|---|---|---|---|
| `status_box` | QFrame | label_14 的边框 | 常显空框 |
| `status_box_id` | QFrame | label_12 的边框 | 常显空框 |

### 2.8 待办看板（`_install_todo_kanban`）

| 变量名 | 类型 | 实际含义 | 备注 |
|---|---|---|---|
| `todo_btn` | QPushButton | “待办事项(N)” | 顶栏右侧；→ `_toggle_todo_panel` |
| `todo_board` | TodoBoard | 待办看板 | 默认隐藏；`taskClicked` → `_on_board_task_click` |
| `_todo_timer` | QTimer | 60 分钟定时刷新 | 非控件，但由本方法创建 |

### 2.9 其他成员变量（非控件，但重构时不要误删）

| 变量名 | 类型 | 实际含义 |
|---|---|---|
| `data_model` | CaseDataModel | 数据模型 |
| `var_manager` | TemplateVariableManager | 模板变量管理器 |
| `file_service` | FileService | 文件服务 |
| `data_service` | DataService | 数据服务 |
| `config_service` | ConfigService | 配置服务 |
| `ai_service` | AIService / None | AI 服务 |
| `user_manager` | UserManager | 用户管理器 |
| `current_case_id` | str | 当前案本号 |
| `current_case_folder` | str / None | 当前案件文件夹 |
| `current_person_name` | str | 当前人名 |
| `_test_data_index` | int | F2 测试数据索引 |
| `_todo_open` | bool | 待办面板展开状态 |
| `_is_handling_ai_result` | bool | AI 结果防重复标志 |
| `_template_dict` | dict | 兼容旧接口的模板字典缓存 |
| `items_list` / `items_list1` / `items_list2` | list | 三个下拉框的数据源 |

---

## 三、信号连接完整清单

### 3.1 来自 `ui_main_window.py` 的连接

| 控件 | 信号 | 槽 |
|---|---|---|
| `idnumer_pane` | `editingFinished` | `on_id_input_finished` |
| `pushButton_4` | `clicked` | `id_clicked` |
| `pushButton_2` | `clicked` | `save_company` |
| `pushButton_3` | `clicked` | `save_construction_company` |
| `pushButton_5` | `clicked` | `save_construction_plant` |
| `radioButton` | `clicked` | `clear_role_fields` |
| `radioButton_2` | `clicked` | `clear_role_fields` |
| `radioButton_3` | `clicked` | `clear_role_fields` |
| `radioButton_4` | `clicked` | `clear_role_fields` |
| `pushButton_12` | `clicked` | `on_pushButton_12_clicked` |

### 3.2 来自 `MainWindow` 的连接

| 控件 | 信号 | 槽 |
|---|---|---|
| `name_pane` | `editingFinished` | `_on_name_pane_changed` |
| `company_pane` | `currentTextChanged` | `company` |
| `construction_company` | `currentTextChanged` | `sync_employer_to_dict` |
| `construction_plant` | `currentTextChanged` | `c_plant` |
| `pushButton_6` | `clicked` | `smart_search_cases` |
| `pushButton_7` | `clicked` | `generate_injury_notice` |
| `pushButton_11` | `clicked` | `approve` |
| `pushButton` | `clicked` | `on_talk_button_clicked` |
| `pushButton_ai_review` | `clicked` | `ai_review_document` |
| `deathCaseCheckbox` | `stateChanged` | `on_case_type_changed` |
| `deathCaseCheckbox` | `stateChanged` | `_refresh_evidence_list` |
| `personalApplicationCheckbox` | `stateChanged` | `on_case_type_changed` |
| `personalApplicationCheckbox` | `stateChanged` | `_refresh_evidence_list` |
| `comboBox` | `currentIndexChanged` | `_refresh_evidence_list` |
| `unit_type_combo` | `currentTextChanged` | `_refresh_evidence_list` |
| `api_user_combo` | `currentTextChanged` | `_on_user_combo_changed` |
| `api_user_combo.lineEdit()` | `editingFinished` | `_on_api_edited` |
| `api_key_input` | `editingFinished` | `_on_api_edited` |
| `config_toggle_btn` | `clicked` | `_toggle_config_panel` |
| `todo_btn` | `clicked` | `_toggle_todo_panel` |
| `todo_board` | `taskClicked` | `_on_board_task_click` |
| `apply_time_edit` | `editingFinished` | `_save_date_inputs` |
| `accept_time_edit` | `editingFinished` | `_save_date_inputs` |
| `injury_time_edit` | `editingFinished` | `_save_date_inputs` |
| `visit_time_edit` | `editingFinished` | `_save_date_inputs` |
| `witness_combo` | `currentIndexChanged` | `_on_witness_selected` |
| `add_witness_btn` | `clicked` | `_add_witness` |
| `_todo_timer` | `timeout` | `_refresh_todo_board` |

### 3.3 匿名 / 局部连接（右侧面板按钮）

| 所在 | 控件 | 信号 | 槽 |
|---|---|---|---|
| `statement_group` | 局部按钮“复制” | `clicked` | `self._copy_statement()` |
| `statement_group` | 局部按钮“清空” | `clicked` | `self.statement_edit.clear()` |
| `material_group` | 局部按钮“复制” | `clicked` | `self._copy_material()` |
| `material_group` | 局部按钮“清空” | `clicked` | `self.material_list.clear()` |
| `material_group` | 局部按钮“新增” | `clicked` | `self.material_list.add_row()` |

> 重构时建议给这五个按钮补上变量名，方便统一连信号。

### 3.4 `MaterialListWidget` 内部连接（自定义控件，不动）

| 控件 | 信号 | 槽 |
|---|---|---|
| 每行复选框 `cb` | `toggled` | `self._on_changed` |
| 每行名称 `name_edit` | `textChanged` | `self._on_changed` |
| 每行备注 `note_edit` | `textChanged` | `self._on_changed` |

---

## 四、重构约束（每次让 AI 改界面前，把这段也贴给它）

```
重构主界面时的硬性约束：

1. 不要改任何控件变量名（见本文件第一节、第二节的完整清单）。
2. 不要改任何业务逻辑方法的签名和内部逻辑
   （approve、on_talk_button_clicked、_save_cases_data 等一律不动）。
3. 不要删除文件，只新建或原地修改。
4. 用布局管理器（QGridLayout / QFormLayout / QVBoxLayout / QHBoxLayout）
   重建界面，删除所有 _move_xxx / _compact_xxx / _align_xxx /
   _grow_xxx / _relocate_xxx 补丁。
5. 信号连接统一收进一个 _connect_signals() 方法。
6. 每改一处，告诉我"改了哪个文件、哪几行、为什么"。
7. 完成后给出"验收清单"，告诉我应该跑程序检查哪几个地方。
8. 如果某个控件是 MainWindow 代码创建的（见本文件第二节），
   重构时要把它一并搬进新的 UI 类，并在 MainWindow 里保留引用
   （变量名一致，业务代码能直接访问）。
9. 浮层控件（api_group、todo_board）保留绝对定位，但位置用
   Form.width() / Form.height() 计算，不要写死坐标。
10. 动态显隐的控件（witness_label / witness_combo / add_witness_btn）
    现在改成常显，重构时不要加回“随角色隐藏”的逻辑。
```

---

## 五、布局分区建议（ASCII 结构图）

```
┌──────────────────────────────────────────────────────────────────────┐
│ 顶栏 (top_bar, 28px)                                                  │
│  ⚙ config_toggle_btn   top_status_label          todo_btn            │
├───────────────────────────────┬──────────────────────────────────────┤
│ 左栏 (约 470px, 固定宽)         │ 右栏 (拉伸)                           │
│                                │                                       │
│ ┌─ 案本号行 ─────────────────┐ │ ┌─ statement_group ──────────────┐   │
│ │ label_10 lineEdit_2 btn_6 │ │ │ statement_edit                │   │
│ └───────────────────────────┘ │ │ [复制] [清空]                  │   │
│ ┌─ 单位性质 / 证人行 ────────┐ │ └───────────────────────────────┘   │
│ │ unit_type_label            │ │ ┌─ material_group ──────────────┐   │
│ │ unit_type_combo            │ │ │ material_list                 │   │
│ │ witness_label witness_combo│ │ │ [复制][清空][新增]             │   │
│ │ add_witness_btn            │ │ └───────────────────────────────┘   │
│ └───────────────────────────┘ │                                       │
│ ┌─ 案件类型 ─────────────────┐ │                                       │
│ │ deathCaseCheckbox          │ │                                       │
│ │ personalApplicationCheckbox│ │                                       │
│ └───────────────────────────┘ │                                       │
│ ┌─ 角色单选行 ───────────────┐ │                                       │
│ │ radioButton ~ _4           │ │                                       │
│ └───────────────────────────┘ │                                       │
│ ┌─ 个人信息表单 ─────────────┐ │                                       │
│ │ label_2 name_pane          │ │                                       │
│ │ label age_pane             │ │                                       │
│ │ label_11 lineEdit          │ │                                       │
│ │ label_3 idnumer_pane lbl_12│ │                                       │
│ │ label_4 textEdit           │ │                                       │
│ │ label_5 lineEdit_4         │ │                                       │
│ │ label_13 lineEdit_5        │ │                                       │
│ │ identity_label identity_edit│ │                                      │
│ │ label_6 comboBox           │ │                                       │
│ │ lbl_apply apply_time_edit  │ │                                       │
│ │ lbl_accept accept_time_edit│ │                                       │
│ │ lbl_injury injury_time_edit│ │                                       │
│ │ lbl_visit visit_time_edit  │ │                                       │
│ └───────────────────────────┘ │                                       │
│ ┌─ 单位信息 ─────────────────┐ │                                       │
│ │ label_7 company_pane btn_2 │ │                                       │
│ │ label_8 construction_company│ │                                      │
│ │ label_9 construction_plant │ │                                       │
│ └───────────────────────────┘ │                                       │
│ ┌─ 操作按钮行 ───────────────┐ │                                       │
│ │ btn_4 pushButton btn_ai    │ │                                       │
│ └───────────────────────────┘ │                                       │
│ ┌─ 底部按钮行 ───────────────┐ │                                       │
│ │ btn_11 btn_12 btn_7        │ │                                       │
│ └───────────────────────────┘ │                                       │
└───────────────────────────────┴──────────────────────────────────────┘

浮层（绝对定位，浮于所有控件之上）：
  api_group    ：顶栏下方，横跨整窗宽度
  todo_board   ：顶栏下方，横跨整窗宽度
```

推荐布局：

- 主窗口 → `QVBoxLayout`
  - 第 1 行：顶栏 `QHBoxLayout`（⚙ + 状态 + 弹簧 + 待办）
  - 第 2 行：`QHBoxLayout`（左栏 + 右栏）
- 左栏 → `QVBoxLayout`
  - 案本号行 → `QHBoxLayout`
  - 单位性质/证人行 → `QHBoxLayout`
  - 案件类型行 → `QHBoxLayout`
  - 角色单选行 → `QHBoxLayout`
  - 个人信息表单 → `QFormLayout` 或 `QGridLayout`
  - 时间字段 → `QGridLayout`（2 列 × 2 行）
  - 单位信息 → `QGridLayout`（3 行 × 3 列）
  - 操作按钮 → `QHBoxLayout`
  - 底部按钮 → `QHBoxLayout`
- 右栏 → `QVBoxLayout`
  - `statement_group`（内部 `QVBoxLayout`：`QTextEdit` + 按钮行 `QHBoxLayout`）
  - `material_group`（内部 `QVBoxLayout`：`MaterialListWidget` + 按钮行 `QHBoxLayout`）

---

## 六、验收检查清单（重构后逐项打勾）

跑程序，逐项确认：

- [ ] 窗口大小 870×850，最小/最大一致。
- [ ] 顶栏在顶部，⚙ 按钮、状态文字、待办按钮都在。
- [ ] 案本号行：标签、输入框、搜索按钮都在。
- [ ] 单位性质下拉 + 证人编号下拉 + 添加证人按钮在同一行。
- [ ] 工亡案件 / 个人案件两个复选框都在。
- [ ] 四个角色单选（本人/证人/法人/家属）在同一行。
- [ ] 姓名/年龄/性别一行三列。
- [ ] 身份证号 + label_12 提示。
- [ ] 住址输入框（多行）。
- [ ] 电话/岗位/身份一行三列。
- [ ] 拟用条例下拉框占满整行。
- [ ] 申请/受理时间一行两列，受伤/就诊时间一行两列。
- [ ] 单位信息三行（用人单位/用工单位/工地名称），各带保存按钮。
- [ ] 操作按钮：身份证导入 / 谈话笔录 / AI审查。
- [ ] 底部按钮：案件审批表 / 谈话通知书 / 工伤告知书。
- [ ] 右栏两个分组框撑满高度。
- [ ] 点 ⚙ 能展开/收起配置面板。
- [ ] 点“待办事项”能展开/收起看板。
- [ ] 四个角色单选切换时，表单清空/加载正常。
- [ ] 身份证号输入后，年龄/性别自动填充。
- [ ] 所有下拉框自动完成正常。
- [ ] 点“谈话笔录”“案件审批表”“工伤告知书”“谈话通知书”都能触发对应逻辑。
- [ ] 点“AI审查”能弹出进度对话框。
- [ ] 点“搜索”能弹出搜索结果。
- [ ] F2 键能轮换测试数据。

全部打勾后，进入第 4 轮（删除旧函数）。

---

*本文件最后更新：随界面重构同步维护。*