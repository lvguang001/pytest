import os
import json

import datetime
import logging
from typing import Dict, List, Any, Optional
from ctypes import windll, byref, create_string_buffer, c_int32, c_uint
import pandas as pd
from prompt_manager import render_prompt_template
from docx import Document
from docxtpl import DocxTemplate
from PyQt5.Qt import *
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication, QWidget, QMessageBox, QDialog, QVBoxLayout,
    QLabel, QTextEdit, QPushButton, QHBoxLayout, QInputDialog,
    QLineEdit, QCompleter, QCheckBox, QProgressDialog,
    QTableWidget, QTableWidgetItem,
)
from PyQt5.QtGui import QFont
from ui_main_build import MainWindowUI, ROLE_IDENTITY_HINT, ROLE_IDENTITY_LABEL
# 控件类搬到了 material_list.py；这里保留一份再导出，外部 `from app_main import
# MaterialListWidget` 的老写法仍然可用
from material_list import MaterialListWidget  # noqa: F401
from dialogs import AIReviewResultDialog, ApprovalDecisionDialog
import case_store
import documents
import transcripts
from services import (FileService, DataService, TemplateVariableManager,
                      CaseDataModel, PERSON_BASE_FIELDS, person_flat_key,
                      witness_seq_label, date_now, time_now, timestamp_now)
from ai_service import (AIService, AIWorker, TranscriptFromTemplateWorker,
                         parse_ai_result)
from case_classifier import (
    CaseClassifier, REGULATION_OPTIONS,
    UNIT_TYPES, UNIT_TYPE_APPELLATION, DEFAULT_UNIT_TYPE, DEFAULT_IDENTITY,
    compose_evidence, regulation_elements, regulation_full_to_short,
    regulation_short_to_full, regulation_full_for_unit,
    person_affiliation, notice_basis_sentence,
)
from config_service import ConfigService
from path_utils import path_utils
import log_utils
from main import UserManager
from service_flow import (derive, initial_sf, confirm_delivery, revert_to_ask,
                          delivered_again, finish_flow, DOC_LABEL, today_iso)
from todo_board import (DeliveryConfirmDialog, PostalTrackingDialog,
                        DecisionConfirmDialog)

logger = logging.getLogger(__name__)

# 设置日志级别
logging.getLogger('config_service').setLevel(logging.WARNING)


# 日期/时间显示格式、人记录字段与中文兼容扁平键、案件数据模型（CaseDataModel）
# 都在 services.py；「身份」输入行的角色文案在 ui_main_build.py。


# 案件数据的磁盘结构（分块格式）与落盘布局（一案一文件、备份、老档迁移）
# 整套都在 case_store.py。本文件里只剩 MainWindow 那层绑 BASE_PATH 的薄封装。


# 角色 → 谈话笔录生成配置（提示词 key / 笔录 docx 模板 / 模板占位符数据方法）——单一事实源
ROLE_TALK = {
    '本人': {
        'ai_prompt': 'self_send_to_ai',
        'talk_template': '本人谈话笔录（普通工伤案件）.docx',
        'docx_data': '_build_unified_template_data',
    },
    '证人': {
        'ai_prompt': 'witness_send_to_ai',
        'talk_template': '证人谈话笔录（普通工伤案件）.docx',
        'docx_data': '_build_witness_template_data',
    },
    '法人': {
        'ai_prompt': 'legal_send_to_ai',
        'talk_template': '法人谈话笔录（普通工伤案件）.docx',
        'docx_data': '_build_legal_template_data',
    },
    '家属': {
        'ai_prompt': 'family_send_to_ai',
        'talk_template': '家属谈话笔录（普通工伤案件）.docx',
        'docx_data': '_build_family_template_data',
    },
}


# ============================================================================
# 拟用条例、单位性质与证据清单见 case_classifier.py
#   （REGULATION_OPTIONS / REGULATION_ELEMENTS / compose_evidence /
#    regulation_short_to_full / UNIT_TYPES / DEFAULT_IDENTITY /
#    person_affiliation / notice_basis_sentence 等）
# ============================================================================


def _filter_provided(materials):
    """只保留「已提供（勾选）」的材料，转成 {name, notes} 格式"""
    if not isinstance(materials, list):
        return []
    return [
        {"name": m.get('name', ''), "notes": m.get('notes', '')}
        for m in materials
        if isinstance(m, dict) and m.get('provided') and m.get('name')
    ]


def _to_full_materials(provided_materials):
    """把 JSON 里的 {name, notes} 材料转回界面用的 {name, provided:True, notes}"""
    if not isinstance(provided_materials, list):
        return []
    return [
        {"name": m.get('name', ''), "provided": True, "notes": m.get('notes', '')}
        for m in provided_materials
        if isinstance(m, dict) and m.get('name')
    ]


# ============================================================================
# F2 测试数据预设（按 F2 轮换）
# ============================================================================

# F2 循环键。目前只留一条（本人·莫言）用于试生成笔录；原先的
# 工亡/证人/法人/机关公务员/事业单位几条预设已按需删除，需要时从 git 历史取回。
TEST_DATA_PRESETS = [{'name': '单位申请×工伤 本人(莫言)',
  'role': '本人',
  'deathCaseCheckbox': False,
  'personalApplicationCheckbox': False,
  'name_pane': '莫言',
  'idnumer_pane': '330324199003151234',
  'textEdit': '浙江省永嘉县瓯北街道XX路88号',
  'lineEdit_4': '13888880001',
  'lineEdit_5': '泥水工',
  'injured_worker': '莫言',
  'regulation': '第十四条第（一）项',
  'company_pane': '温州YY建筑劳务有限公司',
  'construction_company': '永嘉县XX建设工程有限公司',
  'construction_plant': 'ZZ新城项目一期工地',
  'statement_edit': '我单位职工莫言，男，1990年3月15日出生，身份证号330324199003151234。2026年7月20日16时20分许，莫言在工地3号楼5层搬运水泥时被滑落的水泥袋砸伤右脚，诊断为右足跖骨骨折。属工作时间工作场所因工作原因受伤，单位申请认定工伤。',
  'materials': [{'name': '身份证复印件', 'provided': True, 'notes': ''},
                {'name': '医院诊断证明书', 'provided': True, 'notes': '右足跖骨骨折'},
                {'name': '劳动合同', 'provided': True, 'notes': ''},
                {'name': '考勤记录', 'provided': False, 'notes': ''}]}]


class MainWindow(MainWindowUI):
    """主界面业务逻辑。

    界面构建（建控件 + 用布局管理器摆位置）在 ui_main_build.MainWindowUI，
    这里只剩业务；信号统一在 _connect_signals() 里接。
    """

    def __init__(self, parent=None, *args, **kwargs):
        # 建界面 + 用布局管理器摆好位置（见 ui_main_build.MainWindowUI）
        super().__init__(parent, *args, **kwargs)

        # .ui 里是驼峰名，这里补两个下划线别名给业务代码用
        self.death_case_checkbox = self.findChild(QCheckBox, "deathCaseCheckbox")
        self.personal_application_checkbox = self.findChild(QCheckBox, "personalApplicationCheckbox")

        # 信号统一在这里接（老代码散在 __init__ 内联、_setup_api_config_ui、
        # _setup_witness_ui、_setup_radio_connections、_install_todo_kanban 五处）。
        # 必须早接：api_user_combo、company_pane 那几条会在下面的初始化过程中
        # 就触发一次，晚接会漏掉这次触发。
        self._connect_signals()

        self._test_data_index = -1  # F2 测试数据轮换索引
        self.current_case_id = ""  # 当前案件案本号（跨角色/跨步骤保持同案关联）

        # lineEdit_2 改为案本号显示
        self.label_10.setText("案本号：")
        self.lineEdit_2.setPlaceholderText("输入本人姓名后自动生成")

        # 创建用户管理器
        self.user_manager = UserManager()
        self._load_saved_user_config()

        # 第一步：统一设置所有路径（必须在所有服务初始化之前）
        print("=" * 50)
        print("🚀 开始初始化 MainWindow")
        print("=" * 50)

        self._setup_paths()  # 统一使用 path_utils 设置路径

        # 第二步：初始化配置服务（但禁用其路径管理功能）
        self.config_service = ConfigService()
        # 禁用config_service的路径检查，避免干扰
        self.config_service.set('system.startup_check_disk_space', False)
        self.config_service.set('template.base_path', self.TEMPLATE_PATH)

        # 第三步：初始化组合框数据（必须在路径设置之后）
        self.init_combobox_data()

        # 第四步：初始化其他核心服务（使用正确的路径）
        self.file_service = FileService(self.BASE_PATH)
        self.data_service = DataService()

        # 第五步：更新所有服务的路径
        self._update_services_paths()

        # 第六步：初始化数据模型
        self.data_model = CaseDataModel()
        self.var_manager = TemplateVariableManager(self.data_model)
        self.data_model.output_config['用户名'] = self._get_current_username()

        # 简化日志系统
        self.setup_logging()

        # 保持向后兼容
        self._template_dict = self.data_model.to_template_dict()
        self.current_case_folder = None
        self.current_person_name = ""

        # 初始化数据
        case_config = self.config_service.get_case_config()
        self.set_data('案件性质', case_config.default_case_type, 'case')
        self.set_data('申请类型', case_config.default_application_type, 'case')

        # 初始化案件类型下拉框（全部条例情形，来自 case_classifier）
        for _short in REGULATION_OPTIONS:
            self.comboBox.addItem(regulation_short_to_full(_short))

        # 初始化组合框（必须在 init_combobox_data 之后）
        self.init_comboboxes()

        # 应用UI设置
        self._apply_ui_settings()

        # 初始化AI服务（使用传入的api_config）
        self.ai_service = None  # 先初始化为None
        self.init_ai_service()

        # 初始状态设为可用
        self.pushButton.setEnabled(True)

        # 单位性质 / 身份 的选项与文案。界面构建类不引用本文件的常量，故在这里补；
        # 必须等数据模型建好——填入选项会触发一次证据清单重算。
        self.unit_type_combo.addItems(UNIT_TYPES)
        self.unit_type_combo.setCurrentText(DEFAULT_UNIT_TYPE)
        self.identity_label.setText(ROLE_IDENTITY_LABEL["本人"])
        self.identity_edit.setPlaceholderText(DEFAULT_IDENTITY)

        # 验证模板路径（使用已获取的路径）
        if os.path.exists(self.TEMPLATE_PATH):
            print(f"✅ 模板路径可访问: {self.TEMPLATE_PATH}")
            print(f"✅ 谈话模板路径: {self.TALK_TEMPLATE_PATH}")
            print(f"✅ 文书模板路径: {self.DOCUMENT_TEMPLATE_PATH}")
        else:
            logger.error(f"❌ 模板路径不存在: {self.TEMPLATE_PATH}")

        self._install_todo_kanban()  # 待办事项看板（含 60 分钟定时刷新）

        # 证据清单：启动时先按当前条例/案件性质/申请类型列一遍，
        # 之后三者任一变化都跟着重算（那几个信号已在 _connect_signals() 接好）
        self._refresh_evidence_list()

        print("=" * 50)
        print("🎉 MainWindow 初始化完成")
        print("=" * 50)

    def _connect_signals(self):
        """所有信号连接集中在这里。

        `ui_main_window.Ui_Form.setupUi()` 自己接过一批（身份证号 editingFinished、
        身份证导入、三个保存按钮、四个角色单选、谈话通知书按钮），那份是生成代码，
        本次不动；这里只接原先散在 MainWindow 各处的那些。
        """
        # —— 共享人字段表单 ——
        self.name_pane.editingFinished.connect(self._on_name_pane_changed)

        # —— 单位 / 工地（三个可编辑下拉）——
        self.company_pane.currentTextChanged.connect(self.company)
        self.construction_company.currentTextChanged.connect(self.sync_employer_to_dict)
        self.construction_plant.currentTextChanged.connect(self.c_plant)

        # —— 案件类型与条例 ——
        self.death_case_checkbox.stateChanged.connect(self.on_case_type_changed)
        self.personal_application_checkbox.stateChanged.connect(self.on_case_type_changed)
        self.comboBox.currentIndexChanged.connect(self._refresh_evidence_list)
        self.death_case_checkbox.stateChanged.connect(self._refresh_evidence_list)
        self.personal_application_checkbox.stateChanged.connect(self._refresh_evidence_list)
        self.unit_type_combo.currentTextChanged.connect(self._refresh_evidence_list)

        # —— 四个时间字段 ——
        for edit in (self.apply_time_edit, self.accept_time_edit,
                     self.injury_time_edit, self.visit_time_edit):
            edit.editingFinished.connect(self._save_date_inputs)

        # —— 证人编号 ——
        self.witness_combo.currentIndexChanged.connect(self._on_witness_selected)
        self.add_witness_btn.clicked.connect(self._add_witness)

        # —— 顶栏与两个浮层 ——
        self.config_toggle_btn.clicked.connect(self._toggle_config_panel)
        self.todo_btn.clicked.connect(self._toggle_todo_panel)
        self.todo_board.taskClicked.connect(self._on_board_task_click)
        self.api_user_combo.currentTextChanged.connect(self._on_user_combo_changed)
        self.api_key_input.editingFinished.connect(self._on_api_edited)
        try:
            self.api_user_combo.lineEdit().editingFinished.connect(self._on_api_edited)
        except Exception:
            pass

        # —— 主操作按钮 ——
        self.pushButton_6.clicked.connect(self.smart_search_cases)
        self.pushButton_7.clicked.connect(self.generate_injury_notice)
        self.pushButton_11.clicked.connect(self.approve)   # 原 _reconnect_approval_button()
        self.pushButton_ai_review.clicked.connect(self.ai_review_document)
        # 下面两条在 .ui 里也各接过一次，**必须先断开再接**，否则同一个槽会挂两遍、
        # 点一次跑两次（谈话通知书原先就是这样）。
        for btn, slot in ((self.pushButton_12, self.on_pushButton_12_clicked),
                          (self.pushButton, self.on_talk_button_clicked)):
            try:
                btn.clicked.disconnect()
            except TypeError:
                pass          # 没有旧连接时 PyQt 抛 TypeError，忽略
            btn.clicked.connect(slot)

        # —— 右栏两个面板里的按钮（原先都是匿名控件 + lambda）——
        self.stmt_copy_btn.clicked.connect(self._copy_statement)
        self.stmt_clear_btn.clicked.connect(self.statement_edit.clear)
        self.mat_copy_btn.clicked.connect(self._copy_material)
        self.mat_clear_btn.clicked.connect(self.material_list.clear)
        self.mat_add_btn.clicked.connect(self.material_list.add_row)

    def on_talk_button_clicked(self):
        """谈话笔录按钮点击事件处理 — 数据核对 + 按角色生成笔录"""
        try:
            # 证人/法人/家属：核对前先把表单里新录的数据写回数据模型（open_data_review 保存后会把表单回填成本人数据）
            current_role = self.get_current_role_type()
            if current_role == "证人":
                self._sync_form_to_current_witness()
            elif current_role == "法人":
                self.update_role_info('法人')
            elif current_role == "家属":
                self.update_role_info('家属')

            # 工亡案件：职工本人已故，不能制作本人谈话笔录 → 提示改用 家属/证人/法人
            if current_role == "本人" and self.death_case_checkbox.isChecked():
                QMessageBox.warning(
                    self, "提示",
                    "工亡案件职工本人已故，无法制作本人谈话笔录。\n请改选『家属』（或证人/法人）作为被谈话人。"
                )
                return

            # 本人：已生成过笔录就先问要不要覆盖。
            # 放在保存之前——_save_case_from_form 会把案件数据写回磁盘，先存再问的话，
            # 点「否」也白存了一遍。第一次点按钮时案本号可能还没生成，那时也不会有
            # 旧笔录（_main_transcript_files 拿不到目录就返回空），自然放行。
            if current_role == "本人":
                case_id = self.current_case_id or self.lineEdit_2.text().strip()
                if not self._confirm_overwrite_main_transcript(case_id):
                    self._set_status('已取消', 'black')
                    return

            # ── 第一步：把当前录入存下来（后面的生成是从磁盘读案件数据的）──
            if not self._save_case_from_form():
                return

            # ── 第二步：按角色生成（同一套代码路径，仅提示词/模板按角色区分）──
            role = self.get_current_role_type()
            if role == "证人":
                # 上一步的回写会把表单填成本人数据，这里把表单切回当前证人显示
                self._sync_current_witness_to_form()
                # 证人：直接生成证人谈话笔录
                self._generate_role_transcript('证人')
            elif role == "法人":
                # 数据核对会把表单回填成本人数据，这里把表单切回法人显示
                self._sync_legal_to_form()
                # 法人：直接生成法人谈话笔录
                self._generate_role_transcript('法人')
            elif role == "家属":
                # 数据核对会把表单回填成本人数据，这里把表单切回家属显示
                self._sync_family_to_form()
                # 家属：直接生成家属谈话笔录（工亡案）
                self._generate_role_transcript('家属')
            else:
                # 本人：与其他角色同一条路——直接拼提示词生成（不再先跑一次条例判断）
                self._generate_role_transcript('本人')
        except Exception as e:
            print(f"谈话笔录按钮点击异常: {e}")
            import traceback
            traceback.print_exc()

    # ========================================================================
    # 保存当前录入（点「谈话笔录」的第一步）
    # ========================================================================

    def _save_case_from_form(self) -> bool:
        """把主界面当前录入收成案件对象、回写、落盘。

        点「谈话笔录」时先走这一步，因为后面的生成流程是**从磁盘读案件数据**的
        （见 `_generate_role_transcript`），不先存就找不到案件。

        案本号在这一步自动生成。原先是「弹一个 JSON 核对窗让用户改完再存」，
        2026-09 把核对窗删掉了——录入即保存，不再拦一道确认。
        """
        data, materials, _, _ = self._collect_review_data()

        # ── 自动生成案本号 ──
        case_id = str(data.get('case_id', '')).strip()
        if not case_id:
            case_id = self._auto_generate_case_number(
                data.get('name', ''), data.get('id_card', '')
            )
            data['case_id'] = case_id
        self.current_case_id = case_id

        case_obj = self._build_case_object(data, materials)
        case_obj['case_id'] = case_id

        # ── 回写主界面与数据模型（把自动生成的案本号等派生值显示出来）──
        self._apply_case_object(case_obj)

        # ── 保存到磁盘（保留既有扩展字段）──
        cases = self._load_cases_data()
        old = cases.get(case_id)
        if old:
            # 整体覆盖前保留既有扩展字段（service_flow/folder_name/transcript_file/
            # analysis_result/conclusion 等），避免重复保存时丢失
            for k, v in old.items():
                case_obj.setdefault(k, v)
        cases[case_id] = case_obj
        if not self._save_cases_data(cases):
            # 存不下就别继续了——下一步是从磁盘读案件数据的，读到的会是旧值或空
            self._set_status('案件数据保存失败，已中止', 'red')
            return False

        self._set_status(f'案件数据已保存（案本号：{case_id}）', 'green')
        return True

    def _collect_review_data(self):
        """从主界面与数据模型收集当前案件的全部字段（供核对窗口展示）"""
        # 本人字段以数据模型优先，避免证人/法人切换后 name_pane 串数据
        regulation_full = self.comboBox.currentText().strip()
        # 「拟用条例」例外：以下拉框为准。下拉框的改动不写回数据模型，数据模型里可能是
        # 旧值——曾经因此「改了下拉框却按旧条例保存并生成」，下拉框还会被刷回旧值。
        regulation_short = (regulation_full_to_short(regulation_full)
                            or self.get_data('拟用条例', ''))

        # 本人单位＝案件级用人单位。仅当前角色是「本人」时才采信「用人单位」控件里
        # 尚未保存的手输值——其余角色下该控件代表的是那个人自己的工作单位。
        self_unit = self.get_data('用人单位', '') or (
            self.company_pane.currentText().strip()
            if self.get_current_role_type() == '本人' else '')

        data = {
            'case_id': self.lineEdit_2.text().strip() or self.get_data('案本号', '') or self.current_case_id,
            'case_nature': '工亡案件' if self.death_case_checkbox.isChecked() else '工伤案件',
            'applicant_type': '个人申请' if self.personal_application_checkbox.isChecked() else '单位申请',
            'regulation': regulation_short,
            'apply_time': self._resolve_date_input(self.apply_time_edit.text()) if hasattr(self, 'apply_time_edit') else '',
            'accept_time': self._resolve_date_input(self.accept_time_edit.text()) if hasattr(self, 'accept_time_edit') else '',
            'visit_time': self._normalize_compact_time(self.visit_time_edit.text()) if hasattr(self, 'visit_time_edit') else '',
            'injury_time': self._normalize_compact_time(self.injury_time_edit.text()) if hasattr(self, 'injury_time_edit') else '',
            'name': self.get_data('本人姓名', '') or self.name_pane.text().strip(),
            'gender': self.get_data('本人性别', '') or self.lineEdit.text().strip(),
            'age': str(self.get_data('本人年龄', '') or self.age_pane.text().strip()),
            'id_card': self.get_data('本人身份证号', '') or self.idnumer_pane.text().strip(),
            'phone': self.get_data('本人手机号', '') or self.lineEdit_4.text().strip(),
            'address': self.get_data('本人身份证地址', '') or self.textEdit.toPlainText().strip(),
            'position': self.get_data('本人岗位', '') or self.lineEdit_5.text().strip(),
            'identity': (self.get_data('本人身份', '')
                         or (self.identity_edit.text().strip() if hasattr(self, 'identity_edit') else '')
                         or DEFAULT_IDENTITY),
            'unit_type': (self.unit_type_combo.currentText().strip() if hasattr(self, 'unit_type_combo')
                          else '') or DEFAULT_UNIT_TYPE,
            'employer': self.get_data('用工单位', '') or self.construction_company.currentText().strip(),
            'labor_unit': self_unit,
            'unit': self_unit,
            'site': self.get_data('工地名称', '') or self.construction_plant.currentText().strip(),
            # 「案件申请陈述」输入框已停用，受伤经过改从数据模型取——
            # 界面上那个框填什么都不再影响文书（值仍由 _apply_case_object 写入）
            'injury_desc': self.get_data('受伤经过', ''),
        }
        materials = (self.material_list.get_materials() if hasattr(self, 'material_list') else []) \
            or self.data_model.investigation.get('本人材料', [])
        witnesses = list(self.data_model.witnesses)
        legal_reps = self._collect_legal_reps()
        return data, materials, witnesses, legal_reps

    def _collect_legal_reps(self) -> List[Dict[str, Any]]:
        """收集法人信息（当前系统法人是单条，统一为规范人记录；兼容未来多条）"""
        person = self._person_from_flat('法人')
        if not person.get('name'):
            return []
        person['role'] = '法人'
        person['materials'] = self.data_model.investigation.get('法人材料', [])
        return [person]

    def _collect_family_reps(self) -> List[Dict[str, Any]]:
        """收集家属（近亲属）信息（工亡案单条，统一为规范人记录）"""
        person = self._person_from_flat('家属')
        if not person.get('name'):
            return []
        person['role'] = '家属'
        return [person]

    def _set_combo_or_type(self, combobox, text):
        """设置下拉框的值：存在则选中，否则输入（可编辑）或新增项（不可编辑）"""
        text = text or ""
        if not text:
            combobox.setCurrentIndex(-1)
            return
        idx = combobox.findText(text)
        if idx >= 0:
            combobox.setCurrentIndex(idx)
        elif combobox.isEditable():
            combobox.setEditText(text)
        else:
            combobox.addItem(text)
            combobox.setCurrentIndex(combobox.count() - 1)

    def _apply_regulation(self, short: str):
        """把「拟用条例」写回下拉框与数据模型

        下拉框**总是**跟着走（包括清空）：它才是这一个字段的权威来源。原先只有
        非空时才刷下拉框，于是加载一个没填条例的案子时，框里会留着上一个案子的条例。
        """
        short = (short or "").strip()
        self.set_data('拟用条例', short, 'case')
        full = regulation_short_to_full(short)
        self.set_data('引用条例', full, 'case')
        self._set_combo_or_type(self.comboBox, full)

    def _apply_case_object(self, case_obj: Dict[str, Any]):
        """把核对后的案件 JSON 对象回写到主界面控件与数据模型"""
        # 案件基本信息
        self.lineEdit_2.setText(str(case_obj.get('case_id', '')))
        self.set_data('案本号', case_obj.get('case_id', ''), 'case')

        self.death_case_checkbox.setChecked(case_obj.get('case_nature', '') == '工亡案件')
        self.personal_application_checkbox.setChecked(case_obj.get('applicant_type', '') == '个人申请')
        self.on_case_type_changed()

        # 单位性质（案件级）
        if hasattr(self, 'unit_type_combo'):
            ut = str(case_obj.get('unit_type', DEFAULT_UNIT_TYPE)) or DEFAULT_UNIT_TYPE
            self._set_combo_or_type(self.unit_type_combo, ut)
            self.set_data('单位性质', ut, 'case')

        self._apply_regulation(case_obj.get('proposed_article', ''))

        if hasattr(self, 'apply_time_edit'):
            self.apply_time_edit.setText(str(case_obj.get('apply_time', '')))
        if hasattr(self, 'accept_time_edit'):
            self.accept_time_edit.setText(str(case_obj.get('accept_time', '')))
        if hasattr(self, 'injury_time_edit'):
            self.injury_time_edit.setText(str(case_obj.get('injury_time', '')))
        if hasattr(self, 'visit_time_edit'):
            self.visit_time_edit.setText(str(case_obj.get('visit_time', '')))
        self._save_date_inputs()

        # 本人信息
        self.name_pane.setText(str(case_obj.get('name', '')))
        self.set_data('本人姓名', case_obj.get('name', ''), 'basic')
        self.lineEdit.setText(str(case_obj.get('gender', '')))
        self.set_data('本人性别', case_obj.get('gender', ''), 'basic')
        self.age_pane.setText(str(case_obj.get('age', '')))
        self.set_data('本人年龄', case_obj.get('age', ''), 'basic')
        self.idnumer_pane.setText(str(case_obj.get('id_card', '')))
        self.set_data('本人身份证号', case_obj.get('id_card', ''), 'basic')
        self.lineEdit_4.setText(str(case_obj.get('phone', '')))
        self.set_data('本人手机号', case_obj.get('phone', ''), 'basic')
        self.lineEdit_5.setText(str(case_obj.get('position', '')))
        self.set_data('本人岗位', case_obj.get('position', ''), 'basic')
        if hasattr(self, 'identity_edit'):
            self.identity_edit.setText(str(case_obj.get('identity', DEFAULT_IDENTITY)) or DEFAULT_IDENTITY)
        self.set_data('本人身份', str(case_obj.get('identity', DEFAULT_IDENTITY)) or DEFAULT_IDENTITY, 'basic')
        self.set_data('本人单位名称', case_obj.get('unit', ''), 'basic')
        self.textEdit.setPlainText(str(case_obj.get('address', '')))
        self.set_data('本人身份证地址', case_obj.get('address', ''), 'basic')

        # 单位信息（company_pane=用人单位，construction_company=用工单位）
        # 只写数据模型，不直接推控件：company_pane 是共享控件，其 currentTextChanged
        # 会把值写进*当前角色*的 unit 槽，非本人角色下推案件级值会污染该人的单位。
        # 控件由方法末尾的 _restore_role_unit() 按角色回填。
        self.set_data('用人单位', case_obj.get('labor_unit', ''), 'company')
        self._set_combo_or_type(self.construction_company, case_obj.get('employer', ''))
        self.set_data('用工单位', case_obj.get('employer', ''), 'company')
        self._set_combo_or_type(self.construction_plant, case_obj.get('site', ''))
        self.set_data('工地名称', case_obj.get('site', ''), 'company')

        # 受伤经过：只写数据模型，不再回填到「案件申请陈述」框
        # （该框已停用，回填会让用户以为改了有效——见 ui_main_build 里
        #  _create_right_panel_widgets 的说明）
        self.set_data('受伤经过', case_obj.get('injury_description', ''), 'investigation')
        if hasattr(self, 'statement_edit'):
            self.statement_edit.clear()   # 清掉残留，免得看着像本案陈述

        # 材料清单（本人）—— JSON 里只存了「已提供」，转回界面格式
        materials_full = _to_full_materials(case_obj.get('materials', []))
        if hasattr(self, 'material_list'):
            self.material_list.set_materials(materials_full)
            # 载入案件自带材料后，再补上按条例/性质应备而清单里没有的
            self._refresh_evidence_list()
        self.data_model.investigation['本人材料'] = materials_full

        # 法人信息回写（统一规范人记录 → 法人* 扁平兼容键，含性别/年龄）
        legal_reps = case_obj.get('legal_reps', [])
        if legal_reps:
            lr = legal_reps[0]
            for field in PERSON_BASE_FIELDS:
                val = lr.get(field)
                if val:
                    self.set_data(person_flat_key('法人', field), val, 'basic')
            self.data_model.investigation['法人材料'] = lr.get('materials', [])

        # 家属信息回写（工亡案单条 → 家属* 扁平兼容键，含 与死者关系/单位/岗位）
        family_reps = case_obj.get('family_reps', [])
        if family_reps:
            fr = family_reps[0]
            for field in PERSON_BASE_FIELDS:
                val = fr.get(field)
                if val:
                    self.set_data(person_flat_key('家属', field), val, 'basic')

        # 上面把「用人单位」写成了案件级值；若当前不在本人角色，要换回该角色自己的单位
        self._restore_role_unit()

        # 刷新模板字典缓存
        self._template_dict = self.data_model.to_template_dict()
        print("✅ 核对数据已回写主界面与数据模型")

    # ========================================================================
    # 案件 JSON 持久化（案本号为键）
    # ========================================================================

    # ---- 案件数据的磁盘布局：一案一文件 ----
    # 实现在 case_store.py —— 把 base_path 显式当参数传进去，就不必构造窗口也能
    # 单测（见 tests/test_case_store.py）。这里留一层薄封装，把 self.BASE_PATH 绑上，
    # 免得 40 多处调用点全要改成 case_store.xxx(self.BASE_PATH, ...)。

    def _locate_case_dir(self, case_id: str) -> str:
        return case_store.locate_case_dir(self.BASE_PATH, case_id)

    def _case_dir(self, case_id: str) -> str:
        return case_store.case_dir(self.BASE_PATH, case_id)

    def _load_cases_data(self) -> Dict[str, Any]:
        return case_store.load_all(self.BASE_PATH)

    def _save_cases_data(self, cases: Dict[str, Any]) -> bool:
        return case_store.save_all(self.BASE_PATH, cases)

    def _update_case_field(self, case_number: str, **fields) -> bool:
        return case_store.update_case_field(self.BASE_PATH, case_number, **fields)


    def _refresh_evidence_list(self, *_ignored):
        """按当前的 拟用条例 / 工亡案件 / 个人案件 重算材料清单。

        下拉框与两个复选框任一变化都会重算（都接到了这里，故用 *_ignored 吞掉
        Qt 传来的 index/state 参数）。

        案件自带和手工添加的材料不受影响，同名项也不会重复
        （见 MaterialListWidget.apply_evidence_list）。
        """
        if not hasattr(self, "material_list"):
            return
        # 和 _collect_review_data 同一口径：以下拉框为准，数据模型里的可能是旧值
        short = (regulation_full_to_short(self.comboBox.currentText().strip())
                 or self.get_data('拟用条例', ''))
        unit_type = (self.unit_type_combo.currentText().strip()
                     if hasattr(self, 'unit_type_combo') else DEFAULT_UNIT_TYPE)
        items = compose_evidence(short,
                                 self.death_case_checkbox.isChecked(),
                                 self.personal_application_checkbox.isChecked(),
                                 unit_type)
        self.material_list.apply_evidence_list(items)

    # ========================================================================
    # 待办事项看板：文书送达流程（个人申请工伤案）
    # ========================================================================

    def _install_todo_kanban(self):
        """待办看板的定时刷新：每小时按“今天”重算各案件节点/剩余天数。

        按钮与看板控件本身（todo_btn / todo_board）由 MainWindowUI 建好并摆位，
        它们的信号在 _connect_signals() 里接；这里只管定时器与首次刷新。
        """
        self._todo_open = False
        # 定时刷新（任务文字/倒计时/到期流转）—— 每小时一次
        self._todo_timer = QTimer(self)
        self._todo_timer.setInterval(60 * 60 * 1000)
        self._todo_timer.timeout.connect(self._refresh_todo_board)
        self._todo_timer.start()
        self._refresh_todo_board()

    def _toggle_todo_panel(self):
        """待办事项菜单按钮：展开/收起下拉看板。"""
        self._todo_open = not self._todo_open
        self._refresh_todo_board()  # 展开前确保任务/计数最新
        self.todo_board.setVisible(self._todo_open)
        if self._todo_open:
            self.todo_board.raise_()

    def _collect_todo_rows(self):
        """遍历全部案件，返回「个人申请 + 已建卡 + 未结束」案件的 (case_id, derive结果)。"""
        try:
            cases = self._load_cases_data()
        except Exception:
            cases = {}
        today = today_iso()
        rows = []
        for cid, cobj in cases.items():
            try:
                if str(cobj.get('applicant_type', '')) != '个人申请':
                    continue
                sf = cobj.get('service_flow')
                if not sf or sf.get('done'):
                    continue
                d = derive(sf, today)
                if d.get('phase') in ('none', 'done'):
                    continue
                rows.append((cid, d))
            except Exception:
                continue
        return rows

    def _refresh_todo_board(self):
        """刷新下拉看板内容与顶栏按钮上的任务计数。"""
        if not getattr(self, 'todo_board', None) or not getattr(self, 'todo_btn', None):
            return
        rows = self._collect_todo_rows()
        self.todo_board.set_tasks(rows)
        self.todo_btn.setText(f"待办事项({len(rows)})")

    def _ensure_service_flow_started(self, case_id: str, case_obj: dict = None, role: str = ""):
        """个人申请案：录入第一份谈话笔录成功后自动建卡（幂等；老案件不补建）。
        建卡口径：目录中「谈话笔录」docx 份数 <=1（即功能上线后首次产生笔录）。"""
        try:
            cases = self._load_cases_data()
        except Exception:
            return
        stored = cases.get(case_id)
        if not stored:
            if not case_obj:
                return
            stored = case_obj
        if str(stored.get('applicant_type', '')) != '个人申请':
            return
        if stored.get('service_flow'):
            return
        # 老案件（此前已有笔录）不补建
        try:
            folder = self._case_dir(case_id)
            cnt = 0
            if os.path.isdir(folder):
                cnt = sum(1 for f in os.listdir(folder)
                          if f.lower().endswith('.docx') and '谈话笔录' in f)
            if cnt > 1:
                return
        except Exception:
            pass
        stored['service_flow'] = initial_sf(case_id, role=role)
        cases[case_id] = stored
        try:
            self._save_cases_data(cases)
        except Exception:
            return
        self._set_status(f'已建立文书送达待办：{case_id}', 'green')
        self._refresh_todo_board()

    def _persist_service_flow(self, case_id: str, nsf: dict) -> bool:
        """把新的 service_flow 写回案件 JSON 并刷新看板。"""
        try:
            cases = self._load_cases_data()
            if case_id not in cases:
                return False
            cases[case_id]['service_flow'] = nsf
            ok = self._save_cases_data(cases)
            self._refresh_todo_board()
            return ok
        except Exception:
            return False

    def _on_board_task_click(self, case_id: str):
        """看板任务点击：先收起下拉，再重新读盘并按当前节点派生 → 打开对应弹窗。"""
        if getattr(self, 'todo_board', None):
            self.todo_board.hide()
            self._todo_open = False
        try:
            cases = self._load_cases_data()
        except Exception:
            return
        cobj = cases.get(case_id)
        if not cobj or not cobj.get('service_flow'):
            return
        sf = cobj['service_flow']
        d = derive(sf, today_iso())
        action = d.get('action')
        if action == 'delivery_confirm':
            self._open_delivery_confirm(case_id, d.get('doc'),
                                        prefill=bool(d.get('prefill', False)))
        elif action == 'postal_tracking':
            self._open_postal_tracking(case_id, d.get('doc'))
        elif action == 'decision':
            self._on_decision_make(case_id)
        else:
            self._refresh_todo_board()

    def _open_delivery_confirm(self, case_id: str, doc: str, prefill: bool = False):
        """送达确认窗（举证/告知书复用）。保存 → confirm_delivery 落盘重算。"""
        try:
            cobj = self._load_cases_data().get(case_id)
        except Exception:
            return
        sf = cobj.get('service_flow') if cobj else None
        if not sf:
            return
        st = sf.get('stage', {})
        dlg = DeliveryConfirmDialog(
            case_id, doc, self,
            prefill_method=(st.get('method') if prefill else None),
            prefill_deliver=(st.get('deliver_time') if prefill else ''),
            prefill_send=(st.get('send_time') if prefill else ''))
        if dlg.exec_() != QDialog.Accepted:
            return
        nsf = confirm_delivery(sf, doc, dlg.get_method(),
                               dlg.get_deliver_time(), dlg.get_send_time())
        self._persist_service_flow(case_id, nsf)
        self._set_status(f'已记录{DOC_LABEL.get(doc, doc)}送达确认', 'green')

    def _open_postal_tracking(self, case_id: str, doc: str):
        """邮寄送达状态追踪。①② → 退回送达确认第一步并重开窗；③ → 更新送达时间。"""
        try:
            cobj = self._load_cases_data().get(case_id)
        except Exception:
            return
        sf = cobj.get('service_flow') if cobj else None
        if not sf:
            return
        dlg = PostalTrackingDialog(case_id, doc, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        act = dlg.get_action()
        if act in ('resend', 'switch'):
            nsf = revert_to_ask(sf, act, note=dlg.get_note())
            self._persist_service_flow(case_id, nsf)
            # 退回「送达确认」第一步：立即重开窗（再次寄送保留方式，改用其它方式则清空）
            self._open_delivery_confirm(case_id, doc, prefill=(act == 'resend'))
        elif act == 'delivered':
            nsf = delivered_again(sf, dlg.get_new_deliver_time(), note=dlg.get_note())
            self._persist_service_flow(case_id, nsf)
            self._set_status('已确认送达，倒计时按新的送达时间重算', 'green')
        else:
            self._refresh_todo_board()

    def _on_decision_make(self, case_id: str):
        """制作工伤认定决定书确认。是 → 结束流程(移除待办)并触发“案件审批表”(approve)。"""
        try:
            cobj = self._load_cases_data().get(case_id)
        except Exception:
            return
        sf = cobj.get('service_flow') if cobj else None
        if not sf:
            return
        dlg = DecisionConfirmDialog(case_id, self)
        if dlg.exec_() != QDialog.Accepted or not dlg.get_choice():
            return  # 否：关闭，保持该待办等待下次处理
        # 是：视为已触发制作动作 → 流程结束，删除本案件提醒
        self._persist_service_flow(case_id, finish_flow(sf))
        self._set_status(f'{case_id} 文书送达流程结束，触发制作工伤认定决定书(案件审批表)', 'green')
        # 装载该案到主界面，使 approve() 读到正确案本号/日期
        try:
            self._apply_case_object(cobj)
        except Exception as e:
            logger.warning(f"⚠️ 决定书装载案件到主界面失败: {e}")
        self.lineEdit_2.setText(case_id)
        QTimer.singleShot(0, self.approve)  # 等同点击“案件审批表”按钮

    def _build_unified_template_data(self, case_obj: Dict[str, Any]) -> Dict[str, Any]:
        """统一模板渲染字典（实现在 transcripts.py；这里补上环境：用户名、当前时期）"""
        return transcripts.build_unified_template_data(
            case_obj,
            username=self._get_current_username(),
            current_period=self.get_data('当前时期', ''),
        )

    def _build_case_object(self, data, materials) -> Dict[str, Any]:
        """构建单个案件对象（case_id 为第一字段）

        - 本人数据平铺在顶层，injury_description 仅本人
        - materials 只保留「已提供（勾选）」的证据
        - 含记录人、申请人名称、证人（并入 cases_data.json，单一数据源）
        """
        case = {
            "case_id": data.get('case_id', ''),
            "applicant_name": data.get('name', '') if data.get('applicant_type', '') == '个人申请' else data.get('labor_unit', ''),
            "case_nature": data.get('case_nature', ''),
            "applicant_type": data.get('applicant_type', ''),
            "unit_type": data.get('unit_type', DEFAULT_UNIT_TYPE),
            "employer": data.get('employer', ''),
            "labor_unit": data.get('labor_unit', ''),
            "site": data.get('site', ''),
            "apply_time": data.get('apply_time', ''),
            "accept_time": data.get('accept_time', ''),
            "visit_time": data.get('visit_time', ''),
            "injury_time": data.get('injury_time', ''),
            "proposed_article": data.get('regulation', ''),
            "proposed_article_elements": regulation_elements(data.get('regulation', '')),
            # ── 本人（一套完整数据）──
            "name": data.get('name', ''),
            "gender": data.get('gender', ''),
            "age": data.get('age', ''),
            "id_card": data.get('id_card', ''),
            "phone": data.get('phone', ''),
            "address": data.get('address', ''),
            "position": data.get('position', ''),
            "identity": data.get('identity', DEFAULT_IDENTITY),
            "unit": data.get('unit', '') or self.get_data('本人单位名称', ''),
            "injury_description": data.get('injury_desc', ''),
            "materials": _filter_provided(materials),
            # ── 记录人 / 证人 ──
            "recorder": self._get_current_username(),
            "witnesses": list(self.data_model.witnesses),
            "legal_reps": self._collect_legal_reps(),
            "family_reps": self._collect_family_reps(),
        }
        return case

    def _start_transcript_generation(self, role: str, case_id: str, case_obj: dict, prompt_text: str):
        """统一的笔录生成启动（本人/证人/法人共用一条代码路径）：txt 提示词 → AI 后台线程"""
        self._set_status(f'正在AI生成{role}谈话笔录...', 'black')
        QApplication.processEvents()
        self.transcript_worker = TranscriptFromTemplateWorker(self.ai_service, prompt_text)
        self.transcript_worker.finished.connect(
            lambda result, r=role, cid=case_id, co=case_obj: self._on_transcript_generated(r, cid, co, result)
        )
        self.transcript_worker.error.connect(self._on_transcript_error)
        self.transcript_worker.start()

    def _prompt_fill_data(self, role: str, case_obj: dict) -> Dict[str, Any]:
        """「发给AI」提示词的填充数据（实现在 transcripts.py）

        证人要先确保存在一条当前证人记录——那一步有副作用，所以留在这一层。
        """
        witness = None
        if role == '证人':
            self._ensure_current_witness()
            # 不调用 _sync_form_to_current_witness（open_data_review 已把表单回填成
            # 本人数据，会污染证人）
            witness = self._current_witness() or {}
        return transcripts.prompt_fill_data(
            role, case_obj,
            get_data=self.get_data,
            witness=witness,
            username=self._get_current_username(),
            current_period=self.get_data('当前时期', ''),
        )

    def _build_prompt_for_role(self, role: str, case_obj: dict) -> str:
        """按角色返回发给 AI 的 txt 提示词（ROLE_TALK 定 key，统一渲染并校验残留占位符）

        条件块（时间核对、第（六）项问现住址）已写进各自的 txt 模板，用 `{% if %}` 控制，
        代码这边只负责填数据——要改措辞或加减条件，改 resource/prompts/ 下的 txt 即可。
        """
        from prompt_manager import load_prompt
        meta = ROLE_TALK.get(role, ROLE_TALK['本人'])
        prompt = load_prompt(meta['ai_prompt'])
        return render_prompt_template(prompt, self._prompt_fill_data(role, case_obj), role)

    def _build_prompt_or_warn(self, role: str, case_obj: dict) -> Optional[str]:
        """拼提示词；提示词文件缺失/为空时给个明确提示，返回 None（别让程序闪退）。

        这个异常必须在这里兜住：调用它的是 Qt 槽函数，漏出去的异常会被 qFatal 直接中止
        进程（见 log_utils.install_excepthook），用户只会看到程序莫名其妙关掉。
        """
        from prompt_manager import PromptError
        try:
            return self._build_prompt_for_role(role, case_obj)
        except PromptError as e:
            logger.error(f"❌ 无法生成{role}谈话笔录：{e}")
            QMessageBox.critical(
                self, "提示词文件有问题",
                f"无法生成{role}谈话笔录。\n\n{e}\n\n"
                f"请检查 resource/prompts/ 下的提示词文件（可能被删除、清空或被占用）。")
            self._set_status(f'{role}笔录：提示词文件缺失或为空', 'red')
            return None

    def _generate_role_transcript(self, role: str):
        """统一的谈话笔录生成入口——四个角色都走这里（数据核对确认后拼提示词 → AI 后台线程）"""
        case_id = self.current_case_id or self.lineEdit_2.text().strip()
        if not case_id:
            self._set_status(f'无案本号，无法生成{role}笔录', 'orange')
            return
        if not self.ai_service:
            self._set_status('未配置AI，无法生成笔录', 'orange')
            QMessageBox.warning(self, "提示", f"未配置API密钥，无法生成{role}谈话笔录。\n请在顶部⚙配置中设置API密钥。")
            return
        case_obj = self._load_cases_data().get(case_id)
        if not case_obj:
            self._set_status('未找到该案本号的案件数据', 'orange')
            return
        prompt_text = self._build_prompt_or_warn(role, case_obj)
        if prompt_text is None:
            return
        self._start_transcript_generation(role, case_id, case_obj, prompt_text)

    def _on_transcript_generated(self, role: str, case_id: str, case_obj: dict, result: dict):
        if result.get("状态") != "成功":
            err = result.get("错误信息", "未知错误")
            logger.warning(f"⚠️ {role}谈话笔录生成失败: {err}")
            self._set_status(f'{role}谈话笔录生成失败: {err[:40]}', 'orange')
            return
        content = (result.get("内容", "") or "").strip()
        if not content:
            logger.warning(f"⚠️ {role}谈话笔录生成失败：AI 返回内容为空")
            self._set_status(f'{role}谈话笔录生成失败：AI 返回内容为空', 'orange')
            return
        path = self._save_transcript_to_template(case_obj, content, role)
        if not path:
            return
        if role == '证人':
            self._save_witnesses()  # 持久化证人数据
        # 个人申请案：录入第一份谈话笔录后自动建立“文书送达”待办（看板）
        self._ensure_service_flow_started(case_id, case_obj, role)
        success, message = self.file_service.open_document(path)
        if success:
            self._set_status(f'已生成并打开{role}谈话笔录', 'green')
        else:
            self._set_status(f'{role}谈话笔录已生成，打开失败: {message}', 'orange')

    def _on_transcript_error(self, err: str):
        logger.error(f"❌ 询问笔录生成出错: {err}")
        self._set_status('询问笔录生成出错', 'red')

    def _main_transcript_files(self, case_id: str) -> List[str]:
        """该案卷目录下已生成的本人笔录文件（基础名与 (2)(3) 副本都算）

        用 _locate_case_dir 找**已存在**的目录——_case_dir 在案件还没落盘时会返回一个
        尚不存在的新路径。各角色 label 不同（本人/证人/法人/家属谈话笔录），
        按「本人谈话笔录」匹配不会误伤别的角色。
        """
        folder = self._locate_case_dir(case_id) if case_id else ''
        if not folder or not os.path.isdir(folder):
            return []
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        return [os.path.join(folder, n) for n in sorted(names)
                if n.endswith('.docx') and '本人谈话笔录' in n]

    def _delete_main_transcripts(self, paths: List[str]) -> bool:
        """删掉旧的本人笔录；有文件删不掉（例如正被 Word 打开）就返回 False 并提示"""
        failed = []
        for p in paths:
            try:
                os.remove(p)
                print(f"🗑️ 已删除旧的本人笔录: {p}")
            except OSError as e:
                logger.warning("⚠️ 删除旧笔录失败 %s: %s", p, e)
                failed.append(p)
        if failed:
            QMessageBox.warning(
                self, "无法覆盖",
                "删除旧的本人笔录失败（可能正被 Word/WPS 打开）：\n%s\n\n"
                "请先关闭该笔录文档，再点一次「谈话笔录」。" % '\n'.join(failed))
            return False
        return True

    def _confirm_overwrite_main_transcript(self, case_id: str) -> bool:
        """本人笔录已存在时问一句要不要覆盖；返回 True 表示可以继续生成。

        点「是」会把旧笔录（含之前累积的 (2)(3) 副本）一起删掉，随后按主界面当前
        数据重新生成；删不掉就不继续，免得又堆一个新文件。
        """
        old = self._main_transcript_files(case_id)
        if not old:
            return True
        msg = QMessageBox(self)
        msg.setWindowTitle("本人笔录已存在")
        msg.setIcon(QMessageBox.Question)
        msg.setText("该案本号下已生成过本人谈话笔录，是否覆盖？")
        msg.setInformativeText(
            "点「是」会删掉旧的笔录文件（含之前生成的副本 %d 个），"
            "然后按主界面当前数据重新生成。" % len(old))
        yes_btn = msg.addButton("是", QMessageBox.YesRole)
        no_btn = msg.addButton("否", QMessageBox.NoRole)
        msg.setDefaultButton(no_btn)          # 默认落在「否」：手快回车不该把旧的删了
        msg.exec_()
        if msg.clickedButton() is not yes_btn:
            logger.info("用户选择不覆盖旧的本人笔录")
            return False
        return self._delete_main_transcripts(old)

    def _save_transcript_to_template(self, case_obj: dict, content: str, role: str = '本人') -> str:
        """渲染并保存该角色的谈话笔录（实现在 transcripts.py）

        这一层负责「选模板、定案卷目录、失败时报状态栏」；渲染本身是纯的。
        """
        try:
            meta = ROLE_TALK.get(role, ROLE_TALK['本人'])
            label = f'{role}谈话笔录'
            template_path = str(path_utils.get_talk_template_path(meta['talk_template']))
            template_data = getattr(self, meta['docx_data'])(case_obj)

            # 案卷目录：年份目录 + 已存在的按原位
            # （走 _case_dir，文书和数据必须落在同一个案卷文件夹里）
            if not self.current_case_folder or not os.path.exists(self.current_case_folder):
                folder_subject = str(case_obj.get('case_id', '') or case_obj.get('name', '') or '案件').strip()
                self.current_case_folder = self._case_dir(folder_subject)

            subject = str(case_obj.get('name', '') or case_obj.get('case_id', '') or '案件').strip()
            return transcripts.render_transcript(
                template_path, template_data, content,
                out_dir=self.current_case_folder,
                file_base=f"{subject}{label}", label=label,
            )
        except Exception as e:
            logger.error(f"❌ 生成{role}谈话笔录失败: {e}")
            import traceback
            traceback.print_exc()
            self._set_status(f'生成{role}谈话笔录失败', 'red')
            return ""

    def _build_witness_template_data(self, case_obj: dict) -> dict:
        """构建证人谈话笔录模板的占位符数据"""
        self._ensure_current_witness()
        # 注意：不调用 _sync_form_to_current_witness（open_data_review 已把表单回填成本人数据，会污染证人）
        w = self._current_witness() or {}
        return {
            '当前时期': self.get_data('当前时期', '') or (date_now() + time_now()),
            '用户名': self._get_current_username(),
            '本人姓名': case_obj.get('name', ''),
            '证人姓名': w.get('name', '') or self.get_data('证人姓名', ''),
            '证人性别': w.get('gender', '') or self.get_data('证人性别', ''),
            '证人年龄': w.get('age', '') or self.get_data('证人年龄', ''),
            '证人身份证号': w.get('id_card', '') or self.get_data('证人身份证号', ''),
            '证人身份证地址': w.get('address', '') or self.get_data('证人身份证地址', ''),
            '证人手机号': w.get('phone', '') or self.get_data('证人手机号', ''),
            '证人岗位': w.get('position', '') or self.get_data('证人岗位', ''),
            '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
            '单位名称': case_obj.get('labor_unit', ''),
            '证人身份': w.get('identity') or self.get_data('证人身份', '') or DEFAULT_IDENTITY,
            '公司名称': case_obj.get('labor_unit', ''),  # 用人单位（签合同的单位）
        }


    # ========================================================================
    # 法人谈话笔录：表单同步 + 提示词 / 模板占位符数据（生成统一走 _generate_role_transcript）
    # ========================================================================

    def _sync_legal_to_form(self):
        """数据核对后把表单切回法人数据（open_data_review 会把表单回填成本人数据）"""
        if self.get_current_role_type() != "法人":
            return
        self._write_person_to_form(self._person_from_flat('法人'))

    def _sync_family_to_form(self):
        """数据核对后把表单切回家属数据（open_data_review 会把表单回填成本人数据）"""
        if self.get_current_role_type() != "家属":
            return
        self._write_person_to_form(self._person_from_flat('家属'))

    def _build_legal_template_data(self, case_obj: dict) -> dict:
        """构建法人谈话笔录模板的占位符数据"""
        return {
            '当前时期': self.get_data('当前时期', '') or (date_now() + time_now()),
            '用户名': self._get_current_username(),
            '本人姓名': case_obj.get('name', ''),
            '法人姓名': self.get_data('法人姓名', ''),
            '法人性别': self.get_data('法人性别', ''),
            '法人年龄': self.get_data('法人年龄', ''),
            '法人身份证号': self.get_data('法人身份证号', ''),
            '法人身份证地址': self.get_data('法人身份证地址', ''),
            '法人手机号': self.get_data('法人手机号', ''),
            '法人职务': self.get_data('法人职务', ''),
            '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
            '单位名称': case_obj.get('labor_unit', ''),
            '法人身份': self.get_data('法人身份', '') or DEFAULT_IDENTITY,
            '公司名称': case_obj.get('labor_unit', ''),  # 用人单位（签合同的单位）
        }

    def _build_family_template_data(self, case_obj: dict) -> dict:
        """构建家属谈话笔录模板的占位符数据（被询问人=工亡职工近亲属；本人姓名 指死者）

        表头那行描述的是「家属自己的单位」：家属单位名称 逐字取自「用人单位」控件
        （本人角色下才是案件级用人单位）。家属没单位时，岗位一并留空。
        """
        fam_unit = self.get_data('家属单位名称', '')
        return {
            '当前时期': self.get_data('当前时期', '') or (date_now() + time_now()),
            '用户名': self._get_current_username(),
            '本人姓名': case_obj.get('name', ''),      # 死者姓名
            '家属姓名': self.get_data('家属姓名', ''),
            '家属性别': self.get_data('家属性别', ''),
            '家属年龄': self.get_data('家属年龄', ''),
            '家属身份证号': self.get_data('家属身份证号', ''),
            '家属身份证地址': self.get_data('家属身份证地址', ''),
            '家属手机号': self.get_data('家属手机号', ''),
            '与死者关系': self.get_data('家属身份', ''),
            '家属单位名称': fam_unit,
            '家属岗位': self.get_data('家属岗位', '') if fam_unit else '',
            '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
            '单位名称': case_obj.get('labor_unit', ''),
            '家属身份': self.get_data('家属身份', ''),
            '公司名称': case_obj.get('labor_unit', ''),  # 用人单位（签合同的单位）
        }


    # ========================================================================
    # F2 测试数据轮换
    # ========================================================================

    def keyPressEvent(self, event):
        """F2 键轮换测试数据"""
        if event.key() == Qt.Key_F2:
            self._cycle_test_data()
        else:
            super().keyPressEvent(event)

    def _cycle_test_data(self):
        """按 F2 切换到下一组测试数据"""
        self._test_data_index = (self._test_data_index + 1) % len(TEST_DATA_PRESETS)
        data = TEST_DATA_PRESETS[self._test_data_index]

        print(f"\n{'=' * 50}")
        print(f"F2 测试数据: {data['name']}")
        print(f"{'=' * 50}")

        # ── 角色单选按钮 ──
        role_map = {"本人": self.radioButton, "证人": self.radioButton_2,
                    "法人": self.radioButton_3, "家属": self.radioButton_4}
        for role_name, btn in role_map.items():
            btn.setChecked(role_name == data["role"])
        # 程序化 setChecked 不会触发 clicked，手动触发一次角色切换清理
        self.clear_role_fields()

        # ── 案例类型复选框 ──
        self.death_case_checkbox.setChecked(data["deathCaseCheckbox"])
        self.personal_application_checkbox.setChecked(data["personalApplicationCheckbox"])
        self.on_case_type_changed()

        # ── 基本信息输入框 ──
        self.name_pane.setText(data["name_pane"])
        self.idnumer_pane.setText(data["idnumer_pane"])
        self.textEdit.setPlainText(data["textEdit"])
        self.lineEdit_4.setText(data["lineEdit_4"])
        self.lineEdit_5.setText(data["lineEdit_5"])
        # 案本号：切到本人时清空（输入新姓名后自动生成）；切到证人/法人保留同一案本号
        if data["role"] == "本人":
            self.lineEdit_2.clear()

        # ── 条例下拉框 ──
        # 拟用条例按规范短名定位，不要用索引——增删条例会让索引整体位移，
        # 预设就静默指到别的条例上去了
        _reg_idx = self.comboBox.findText(regulation_short_to_full(data.get("regulation", "")))
        if _reg_idx >= 0:
            self.comboBox.setCurrentIndex(_reg_idx)

        # ── 公司下拉框 ──
        self._set_combo_or_type(self.company_pane, data["company_pane"])
        self._set_combo_or_type(self.construction_company, data["construction_company"])
        self._set_combo_or_type(self.construction_plant, data["construction_plant"])

        # ── 单位性质 / 人员身份（机关公务员/事业单位支持）──
        if hasattr(self, 'unit_type_combo'):
            self._set_combo_or_type(self.unit_type_combo, data.get('unit_type', DEFAULT_UNIT_TYPE))
        if hasattr(self, 'identity_edit'):
            self.identity_edit.setText(data.get('identity', DEFAULT_IDENTITY))

        # ── 自动计算年龄和性别 ──
        self.on_id_input_finished()

        # ── 同步角色数据到数据模型，供 JSON 保存（统一人记录 schema） ──
        role = self.get_current_role_type()
        if role == "证人":
            name = data['name_pane']
            w = next((x for x in self.data_model.witnesses if x.get('name') == name), None)
            if w is None:
                w = {"role": "证人", "seq": witness_seq_label(len(self.data_model.witnesses) + 1)}
                self.data_model.witnesses.append(w)
            w.update(self._read_form_as_person())
            # 指向该证人，避免索引无效导致生成/回填拿不到证人数据
            self.data_model.current_witness_index = self.data_model.witnesses.index(w)
            # 同步扁平 证人* 键：本人/法人/家属分支走 _person_to_flat，证人需显式镜像，
            # 否则扁平键会停留在上一位证人（F2 轮换后生成笔录会拿到旧值）
            self._mirror_witness_to_flat(w)
        else:
            # 本人 / 法人 / 家属：表单 → 角色前缀扁平兼容键（统一走 _read_form_as_person）
            self._person_to_flat(role, self._read_form_as_person())
            # 家属(工亡)：以死者(injured_worker)为本人姓名，供案号/文案指向死者
            if role == "家属" and data.get('injured_worker'):
                self.set_data('本人姓名', data['injured_worker'], 'basic')

        # ── 右侧面板（同案沿用） ──
        current_worker = data.get("injured_worker", "")
        prev_worker = getattr(self, '_prev_injured_worker', None)
        is_same_case = (prev_worker is not None and current_worker == prev_worker)
        if not is_same_case:
            self.current_case_id = ""  # 换了受伤职工 → 视为新案件，重置案本号关联
            self.set_data('案本号', '', 'case')  # 同时清掉数据模型里缓存的旧案本号
            self.lineEdit_2.clear()  # 测试轮换时清掉界面案本号，点击后重新生成

        # 案件陈述：原「案件申请陈述」框已停用，预设里的陈述直接写数据模型，
        # 仍供文书/提示词的 {{受伤经过}} 使用
        self.set_data('受伤经过', data.get("statement_edit", ""), 'investigation')
        if is_same_case:
            print(f"📋 同案沿用案件陈述（{current_worker}）")

        # 材料：只有本人的证据有效（证人/法人的证据不保存）
        if role == "本人":
            mats = data.get('materials', [])
            if hasattr(self, 'material_list'):
                self.material_list.set_materials(mats)
            self.data_model.investigation['本人材料'] = mats
        else:
            # 证人/法人：恢复显示本人证据，不显示证人/法人证据
            if hasattr(self, 'material_list'):
                self.material_list.set_materials(self.data_model.investigation.get('本人材料', []))

        self._prev_injured_worker = current_worker

        # ── 重置案件状态，允许重新生成笔录 ──
        self.pushButton.setEnabled(True)
        self.pushButton.setStyleSheet("")
        self.current_case_folder = None
        self.current_person_name = ""

        # ── 状态提示 ──
        label = (f"[测试 {self._test_data_index + 1}/{len(TEST_DATA_PRESETS)}] "
                 f"{data['name']}  |  F2=下一个")
        self._set_status(label, 'green')
        print(f"OK 测试数据已填充: {data['name']}")

    def on_role_changed(self):
        """当角色切换时调用"""
        role = self.get_current_role_type()
        print(f"🔄 角色切换: {role}")
        # 共享“身份”输入行的标签/提示随角色变化（家属这一栏填的是与死者关系）
        # 标签槽是固定宽度（见 ui_main_build._SIZES 里的 identity_label 44px），
        # 所以不再按文本长度调宽度，
        # 否则切到长标签的角色会把左边的岗位输入框压住。
        if hasattr(self, 'identity_label'):
            self.identity_label.setText(
                ROLE_IDENTITY_LABEL.get(role, ROLE_IDENTITY_LABEL["本人"]))
        if hasattr(self, 'identity_edit'):
            self.identity_edit.setToolTip(ROLE_IDENTITY_HINT.get(role, ""))
            # 家属这一栏无通用默认值，清掉占位符「职工」避免误读
            self.identity_edit.setPlaceholderText("" if role == "家属" else DEFAULT_IDENTITY)

    def _setup_paths(self):
        """统一使用PathUtils设置所有路径"""
        print("=" * 50)
        print("🔄 使用统一的PathUtils设置所有路径...")

        # 使用PathUtils获取路径（path_utils.get_xxx() 方法已确保目录存在）
        self.BASE_PATH = str(path_utils.get_storage_path())
        self.TEMPLATE_PATH = str(path_utils.get_template_path())
        self.CONFIG_PATH = str(path_utils.get_config_path(""))
        self.DATA_PATH = str(path_utils.get_data_path(""))

        # 获取模板子目录（path_utils 已确保目录存在）
        self.TALK_TEMPLATE_PATH = str(path_utils.get_talk_template_path())
        self.DOCUMENT_TEMPLATE_PATH = str(path_utils.get_document_template_path())

    def _update_services_paths(self):
        """更新所有服务的路径"""
        print("🔄 更新服务路径...")

        # 更新FileService
        if hasattr(self, 'file_service'):
            self.file_service.BASE_PATH = self.BASE_PATH
            print(f"✅ 更新FileService路径: {self.BASE_PATH}")


        # 更新数据模型
        if hasattr(self, 'data_model'):
            self.data_model.company_info['存储路径'] = self.BASE_PATH
            self.data_model.company_info['模板路径'] = self.TEMPLATE_PATH

        print("✅ 服务路径更新完成")

    def insert_selected_questions(self, dialog):
        """将选中的问题插入到笔录文档（问题清单归 dialog 自己管）"""
        try:
            # 获取选中的问题
            selected_questions = dialog.selected_questions()

            if not selected_questions:
                QMessageBox.warning(dialog, "提示", "请至少选择一个要插入的问题")
                return

            print(f"✅ 选择了 {len(selected_questions)} 个问题准备插入")

            # 检查案件文件夹
            if not self.current_case_folder:
                QMessageBox.warning(dialog, "提示", "请先保存案件信息")
                return

            # 查找本人笔录文件
            person_name = self.get_data("本人姓名", "") or self.name_pane.text().strip()
            if not person_name:
                QMessageBox.warning(dialog, "提示", "请先输入受伤职工姓名")
                return

            # 查找主询问对象笔录（工亡案=家属，普通案=本人）
            person_files = self._main_transcript_candidates()
            if not person_files:
                QMessageBox.warning(dialog, "提示", "未找到可插入问题的谈话笔录文件")
                return

            # 使用第一个找到的主询问对象笔录
            file_path = os.path.join(self.current_case_folder, person_files[0])

            # 插入问题到文档
            success, message = documents.insert_questions_to_document(file_path, selected_questions)

            if success:
                QMessageBox.information(dialog, "成功",
                                        f"已成功插入 {len(selected_questions)} 个问题到笔录中\n\n"
                                        f"文件：{os.path.basename(file_path)}")
                dialog.close()
            else:
                QMessageBox.critical(dialog, "失败", f"插入失败：{message}")

        except Exception as e:
            logger.error(f"❌ 插入问题失败: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(dialog, "错误", f"插入过程中发生错误：{str(e)}")

    def on_pushButton_12_clicked(self):
        """谈话通知书按钮点击事件"""
        print("🔄 谈话通知书按钮被点击")

        # 调用谈话通知书生成函数
        self.generate_interview_notice_from_approval()

    def extract_data_from_approval_table(self, file_path):
        """从审批表 docx 提取数据，缺的字段用界面上的值兜底。

        读表格那半在 documents.read_approval_table（纯函数，可单测）；
        这里补的是「界面兜底」那半——它要用到公司下拉框和当前表单。
        """
        try:
            extracted_data = documents.read_approval_table(file_path)

            # 填充缺失字段
            current_date = date_now()

            if '申请时间' not in extracted_data:
                extracted_data['申请时间'] = current_date
                print(f"  🟥 使用默认值 申请时间: {current_date}")

            if '受理时间' not in extracted_data:
                extracted_data['受理时间'] = current_date
                print(f"  🟥 使用默认值 受理时间: {current_date}")

            if '医疗证明' not in extracted_data:
                extracted_data['医疗证明'] = '详见医疗诊断证明'
                print("  🟥 使用默认值 医疗证明: 详见医疗诊断证明")

            # 其他字段使用界面数据
            if '用人单位' not in extracted_data:
                extracted_data['用人单位'] = self.company_pane.currentText().strip() or '未知公司'

            if '职工姓名' not in extracted_data:
                extracted_data['职工姓名'] = self.get_data('本人姓名', '') or self.name_pane.text().strip() or '未知'

            if '职工身份证号' not in extracted_data:
                extracted_data['职工身份证号'] = self.get_data('本人身份证号', '')

            return extracted_data

        except Exception as e:
            logger.error(f"❌ 提取审批表数据失败: {e}")
            import traceback
            traceback.print_exc()

            # 返回最小可用数据
            current_date = date_now()
            return {
                '用人单位': self.company_pane.currentText().strip() or '未知公司',
                '职工姓名': self.get_data('本人姓名', '') or self.name_pane.text().strip() or '未知',
                '职工身份证号': self.get_data('本人身份证号', ''),
                '申请时间': current_date,
                '受理时间': current_date,
                '受伤经过': '详见谈话笔录',
                '医疗证明': '详见医疗诊断证明'
            }

    def generate_interview_notice_from_approval(self):
        """从审批表生成接受谈话通知书"""
        try:
            # 1. 检查本人姓名
            person_name = self.get_data("本人姓名", "")
            if not person_name:
                person_name = self.name_pane.text().strip()
            if not person_name:
                self._set_status('请先输入本人姓名', 'red')
                return

            # 2. 检查案件文件夹
            if not self.current_case_folder or not os.path.exists(self.current_case_folder):
                self._set_status('请先保存案件信息', 'red')
                return

            # 3. 查找本人案件审批表文件
            approval_file_name = f"{person_name}案件审批表.docx"
            approval_file_path = os.path.join(self.current_case_folder, approval_file_name)

            if not os.path.exists(approval_file_path):
                self._set_status(f'未找到审批表: {approval_file_name}', 'red')
                # 尝试查找其他可能的审批表文件
                all_files = os.listdir(self.current_case_folder)
                approval_files = [f for f in all_files if "审批表" in f and f.endswith('.docx')]

                if not approval_files:
                    self._set_status('案件文件夹中没有审批表文件', 'red')
                    return

                # 使用找到的第一个审批表文件
                approval_file_path = os.path.join(self.current_case_folder, approval_files[0])
                logger.warning(f"⚠️ 使用替代审批表: {approval_files[0]}")

            print(f"✅ 找到审批表文件: {approval_file_path}")

            # 4. 从审批表提取数据
            self._set_status('正在提取审批表数据...', 'black')
            QApplication.processEvents()

            extracted_data = self.extract_data_from_approval_table(approval_file_path)

            # 审批表里手填的日期常是 8 位紧凑格式，补成中文日期
            for _field in ('申请时间', '受理时间'):
                if _field in extracted_data:
                    extracted_data[_field] = documents.normalize_compact_date(
                        extracted_data[_field])

            if not extracted_data:
                self._set_status('提取审批表数据失败', 'red')
                return

            # 检查必要字段
            required_fields = ['用人单位', '职工姓名', '职工身份证号', '申请时间', '受理时间', '受伤经过', '医疗证明']
            missing_required = []

            for field in required_fields:
                if field not in extracted_data or not extracted_data[field]:
                    missing_required.append(field)

            if missing_required:
                self._set_status(f'审批表缺少必要字段: {missing_required}', 'red')
                return

            self._set_status('审批表数据提取成功', 'green')

            # 5. 准备模板数据
            current_date = date_now()

            # 使用docxtpl的RichText来设置红色
            from docxtpl import RichText

            template_data = {
                '用人单位': extracted_data.get('用人单位', self.company_pane.currentText().strip()),
                '本人姓名': extracted_data.get('职工姓名', self.get_data('本人姓名', '')),
                '职工性别': extracted_data.get('职工性别', self.get_data('本人性别', '')),
                '本人身份证号': extracted_data.get('职工身份证号', self.get_data('本人身份证号', '')),
                '受伤经过': extracted_data.get('受伤经过', '详见谈话笔录'),
                '当前时期': current_date,
                '当前日期': current_date,
                '当前时间': time_now(),
                '案本号': self.get_data('案本号', '')
            }

            # 申请时间：如果有就用，没有就用红色的当前日期
            if '申请时间' in extracted_data and extracted_data['申请时间']:
                template_data['申请时间'] = extracted_data['申请时间']
            else:
                rt = RichText()
                rt.add(current_date, color='FF0000')  # 红色
                template_data['申请时间'] = rt

            # 受理时间：如果有就用，没有就用红色的当前日期
            if '受理时间' in extracted_data and extracted_data['受理时间']:
                template_data['受理时间'] = extracted_data['受理时间']
            else:
                rt = RichText()
                rt.add(current_date, color='FF0000')  # 红色
                template_data['受理时间'] = rt

            # 医疗证明：如果有就用，没有就用红色的"详见医疗诊断证明"
            if '医疗证明' in extracted_data and extracted_data['医疗证明']:
                template_data['医疗证明'] = extracted_data['医疗证明']
            else:
                rt = RichText()
                rt.add('详见医疗诊断证明', color='FF0000')  # 红色
                template_data['医疗证明'] = rt

            # ============ 关键修复：在这里检查模板文件并定义 template_path ============
            # 6. 检查模板文件
            template_path = str(path_utils.get_document_template_path('接受谈话通知书（样本）.docx'))

            # 打印哪些字段用了红色
            red_fields = []
            if '申请时间' not in extracted_data or not extracted_data['申请时间']:
                red_fields.append('申请时间')
            if '受理时间' not in extracted_data or not extracted_data['受理时间']:
                red_fields.append('受理时间')
            if '医疗证明' not in extracted_data or not extracted_data['医疗证明']:
                red_fields.append('医疗证明')

            if red_fields:
                print(f"🔴 以下字段使用红色: {red_fields}")

            # 6. 生成文档
            self._set_status('正在生成谈话通知书...', 'black')
            QApplication.processEvents()

            try:
                from docxtpl import DocxTemplate
                word = DocxTemplate(template_path)
                word.render(template_data)

                notice_file_name = f"{person_name}接受谈话通知书.docx"
                target_path = os.path.join(self.current_case_folder, notice_file_name)
                word.save(target_path)

                print(f"✅ 谈话通知书保存到: {target_path}")

                # 打开文件
                success, message = self.file_service.open_document(target_path)
                if success:
                    self._set_status('谈话通知书生成成功', 'green')
                else:
                    self._set_status(f'谈话通知书生成成功，但打开失败: {message}', 'orange')

            except Exception as e:
                logger.error(f"❌ 生成谈话通知书失败: {e}")
                import traceback
                traceback.print_exc()
                self._set_status(f'生成谈话通知书失败: {str(e)}', 'red')

        except Exception as e:
            logger.error(f"❌ 谈话通知书过程异常: {e}")
            import traceback
            traceback.print_exc()
            self._set_status(f'生成谈话通知书异常: {str(e)}', 'red')

    def _cleanup_resources(self):
        """统一清理所有临时资源（对话框、线程等）"""
        resources = ['wait_dialog', 'progress_dialog', 'ai_worker']

        for attr_name in resources:
            if hasattr(self, attr_name):
                try:
                    resource = getattr(self, attr_name)
                    if attr_name == 'ai_worker' and resource.isRunning():
                        resource.terminate()  # 改为terminate
                        resource.wait(5000)  # 等待5秒
                    elif hasattr(resource, 'close'):
                        resource.close()
                except Exception as e:
                    print(f"清理资源 {attr_name} 失败: {e}")
    def _set_status(self, text, color="black", label="label_14"):
        """设置状态标签"""
        label_widget = getattr(self, label, None)
        if not label_widget:
            return

        label_widget.setText(text)
        if color == "green":
            label_widget.setStyleSheet("QLabel{color:green;}")
        elif color == "red":
            label_widget.setStyleSheet("QLabel{color:red;}")
        elif color == "orange":
            label_widget.setStyleSheet("QLabel{color:orange;}")
        else:
            label_widget.setStyleSheet("QLabel{color:black;}")

    def _handle_ai_result(self, result=None, error=None, canceled=False):
        """
        统一处理AI操作结果
        """
        print(f"🔄 处理AI结果: result={result is not None}, error={error}, canceled={canceled}")

        # 添加简单的防重复
        if hasattr(self, '_is_handling_ai_result') and self._is_handling_ai_result:
            logger.warning("⚠️ 已经在处理AI结果，跳过重复调用")
            return

        self._is_handling_ai_result = True

        try:
            # 清理资源
            self._cleanup_resources()

            if canceled:
                print("⏹️ AI操作被用户取消")
                return

            if error:
                logger.error(f"❌ AI操作出错: {error}")
                QMessageBox.critical(self, "AI审查错误", f"操作失败: {error}")
                return

            if result:
                print("✅ AI操作成功，显示结果")
                self.show_ai_review_result(result)
        except Exception as e:
            logger.error(f"❌ 处理AI结果时出错: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 清理标志
            if hasattr(self, '_is_handling_ai_result'):
                delattr(self, '_is_handling_ai_result')

    def _resolve_date_input(self, raw_value: str) -> str:
        """把输入框内容解析为日期字符串；为空时返回系统当前日期（用于 申请/受理时间）"""
        raw = (raw_value or "").strip()
        if not raw:
            return date_now()
        # 8位纯数字 → YYYY年MM月DD日
        if raw.isdigit() and len(raw) == 8:
            return f"{raw[0:4]}年{raw[4:6]}月{raw[6:8]}日"
        return raw

    def _normalize_compact_time(self, raw_value: str) -> str:
        """受伤/就诊时间归一为 年月日时分 纯数字 YYYYMMDDHHMM；留空返回 ''（不默认当前）"""
        raw = (raw_value or "").strip()
        if not raw:
            return ""
        return "".join(ch for ch in raw if ch.isdigit())

    def _save_date_inputs(self):
        """保存 申请/受理/受伤/就诊 时间到数据模型（申请/受理留空用当前；受伤/就诊留空不填）"""
        if not hasattr(self, 'apply_time_edit') or not hasattr(self, 'accept_time_edit'):
            return
        self.set_data('申请时间', self._resolve_date_input(self.apply_time_edit.text()), 'case')
        self.set_data('受理时间', self._resolve_date_input(self.accept_time_edit.text()), 'case')
        if hasattr(self, 'injury_time_edit'):
            self.set_data('受伤时间', self._normalize_compact_time(self.injury_time_edit.text()), 'case')
        if hasattr(self, 'visit_time_edit'):
            self.set_data('就诊时间', self._normalize_compact_time(self.visit_time_edit.text()), 'case')

    def _load_saved_user_config(self):
        """加载已保存的用户配置到UI"""
        remembered = self.user_manager.get_remembered_user()
        if remembered:
            username = remembered.get("username", "")
            api_key = remembered.get("api_key", "")
            # 填充用户下拉列表
            user_list = self.user_manager.get_user_list()
            self.api_user_combo.addItems(user_list)
            if username:
                idx = self.api_user_combo.findText(username)
                if idx >= 0:
                    self.api_user_combo.setCurrentIndex(idx)
                else:
                    self.api_user_combo.setCurrentText(username)
            if api_key:
                self.api_key_input.setText(api_key)
            print(f"✅ 已加载记住的用户: {username}")
        else:
            # 至少填充用户列表
            user_list = self.user_manager.get_user_list()
            self.api_user_combo.addItems(user_list)
            print("ℹ️ 没有记住的用户")

    def _get_current_username(self) -> str:
        """获取当前用户名"""
        if hasattr(self, 'api_user_combo'):
            return self.api_user_combo.currentText().strip()
        return "未登录用户"

    def _toggle_config_panel(self):
        """展开/收起用户配置面板"""
        visible = self.api_group.isVisible()
        if visible:
            self.api_group.hide()
        else:
            self.api_group.show()
            self.api_group.raise_()  # 置顶，避免被“案件申请陈述”等面板遮挡
        arrow = "▼" if visible else "⚙"
        self.config_toggle_btn.setText(arrow)

    def _update_api_status(self):
        """更新顶栏状态文字（配置面板内不再显示状态/图标）"""
        if not hasattr(self, 'top_status_label'):
            return
        if self.ai_service:
            self.top_status_label.setText("AI 已就绪")
            self.top_status_label.setStyleSheet("color: green; background: transparent; border: none;")
        else:
            username = self._get_current_username()
            if not username or username == "未登录用户":
                msg = "请配置用户名和API密钥"
            else:
                msg = "API密钥未配置，AI 功能不可用"
            self.top_status_label.setText(msg)
            self.top_status_label.setStyleSheet("color: orange; background: transparent; border: none;")

    def _on_api_edited(self):
        """用户/密钥编辑完成后自动保存（无需“保存”按钮）；仅输入改变时才会触发。"""
        username = self.api_user_combo.currentText().strip()
        api_key = self.api_key_input.text().strip()
        if not username:
            return
        try:
            self.user_manager.save_user_config(
                username=username,
                api_url="https://api.deepseek.com",
                api_key=api_key,
                remember_me=True,   # 默认记住；启动自动使用
                service="DeepSeek",
            )
        except Exception as e:
            logger.warning(f"⚠️ 自动保存失败: {e}")
            return
        if self.api_user_combo.findText(username) < 0:
            self.api_user_combo.addItem(username)
        self.data_model.output_config['用户名'] = username
        self.init_ai_service()
        print(f"✅ API配置已自动保存: 用户={username}, 密钥={'已设置' if api_key else '未设置'}")

    def _on_user_combo_changed(self, text):
        """用户名下拉框变化时自动加载对应的API密钥（不触发保存）"""
        if not text or not text.strip():
            return
        username = text.strip()
        user_data = self.user_manager.users_data.get('users', {}).get(username, {})
        if user_data:
            api_key = user_data.get('api_key', '')
            self.api_key_input.setText(api_key)
            if api_key:
                print(f"✅ 已加载用户 '{username}' 的API配置")
            else:
                print(f"ℹ️ 用户 '{username}' 未配置API密钥")

    def init_ai_service(self):
        """初始化AI服务 - 从UserManager读取配置"""
        try:
            # 从UserManager获取当前用户的API配置
            username = self._get_current_username()
            if not username:
                logger.warning("⚠️ 未找到用户配置，AI功能将不可用")
                self.ai_service = None
                self._update_api_status()
                return

            user_data = self.user_manager.users_data.get('users', {}).get(username, {})
            api_key = user_data.get('api_key', '')
            api_url = user_data.get('api_url', 'https://api.deepseek.com')

            # 检查配置是否完整
            if not api_key or not api_url:
                logger.warning("⚠️ API配置不完整，AI功能将不可用")
                print(f"  API地址: {api_url if api_url else '未设置'}")
                print(f"  API密钥: {'已设置' if api_key else '未设置'}")
                self.ai_service = None
                self._update_api_status()
                return

            print(f"✅ 使用用户 '{username}' 的API配置初始化AI服务")
            print(f"  API地址: {api_url}")
            print(f"  API密钥前8位: {api_key[:8]}...")

            # 创建AI服务实例
            self.ai_service = AIService(api_key, api_url)
            print("✅ AI服务初始化成功")
            self._update_api_status()

        except Exception as e:
            logger.error(f"❌ AI服务初始化失败: {e}")
            import traceback
            traceback.print_exc()
            self.ai_service = None
            self._update_api_status()

    def _main_transcript_candidates(self) -> list:
        """案件目录下可作为主询问对象笔录的文件：优先 家属(工亡案)，其次 本人；
        均无则退回第一份非 审批表/告知书/通知书 的谈话笔录。"""
        if not self.current_case_folder or not os.path.isdir(self.current_case_folder):
            return []
        cands, family, main_self = [], [], []
        for file in sorted(os.listdir(self.current_case_folder)):
            if not file.endswith('.docx'):
                continue
            if any(k in file for k in ("审批表", "告知书", "通知书")):
                continue
            cands.append(file)
            if "家属" in file:
                family.append(file)
            elif "本人" in file:
                main_self.append(file)
        return (family or main_self or cands[:1])

    def ai_review_document(self):
        """AI审查文档"""
        try:
            print("=" * 50)
            print("🔄 AI审查开始执行...")

            # 检查AI服务
            if not self.ai_service:
                logger.error("❌ AI服务未初始化")
                QMessageBox.warning(self, "AI审查", "请先配置API密钥。")
                self.init_ai_service()
                return

            print("✅ AI服务检查通过")

            # 检查案件文件夹
            if not self.current_case_folder:
                logger.error("❌ 当前案件文件夹为空")
                QMessageBox.warning(self, "AI审查", "请先保存案件信息。")
                return

            print(f"📁 案件文件夹: {self.current_case_folder}")

            # 查找主询问对象笔录（工亡案=家属，普通案=本人）
            person_files = self._main_transcript_candidates()
            if not person_files:
                logger.error("❌ 未找到可审查的谈话笔录文件")
                QMessageBox.warning(self, "AI审查", "未找到可审查的谈话笔录文件。")
                return

            print(f"✅ 找到{len(person_files)}个谈话笔录文件")

            # 使用第一个找到的主询问对象笔录
            file_path = os.path.join(self.current_case_folder, person_files[0])
            print(f"📄 使用文件路径: {file_path}")

            # 检查文件是否存在
            if not os.path.exists(file_path):
                logger.error(f"❌ 文件不存在")
                QMessageBox.warning(self, "AI审查", f"文件不存在: {file_path}")
                return

            print("✅ 文件存在")

            # ======================
            # 在这里创建进度对话框和AI工作线程
            # ======================

            # 创建进度对话框
            self.progress_dialog = QProgressDialog("正在分析文档...", "取消", 0, 100, self)
            self.progress_dialog.setWindowTitle("AI审查")
            self.progress_dialog.setWindowModality(Qt.WindowModal)

            # 创建AI工作线程（现在file_path已经定义）
            self.ai_worker = AIWorker(self.ai_service, file_path)

            # 连接信号到统一处理方法
            self.ai_worker.finished.connect(
                lambda result: self._handle_ai_result(result=result)
            )
            self.ai_worker.error.connect(
                lambda error: self._handle_ai_result(error=error)
            )
            self.ai_worker.progress.connect(
                lambda msg, value: self.progress_dialog.setLabelText(f"{msg} ({value}%)")
            )

            # 进度对话框取消
            self.progress_dialog.canceled.connect(
                lambda: self._handle_ai_result(canceled=True)
            )

            # 显示进度对话框并启动线程
            self.progress_dialog.show()
            self.ai_worker.start()

        except Exception as e:
            print(f"🔥 整体审查过程异常: {str(e)}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "AI审查错误", f"审查失败: {str(e)}")

    def show_ai_review_result(self, review_result):
        """显示AI审查结果 —— 解析后交给 AIReviewResultDialog（带问题勾选）"""
        if isinstance(review_result, str):
            result_text = review_result
        elif isinstance(review_result, dict):
            for key in ("结果", "原始回复"):
                if key in review_result:
                    result_text = review_result[key]
                    break
            else:
                result_text = (f"错误: {review_result['错误信息']}"
                               if "错误信息" in review_result else str(review_result))
        else:
            result_text = str(review_result)

        parsed_result = parse_ai_result(result_text)

        # 对话框只管显示与勾选，三个动作（插入 / 复制 / 保存）回抛到这里
        dialog = AIReviewResultDialog(parsed_result, self)
        dialog.insertRequested.connect(lambda: self.insert_selected_questions(dialog))
        dialog.copyRequested.connect(self.copy_to_clipboard)
        dialog.saveRequested.connect(self.save_ai_report)
        dialog.exec_()

    def copy_to_clipboard(self, text, parent=None):
        """复制文本到剪贴板"""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        QMessageBox.information(parent or self, "复制成功", "已复制到剪贴板")

    def _copy_statement(self):
        """复制案件申请陈述"""
        text = self.statement_edit.toPlainText().strip()
        if text:
            self.copy_to_clipboard(text)
        else:
            QMessageBox.information(self, "提示", "案件申请陈述为空")

    def _copy_material(self):
        """复制材料分类"""
        if hasattr(self, 'material_list'):
            self.material_list.copy_to_clipboard()
        QMessageBox.information(self, "提示", "材料列表已复制到剪贴板")

    def save_ai_report(self, parsed_result):
        """保存AI审查报告"""
        if not self.current_case_folder:
            QMessageBox.warning(self, "提示", "请先保存案件信息")
            return

        person_name = self.get_data('本人姓名', '未知')
        timestamp = timestamp_now()
        filename = f"{person_name}_AI审查报告_{timestamp}.txt"
        filepath = os.path.join(self.current_case_folder, filename)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("=" * 60 + "\n")
                f.write("AI法律审查报告\n")
                f.write("=" * 60 + "\n\n")
                f.write(f"审查时间：{datetime.datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}\n")
                f.write(f"审查对象：{person_name}\n\n")

                f.write("【审查结果】\n")
                f.write(parsed_result.get("审查结果", "无审查结果") + "\n\n")

                if parsed_result.get("缺失问题"):
                    f.write("【缺失问题列表】\n")
                    for i, question in enumerate(parsed_result["缺失问题"], 1):
                        f.write(f"{i}. {question}\n")

            QMessageBox.information(self, "保存成功", f"报告已保存为：\n{filename}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存失败：{str(e)}")

    def generate_injury_notice(self):
        """生成工伤告知书：按案本号找目录 → 检查审批表 → 按JSON结论选模板渲染"""
        try:
            # 1. 读案本号
            case_number = self.lineEdit_2.text().strip()
            if not case_number:
                self._set_status('未找到案本号', 'red')
                QMessageBox.warning(self, "提示", "请先输入或生成案本号")
                return

            # 2. 用案本号找案件文件夹（年份目录 + 老布局都认）
            case_folder = self._case_dir(case_number)
            if not os.path.exists(case_folder):
                self._set_status('未找到案件目录', 'red')
                QMessageBox.warning(self, "提示", f"未找到案本号对应的案件目录：\n{case_folder}")
                return

            # 3. 检查审批表文件
            approval_files = [f for f in os.listdir(case_folder)
                              if f.endswith('.docx') and '审批表' in f]
            if not approval_files:
                self._set_status('目录下没有审批表文件，请先生成案件审批表', 'red')
                QMessageBox.warning(self, "提示", "目录下没有审批表文件，请先生成案件审批表。")
                return

            # 多个审批表：弹窗选择
            selected_file = approval_files[0]
            if len(approval_files) > 1:
                choice, ok = QInputDialog.getItem(self, "选择审批表",
                                                  "目录下有多个审批表，请选择：",
                                                  approval_files, 0, False)
                if not ok or not choice:
                    self._set_status('已取消', 'black')
                    return
                selected_file = choice
            print(f"✅ 使用审批表: {selected_file}")

            # 4. 用 JSON 数据生成字典
            case_obj = self._load_cases_data().get(case_number)
            if not case_obj:
                self._set_status('未找到该案本号的案件数据', 'red')
                QMessageBox.warning(self, "提示", f"未在数据中找到案本号：{case_number}")
                return
            template_data = documents.build_notice_template_data(case_obj)

            # 5. 按 JSON 结论选模板
            conclusion = case_obj.get('conclusion', '')
            template_name = documents.notice_template_name(conclusion)
            template_path = str(path_utils.get_document_template_path(template_name))
            if not os.path.exists(template_path):
                self._set_status(f'模板文件不存在: {template_name}', 'red')
                QMessageBox.critical(self, "错误", f"找不到模板文件:\n{template_path}")
                return

            # 6. 渲染
            self._set_status('正在生成工伤告知书...', 'black')
            QApplication.processEvents()
            word = documents.render_template(template_path, template_data)

            # 7. 保存 + 打开
            name = case_obj.get('name', '') or '职工'
            notice_file_name = documents.notice_file_name(conclusion, name)
            target_path = os.path.join(case_folder, notice_file_name)
            word.save(target_path)
            print(f"✅ 工伤告知书保存到: {target_path}")

            success, message = self.file_service.open_document(target_path)
            if success:
                self._set_status('工伤告知书生成成功', 'green')
                QMessageBox.information(self, "成功", f"工伤告知书已生成:\n{notice_file_name}")
            else:
                self._set_status(f'工伤告知书生成成功，但打开失败: {message}', 'orange')
                QMessageBox.information(self, "成功",
                                        f"工伤告知书已生成:\n{notice_file_name}\n\n但打开失败: {message}")

        except Exception as e:
            logger.error(f"❌ 生成工伤告知书失败: {e}")
            import traceback
            traceback.print_exc()
            self._set_status(f'生成工伤告知书失败: {str(e)}', 'red')
            QMessageBox.critical(self, "错误", f"生成工伤告知书失败:\n{str(e)}")

    def _apply_ui_settings(self):
        """应用UI设置"""
        try:
            ui_settings = self.config_service.get_ui_settings()

            # 设置字体
            font = QFont(ui_settings.font_family, ui_settings.font_size)
            self.setFont(font)

            # 注意：窗口大小由 MainWindowUI._apply_window_size 固定为 870×850，
            # 此处不再覆盖
        except Exception as e:
            logger.warning(f"⚠️ 应用UI设置失败: {e}")

    # ========================================================================
    # 统一"人记录"助手（表单 ↔ 英文 schema ↔ 中文兼容扁平键）
    # ========================================================================

    def _read_form_as_person(self) -> Dict[str, Any]:
        """把共享表单控件读成统一人记录（英文 schema，不含 role）

        unit 取「用人单位」控件：本人角色下即案件级用人单位，其余角色下是
        该人自己的工作单位（证人/家属不必与案件用人单位相同）。
        """
        return {
            'name': self.name_pane.text().strip(),
            'gender': self.lineEdit.text().strip(),
            'age': self.age_pane.text().strip(),
            'id_card': self.idnumer_pane.text().strip(),
            'address': self.textEdit.toPlainText().strip(),
            'phone': self.lineEdit_4.text().strip(),
            'position': self.lineEdit_5.text().strip(),
            'identity': (self.identity_edit.text().strip()
                         if hasattr(self, 'identity_edit') else ''),
            'unit': self.company_pane.currentText().strip(),
        }

    def _write_person_to_form(self, person: Dict[str, Any]):
        """把统一人记录（英文 schema）回填共享表单控件（不写数据模型）"""
        def _txt(person, key):
            v = person.get(key)
            return "" if v is None else str(v)

        self.name_pane.setText(_txt(person, 'name'))
        self.lineEdit.setText(_txt(person, 'gender'))
        self.age_pane.setText(_txt(person, 'age'))
        self.idnumer_pane.setText(_txt(person, 'id_card'))
        self.textEdit.setPlainText(_txt(person, 'address'))
        self.lineEdit_4.setText(_txt(person, 'phone'))
        self.lineEdit_5.setText(_txt(person, 'position'))
        if hasattr(self, 'identity_edit'):
            self.identity_edit.setText(_txt(person, 'identity'))
        if hasattr(self, 'company_pane'):
            self._set_combo_or_type(self.company_pane, _txt(person, 'unit'))

    def _person_to_flat(self, role: str, person: Dict[str, Any]):
        """统一人记录 → 兼容扁平中文键（本人姓名/证人姓名/…，经 set_data 入 basic_info）

        空值也要写。表单是该角色数据的唯一来源，跳过空值会让"清空输入框"传不进来，
        旧值残留在扁平键里、下次保存照样写进笔录与文书。
        """
        for field in PERSON_BASE_FIELDS:
            self.set_data(person_flat_key(role, field), person.get(field) or '', 'basic')

    def _person_from_flat(self, role: str) -> Dict[str, Any]:
        """兼容扁平中文键 → 统一人记录（英文 schema，字符串；缺值置空）"""
        return {
            field: str(self.get_data(person_flat_key(role, field), '') or '')
            for field in PERSON_BASE_FIELDS
        }

    def _restore_role_unit(self):
        """把「用人单位」控件回填成*当前角色自己的*单位。

        本人 → 案件级用人单位；证人/法人/家属 → 该人记录里的 unit。
        该控件是共享的，若不用角色自己的值回填，换角色后会串数据。
        """
        if not hasattr(self, 'company_pane'):
            return
        role = self.get_current_role_type()
        if role == "本人":
            self._set_combo_or_type(self.company_pane, self.get_data('用人单位', ''))
        else:
            self._set_combo_or_type(
                self.company_pane, self.get_data(person_flat_key(role, 'unit'), ''))

    def clear_role_fields(self):
        """
        清空当前角色的字段
        """
        role = self.get_current_role_type()

        print(f"🧹 清空{role}字段")

        # 清空输入控件
        self.clear_fields()

        # 清空该角色的数据模型与模板缓存，避免多名证人/人员数据串用
        self._clear_role_data(role)

        # 当角色切换时，更新按钮状态
        self.on_role_changed()

        # 「用人单位」控件按角色回填（本人＝案件级用人单位，跨角色保留；
        # 其余角色＝该人自己的工作单位，上面 _clear_role_data 已清空故为空白待录）
        self._restore_role_unit()

        # 切换回本人时清空案本号，输入新姓名后自动生成
        if role == "本人":
            self.lineEdit_2.clear()

        # 多证人：证人编号行常显（见 ui_main_build._create_witness_widgets），
        # 不再随角色隐藏。
        # 切到证人时加载当前证人；切走时先把表单写回当前证人。
        if role == "证人":
            self._show_witness_ui()
        else:
            self._sync_form_to_current_witness()

    def _clear_role_data(self, role: str):
        """清除指定角色在数据模型与模板字典中的所有数据，确保多人数据一一对应"""
        if role not in ("本人", "证人", "法人", "家属"):
            return
        self.data_model.clear_role_data(role)
        for key in [k for k in list(self._template_dict.keys()) if k.startswith(role)]:
            self._template_dict.pop(key, None)
        self.var_manager.clear_cache()
        # 常显（不再随角色隐藏），位置由 ui_main_build._row_unit_witness() 摆在
        # 单位性质那一行

    def _show_witness_ui(self):
        """切换到证人角色时显示下拉框，并加载当前/首位证人"""
        self.witness_label.show()
        self.witness_combo.show()
        self.add_witness_btn.show()

        if not self.data_model.witnesses:
            if self.witness_combo.count() == 0:
                self.witness_combo.blockSignals(True)
                self.witness_combo.addItem("（暂无证人）")
                self.witness_combo.blockSignals(False)
            return

        if self.data_model.current_witness_index < 0:
            self.data_model.current_witness_index = 0
        self._refresh_witness_combo()
        self._sync_current_witness_to_form()


    def _current_witness(self) -> Optional[Dict[str, Any]]:
        idx = self.data_model.current_witness_index
        if 0 <= idx < len(self.data_model.witnesses):
            return self.data_model.witnesses[idx]
        return None

    def _refresh_witness_combo(self):
        """重建下拉框内容（阻塞信号，避免触发切换逻辑）"""
        self.witness_combo.blockSignals(True)
        self.witness_combo.clear()
        for w in self.data_model.witnesses:
            name = w.get("name") or "未命名"
            self.witness_combo.addItem(f"{w.get('seq', '')} · {name}")
        if 0 <= self.data_model.current_witness_index < self.witness_combo.count():
            self.witness_combo.setCurrentIndex(self.data_model.current_witness_index)
        self.witness_combo.blockSignals(False)

    def _mirror_witness_to_flat(self, w: Dict[str, Any]):
        """把统一证人记录（英文 schema）同步到扁平 证人* 键（供模板/AI 兼容使用）

        空值同样要写，理由见 _person_to_flat：否则清空证人某个字段传不进去。
        """
        for field in PERSON_BASE_FIELDS:
            self.set_data(person_flat_key("证人", field), w.get(field) or '', 'basic')

    def _sync_form_to_current_witness(self):
        """把表单内容写回当前证人，并同步扁平 证人* 键"""
        if self.get_current_role_type() != "证人":
            return
        w = self._current_witness()
        if w is None:
            return

        w.update(self._read_form_as_person())

        self._mirror_witness_to_flat(w)

        # 更新下拉框当前项的显示（不重建，避免选中状态被打乱）
        idx = self.data_model.current_witness_index
        if 0 <= idx < self.witness_combo.count():
            self.witness_combo.blockSignals(True)
            self.witness_combo.setItemText(idx, f"{w.get('seq', '')} · {w.get('name') or '未命名'}")
            self.witness_combo.blockSignals(False)

        self.var_manager.clear_cache()

    def _sync_current_witness_to_form(self):
        """把当前证人数据填入表单 + 扁平 证人* 键"""
        w = self._current_witness()
        if w is None:
            return
        self._write_person_to_form(w)

        self._mirror_witness_to_flat(w)
        self.var_manager.clear_cache()

    def _ensure_current_witness(self):
        """生成证人笔录前调用：确保 current_witness_index 指向一个有效证人"""
        if self.get_current_role_type() != "证人":
            return
        if 0 <= self.data_model.current_witness_index < len(self.data_model.witnesses):
            return
        if self.data_model.witnesses:
            # 已有证人但索引无效（如 F2 填充后未设索引）→ 指向第一个
            self.data_model.current_witness_index = 0
            self._refresh_witness_combo()
            return
        new_index = len(self.data_model.witnesses)
        new_witness = {
            "role": "证人",
            "seq": witness_seq_label(new_index + 1),
            "name": "", "gender": "", "age": "",
            "id_card": "", "address": "", "phone": "", "position": "",
            "identity": "",
        }
        self.data_model.witnesses.append(new_witness)
        self.data_model.current_witness_index = new_index
        self._refresh_witness_combo()

    def _add_witness(self):
        """添加一个新证人，自动编号为 证人一/证人二/…"""
        self._sync_form_to_current_witness()  # 先保存当前证人的编辑

        new_index = len(self.data_model.witnesses)
        new_witness = {
            "role": "证人",
            "seq": witness_seq_label(new_index + 1),
            "name": "", "gender": "", "age": "",
            "id_card": "", "address": "", "phone": "", "position": "",
            "identity": "",
        }
        self.data_model.witnesses.append(new_witness)
        self.data_model.current_witness_index = new_index

        self._refresh_witness_combo()
        self._sync_current_witness_to_form()  # 清空表单，准备录入新证人
        self._save_witnesses()

    def _on_witness_selected(self, index):
        """切换选中的证人"""
        if index < 0 or index >= len(self.data_model.witnesses):
            return
        if self.data_model.current_witness_index != index:
            self._sync_form_to_current_witness()  # 保存上一个证人
        self.data_model.current_witness_index = index
        self._sync_current_witness_to_form()

    def _save_witnesses(self):
        """把证人数据写回 cases_data.json 的当前案件（单一数据源，不再单独存证人信息.json）"""
        case_id = (self.current_case_id or '').strip() or self.lineEdit_2.text().strip()
        if not case_id:
            return
        try:
            cases = self._load_cases_data()
            case_obj = cases.get(case_id)
            if case_obj is None:
                return
            case_obj['witnesses'] = list(self.data_model.witnesses)
            self._save_cases_data(cases)
            print(f"✅ 证人信息已保存到案件数据: {len(self.data_model.witnesses)} 位证人")
        except Exception as e:
            print(f"保存证人信息失败: {e}")

    def setup_logging(self):
        """挂上统一日志与全局异常兜底，并暴露 self.log_warning / self.log_error。

        日志落在 数据目录/logs/ 下（程序目录不可写时自动退回临时目录）。

        正常入口 `main.py` 在创建本窗口**之前**就配过一次（为了把窗口构建期间的
        异常也记下来），所以走到这里时 `setup_logging` 已经是空操作——它内部有
        `_configured` 守卫，只生效一次，**先调用者的路径参数生效、后来者被忽略**。
        留着这一次是为了兼容 `python app_main.py` 直接启动（那时没人先配）；
        `install_excepthook` / `install_qt_message_handler` 本身幂等，重复调用无副作用。
        """
        base = getattr(self, 'DATA_PATH', '') or str(path_utils.get_data_path(""))
        log_utils.setup_logging(os.path.join(base, "logs"))
        log_utils.install_excepthook()
        log_utils.install_qt_message_handler()
        self.log_warning = lambda msg: logger.warning("%s", msg)
        self.log_error = lambda msg: logger.error("%s", msg)

    def on_case_type_changed(self):
        """当案件类型选择改变时调用"""
        is_death_case = self.death_case_checkbox.isChecked()
        is_personal = self.personal_application_checkbox.isChecked()

        self.set_data('案件性质', "工亡案件" if is_death_case else "工伤案件", 'case')
        self.set_data('申请类型', "个人申请" if is_personal else "单位申请", 'case')
        self.update_case_type_hint()

    def update_case_type_hint(self):
        """更新案件类型提示信息"""
        is_death_case = self.death_case_checkbox.isChecked()
        is_personal = self.personal_application_checkbox.isChecked()

        hint_text = "当前案件类型: "
        if is_death_case and is_personal:
            hint_text += "个人申请的工亡案件"
        elif is_death_case:
            hint_text += "单位申请的工亡案件"
        elif is_personal:
            hint_text += "个人申请的工伤案件"
        else:
            hint_text += "单位申请的工伤案件"

        hint_label = self.findChild(QLabel, "labelCaseHint")
        if hint_label:
            hint_label.setText(hint_text)

    def init_combobox_data(self):
        """初始化组合框数据（简化版）"""
        try:
            # 使用 path_utils 的数据路径
            from path_utils import path_utils

            doc_template_dir = path_utils.get_document_template_path("")
            print(f"🔍 文书模板目录: {doc_template_dir}")

            # 用工单位 - 使用文书模板目录
            company_file = str(path_utils.get_document_template_path('用工单位汇总.xlsx'))
            print(f"🔍 用工单位文件: {company_file}")

            if os.path.exists(company_file):
                try:
                    file = pd.read_excel(company_file)
                    self.items_list = file['用工单位汇总'].tolist()
                    print(f"✅ 加载用工单位: {len(self.items_list)}个")
                    if self.items_list:
                        print(f"   示例: {self.items_list[:3]}")
                except Exception as e:
                    logger.error(f"❌ 读取用工单位文件失败: {e}")
                    self.items_list = ['公司A', '公司B', '公司C']  # 默认数据
            else:
                logger.warning("⚠️ 用工单位文件不存在，创建默认文件")
                self.items_list = ['公司A', '公司B', '公司C']
                # 创建默认文件
                try:
                    df = pd.DataFrame(self.items_list, columns=['用工单位汇总'])
                    df.to_excel(company_file, index=False)
                    print(f"✅ 创建默认用工单位文件")
                except Exception as e:
                    logger.error(f"❌ 创建用工单位文件失败: {e}")

            # 用人单位 - 使用文书模板目录
            employer_file = str(path_utils.get_document_template_path('用人单位汇总.xlsx'))
            print(f"🔍 用人单位文件: {employer_file}")

            if os.path.exists(employer_file):
                try:
                    file1 = pd.read_excel(employer_file)
                    self.items_list1 = file1['用人单位汇总'].tolist()
                    print(f"✅ 加载用人单位: {len(self.items_list1)}个")
                except Exception as e:
                    logger.error(f"❌ 读取用人单位文件失败: {e}")
                    self.items_list1 = ['用人单位A', '用人单位B']
            else:
                logger.warning("⚠️ 用人单位文件不存在，创建默认文件")
                self.items_list1 = ['用人单位A', '用人单位B']
                try:
                    df = pd.DataFrame(self.items_list1, columns=['用人单位汇总'])
                    df.to_excel(employer_file, index=False)
                    print(f"✅ 创建默认用人单位文件")
                except Exception as e:
                    logger.error(f"❌ 创建用人单位文件失败: {e}")

            # 工地名称 - 使用文书模板目录
            site_file = str(path_utils.get_document_template_path('工地名称汇总.xlsx'))
            print(f"🔍 工地名称文件: {site_file}")

            if os.path.exists(site_file):
                try:
                    file2 = pd.read_excel(site_file)
                    self.items_list2 = file2['工地名称汇总'].tolist()
                    print(f"✅ 加载工地名称: {len(self.items_list2)}个")
                except Exception as e:
                    logger.error(f"❌ 读取工地名称文件失败: {e}")
                    self.items_list2 = ['工地A', '工地B']
            else:
                logger.warning("⚠️ 工地名称文件不存在，创建默认文件")
                self.items_list2 = ['工地A', '工地B']
                try:
                    df = pd.DataFrame(self.items_list2, columns=['工地名称汇总'])
                    df.to_excel(site_file, index=False)
                    print(f"✅ 创建默认工地名称文件")
                except Exception as e:
                    logger.error(f"❌ 创建工地名称文件失败: {e}")

        except Exception as e:
            logger.error(f"❌ 初始化组合框数据失败: {e}")
            import traceback
            traceback.print_exc()

            # 设置默认数据
            self.items_list = ['公司A', '公司B', '公司C']
            self.items_list1 = ['用人单位A', '用人单位B']
            self.items_list2 = ['工地A', '工地B']
            print("✅ 使用默认数据")

    def on_id_input_finished(self):
        """当身份证输入框完成编辑时自动计算年龄和性别"""
        try:
            role = self.get_current_role_type()
            idcard = self.idnumer_pane.text().strip()

            if not idcard or len(idcard) not in (15, 18):
                return

            self.set_data(f"{role}身份证号", idcard, 'basic')
            self.process_id_info(role)
            self.calculate_age_from_id(role)

        except Exception as e:
            import traceback
            traceback.print_exc()

    def save_company(self):
        """保存用人单位到Excel（company_pane 现为用人单位）"""
        new_item = self.company_pane.currentText().strip()
        if new_item and new_item not in self.items_list1:
            self.items_list1 = self.file_service.save_to_excel(
                '用人单位汇总.xlsx',
                '用人单位汇总',
                new_item,
                self.items_list1
            )
            self.init_combobox(self.company_pane, self.items_list1)
            print(f"💾 保存用人单位: {new_item}")

    def save_construction_company(self):
        """保存用工单位到Excel（construction_company 现为用工单位）"""
        new_item = self.construction_company.currentText().strip()
        if new_item and new_item not in self.items_list:
            self.items_list = self.file_service.save_to_excel(
                '用工单位汇总.xlsx',
                '用工单位汇总',
                new_item,
                self.items_list
            )
            self.init_combobox(self.construction_company, self.items_list)
            print(f"💾 保存用工单位: {new_item}")

    def save_construction_plant(self):
        """保存工地名称到Excel"""
        new_item = self.construction_plant.currentText().strip()
        if new_item and new_item not in self.items_list2:
            self.items_list2 = self.file_service.save_to_excel(
                '工地名称汇总.xlsx',
                '工地名称汇总',
                new_item,
                self.items_list2
            )
            self.init_combobox(self.construction_plant, self.items_list2)
            print(f"💾 保存工地名称: {new_item}")

    def init_combobox(self, combobox, items):
        """初始化组合框"""
        print(f"🔍 初始化 {combobox.objectName()}，数据长度: {len(items)}")

        combobox.clear()

        if items:
            for item in items:
                combobox.addItem(str(item))
            print(f"✅ 添加了 {len(items)} 个选项")
        else:
            logger.warning("⚠️ 没有数据可添加")
            combobox.addItem("暂无数据")

        combobox.setCurrentIndex(-1)  # 清空选择

        # 设置自动完成
        completer = QCompleter(items)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        combobox.setCompleter(completer)

        print(f"✅ {combobox.objectName()} 初始化完成，当前项数: {combobox.count()}")

    def calculate_age_from_id(self, role):
        """根据身份证号计算年龄（使用DataService）"""
        try:
            idcard = self.get_data(f"{role}身份证号", "")
            if not idcard:
                return

            # 使用DataService计算年龄
            age = self.data_service.calculate_age_from_idcard(idcard)
            if age is None:
                self._set_status('身份证号格式错误', 'red', 'label_12')
                return

            # 设置年龄数据
            self.set_data(f"{role}年龄", age, 'basic')
            self.age_pane.setText(str(age))

            # 超龄检查（仅对本人）
            if role == "本人":
                gender = self.get_data(f"{role}性别", "")
                if (gender == "男" and age > 60) or (gender == "女" and age > 50):
                    self._set_status('此人已经超龄', 'red', 'label_12')
                else:
                    self._set_status('', 'black', 'label_12')   # 常态留空，不写占位文字

        except Exception as e:
            print(f"[calculate_age_from_id] 错误: {e}")
            self._set_status('年龄计算错误', 'red', 'label_12')

    def get_data(self, key: str, default: Any = None) -> Any:
        """统一的数据访问方法"""
        model_value = self._get_from_data_model(key)
        if model_value is not None:
            if key not in self._template_dict or self._template_dict[key] != model_value:
                self._template_dict[key] = model_value
            return model_value

        dict_value = self._template_dict.get(key)
        if dict_value is not None:
            return dict_value

        return default

    def set_data(self, key: str, value: Any, category: str = "auto") -> None:
        """统一的数据设置方法"""
        if value is None:
            value = ""

        if category == "auto":
            category = self._detect_data_category(key)

        self._store_to_data_model(key, value, category)
        self._template_dict[key] = value
        self._handle_special_keys(key, value)

    def _handle_special_keys(self, key: str, value: Any):
        """处理特殊键的同步逻辑"""
        if key == "当前时期":
            if not value:
                current_date = date_now()
                current_time = time_now()
                self.set_data(key, f"{current_date}{current_time}", 'output')

    def _detect_data_category(self, key: str) -> str:
        """自动检测数据类别"""
        role_prefixes = ['本人', '证人', '法人', '家属']
        for prefix in role_prefixes:
            if key.startswith(prefix):
                base_key = key[len(prefix):]
                return self._detect_base_category(base_key)
        return self._detect_base_category(key)

    def _detect_base_category(self, key: str) -> str:
        """检测基础键名的类别"""
        if key in ['姓名', '年龄', '性别', '身份证号', '身份证地址', '手机号', '岗位', '职务', '身份']:
            return 'basic'
        elif key in ['用工单位', '用人单位', '工地名称']:
            return 'company'
        elif key in ['案件性质', '申请类型', '案本号', '案件类型', '单位性质']:
            return 'case'
        elif key in ['当前日期', '当前时间', '当前时期']:
            return 'output'
        else:
            return 'investigation'

    def _get_from_data_model(self, key: str) -> Any:
        """从数据模型的各个部分获取数据

        注意：_store_to_data_model 存储时保留完整 key（含角色前缀），
        此处 first check 即可命中，不需要再去前缀查找。
        """
        if key in self.data_model.basic_info:
            return self.data_model.basic_info[key]

        for category in (
            self.data_model.company_info,
            self.data_model.case_info,
            self.data_model.investigation,
            self.data_model.output_config,
        ):
            if key in category:
                return category[key]

        return None

    def _store_to_data_model(self, key: str, value: Any, category: str) -> None:
        """存储数据到数据模型"""
        role_prefixes = ['本人', '证人', '法人', '家属']
        for prefix in role_prefixes:
            if key.startswith(prefix):
                self.data_model.basic_info[key] = value
                return

        category_map = {
            'basic': self.data_model.basic_info,
            'company': self.data_model.company_info,
            'case': self.data_model.case_info,
            'investigation': self.data_model.investigation,
            'output': self.data_model.output_config,
        }

        if category in category_map:
            category_map[category][key] = value
        else:
            self.data_model.basic_info[key] = value

    def clear_fields(self):
        """清空所有输入控件"""
        fields = [
            self.name_pane, self.age_pane, self.lineEdit,
            self.idnumer_pane, self.textEdit, self.lineEdit_4, self.lineEdit_5
        ]
        if hasattr(self, 'identity_edit'):
            fields.append(self.identity_edit)
        for field in fields:
            if isinstance(field, QLineEdit):
                field.clear()
            elif isinstance(field, QTextEdit):
                field.clear()

        # 清空右侧辅助面板
        if hasattr(self, 'statement_edit'):
            self.statement_edit.clear()
        if hasattr(self, 'material_list'):
            self.material_list.clear()

        # 两处提示位都清空：方框常态就是空的，不该写占位文字
        # （左上角已有静态的「信息提示：」标签，再写一遍会重复）
        self._set_status('', 'black', 'label_14')
        self._set_status('', 'black', 'label_12')
    def _read_all_transcripts(self, case_folder: str) -> str:
        """读取案件目录下所有谈话笔录（本人/证人/法人）全文，按文件名分隔"""
        try:
            if not case_folder or not os.path.exists(case_folder):
                return ""
            exclude_kw = ("审批表", "告知书", "通知书", "发送给AI")
            parts = []
            for fname in sorted(os.listdir(case_folder)):
                if not fname.endswith('.docx'):
                    continue
                if any(k in fname for k in exclude_kw):
                    continue
                fpath = os.path.join(case_folder, fname)
                try:
                    doc = Document(fpath)
                    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
                    if text.strip():
                        parts.append(f"=== {fname} ===\n{text}")
                except Exception:
                    continue
            if not parts:
                logger.warning("⚠️ 目录下没有可用的谈话笔录")
                return ""
            print(f"📚 读取全部笔录: {len(parts)} 份")
            return "\n\n".join(parts)
        except Exception as e:
            logger.warning(f"⚠️ 读取全部笔录失败: {e}")
            return ""

    def approve(self):
        """生成案件审批表 — 读取案本号 → JSON查数据 → 渲染模板"""
        try:
            # ── 1. 读取主界面上的案本号 ──
            case_number = self.lineEdit_2.text().strip()
            if not case_number:
                self._set_status('未找到案本号', 'red')
                QMessageBox.warning(self, "提示", "请先输入或生成案本号")
                return

            # ── 2. 在 cases_data.json 里查找对应数据 ──
            case_obj = self._load_cases_data().get(case_number)
            if not case_obj:
                self._set_status('JSON中未找到该案本号', 'red')
                QMessageBox.warning(self, "提示", f"未在数据中找到案本号：{case_number}")
                return
            # 用案件数据构建 case_data（字段名兼容原有索引字段，后面逻辑不变）
            case_data = {
                'case_id': case_obj.get('case_id', ''),
                'person_name': case_obj.get('name', ''),
                'company_name': case_obj.get('labor_unit', ''),
                'applicant_name': case_obj.get('applicant_name', ''),
                'regulation': regulation_full_for_unit(
                    case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
                    case_obj.get('proposed_article', '')),
                'folder_name': case_obj.get('folder_name', ''),
                'person_gender': case_obj.get('gender', ''),
                'id_card': case_obj.get('id_card', ''),
            }

            person_name = case_data.get('person_name', '')
            company_name = case_data.get('company_name', '')

            # ── 3. 申请人名称（从JSON读取，保存时已按申请类型算好）──
            applicant_name = case_data.get('applicant_name', '')
            self.set_data('申请人名称', applicant_name, 'case')

            # ── 4. 确定案件文件夹（笔录保存在 <BASE_PATH>/<年份>/<案本号>/ 下）──
            case_folder = self._case_dir(case_number)
            if not os.path.exists(case_folder):
                # 兼容旧数据：JSON folder_name 或当前案件文件夹或兜底
                folder_name = case_data.get('folder_name', '')
                if folder_name:
                    case_folder = os.path.join(self.BASE_PATH, folder_name)
                elif self.current_case_folder and os.path.exists(self.current_case_folder):
                    case_folder = self.current_case_folder
                else:
                    case_folder = str(path_utils.get_storage_path(
                        f"{person_name}-工伤案件" if person_name else "未命名案件"
                    ))

            # ── 4.0 检查目录下是否已有审批表文件 ──
            existing_approval = None
            if os.path.isdir(case_folder):
                for fname in sorted(os.listdir(case_folder)):
                    if fname.endswith('.docx') and '审批表' in fname:
                        existing_approval = os.path.join(case_folder, fname)
                        break
            if existing_approval:
                msg = QMessageBox(self)
                msg.setWindowTitle("审批表已存在")
                msg.setText("该案件目录下已有审批表文件，如何处理？")
                view_btn = msg.addButton("查看已有审批表", QMessageBox.AcceptRole)
                new_btn = msg.addButton("新建一个审批表", QMessageBox.ActionRole)
                msg.exec_()
                if msg.clickedButton() == view_btn:
                    success, _ = self.file_service.open_document(existing_approval)
                    if success:
                        self._set_status('已打开已有审批表', 'green')
                    else:
                        self._set_status('打开已有审批表失败', 'orange')
                    return
                # 选择新建：继续走新建流程（文件名会自动加一）

            # ── 4.1 医疗结论 / 受伤经过（AI 分析全部笔录 → 认定/不予认定决策）──
            medical_conclusion = ''
            injury_process = ''
            conclusion = "予以认定"

            if not self.ai_service:
                # 无 AI：提醒并停止（不生成审批表）
                QMessageBox.warning(self, "提示", "未配置AI，无法分析认定/不予认定，不能生成案件审批表。")
                return

            # 解析适用条款情形，供 AI 突出关键证据要素（按规范短名取，15条等全部情形均可用）
            regulation = case_data.get('regulation', '')
            _reg = CaseClassifier.REGULATIONS.get(case_obj.get('proposed_article', ''), {})
            reg_desc = _reg.get('desc', '')
            reg_elements = _reg.get('elements', [])

            # 读取该案全部谈话笔录（本人/证人/法人）
            all_text = self._read_all_transcripts(case_folder)
            if not all_text:
                QMessageBox.warning(self, "提示", "该案件目录下没有找到谈话笔录，无法分析。\n请先生成本人/证人/法人谈话笔录。")
                return

            # AI 分析 → 认定/不予认定 偏向
            self._set_status('正在AI分析笔录...', 'black')
            QApplication.processEvents()
            analysis = self.ai_service.analyze_approval_transcripts(
                all_text,
                case_id=case_number,
                regulation_text=regulation,
                regulation_elements=reg_elements,
            )
            if not analysis:
                QMessageBox.warning(self, "提示", "AI 分析失败，请重试。")
                return

            # 展示对话框（认定工伤 / 不予认定工伤 / 保存）
            dlg = ApprovalDecisionDialog(analysis, self)
            dlg.exec_()
            choice = dlg.get_choice()

            if choice == "保存":
                # 保存分析结果到 JSON，不生成审批表
                self._update_case_field(case_number,
                                        analysis_result=analysis.get("分析", ""),
                                        conclusion_bias=analysis.get("偏向", ""))
                self._set_status('已保存 AI 分析结果', 'green')
                return

            # 生成「调查核实情况」段落（受伤经过）
            self._set_status('正在AI生成调查核实情况...', 'black')
            QApplication.processEvents()
            gen_result = self.ai_service.generate_injury_and_conclusion(
                all_text,
                regulation_text=regulation,
                regulation_desc=reg_desc,
                regulation_elements=reg_elements,
            )
            if gen_result:
                injury_process = gen_result.get("受伤经过", "") or ""
                medical_conclusion = gen_result.get("诊断结论", "") or ""
            if not injury_process:
                QMessageBox.warning(self, "提示", "生成调查核实情况失败，请重试。")
                return

            if choice == "不予认定":
                # 不予认定：在调查核实后面追加关键理由
                reasons = analysis.get("关键理由", []) or []
                if reasons:
                    injury_process += "\n不予认定的关键理由：\n" + "\n".join(
                        f"{i + 1}. {r}" for i, r in enumerate(reasons))
                conclusion = "不予认定"
            else:
                conclusion = "予以认定"

            # 回写 JSON：受伤经过 / 诊断结论 / 结论
            self.set_data('本人受伤经过', injury_process, 'investigation')
            self.set_data('认定结论', conclusion, 'case')
            self._update_case_field(case_number,
                                    injury_process=injury_process,
                                    medical_conclusion=medical_conclusion,
                                    conclusion=conclusion)

            # 医疗结论写入数据模型
            if medical_conclusion:
                self.data_model.investigation['医院诊断'] = medical_conclusion
                self.data_model.investigation['医疗结论'] = medical_conclusion

            # ── 4.2 用JSON数据构建模板变量（{{受伤经过}}先不替换）──
            template_data = {
                '用人单位': company_name,
                '申请人名称': applicant_name,
                '本人姓名': person_name,
                '本人性别': case_data.get('person_gender', ''),
                '本人身份证号': case_data.get('id_card', ''),
                '受伤经过': injury_process,
                '医疗结论': medical_conclusion,
                '单位性质': case_obj.get('unit_type', DEFAULT_UNIT_TYPE),
                '引用条例': case_data.get('regulation', ''),
                '申请时间': self._resolve_date_input(self.apply_time_edit.text()),
                '受理时间': self._resolve_date_input(self.accept_time_edit.text()),
            }

            # ── 5. 获取模板路径 ──
            template_path = str(path_utils.get_document_template_path(
                '工伤案件审批表（模板）.docx'
            ))
            if not os.path.exists(template_path):
                self._set_status('模板文件不存在', 'red')
                QMessageBox.critical(self, "错误",
                    f"找不到模板文件:\n{template_path}")
                return

            # ── 5. 预处理模板：根据结论在「认定工伤/不予认定工伤」方框打勾 ──
            temp_template = documents.tick_approval_box(template_path, conclusion)

            # ── 6. 渲染模板（仅替换带 {{}} 外框的占位符）──
            word = documents.render_template(temp_template, template_data)

            # ── 6. 保存（不覆盖已有的审批表）──
            os.makedirs(case_folder, exist_ok=True)
            base = f"{person_name}案件审批表" if person_name else "案件审批表"
            target_path = documents.unique_path(case_folder, base)
            word.save(target_path)
            print(f"✅ 案件审批表已保存: {target_path}")

            # ── 清理临时模板 ──
            try:
                os.remove(temp_template)
            except Exception:
                pass

            # ── 9. 打开 ──
            success, message = self.file_service.open_document(target_path)
            if success:
                self._set_status('案件审批表生成成功', 'green')
            else:
                self._set_status(f'审批表已生成，打开失败: {message}', 'orange')

        except Exception as e:
            logger.error(f"❌ 生成审批表异常: {e}")
            import traceback
            traceback.print_exc()
            self._set_status(f'生成审批表失败: {str(e)}', 'red')

    def _on_name_pane_changed(self):
        """当 name_pane 输入完成时，如果是本人角色，自动生成案本号"""
        try:
            role = self.get_current_role_type()
            if role != "本人":
                return
            name = self.name_pane.text().strip()
            if not name:
                return
            # 只有当前没有案本号时才自动生成（避免覆盖用户手动编辑）
            current = self.lineEdit_2.text().strip()
            if not current:
                id_card = self.idnumer_pane.text().strip()
                case_num = self._auto_generate_case_number(name, id_card)
                self.lineEdit_2.setText(case_num)
                self.set_data('案本号', case_num, 'case')
        except Exception as e:
            print(f"自动生成案本号失败: {e}")

    def _auto_generate_case_number(self, person_name: str, id_card: str = "") -> str:
        """根据本人姓名和身份证后四位自动生成案本号"""
        is_death = self.death_case_checkbox.isChecked()
        prefix = "工亡" if is_death else "案本"
        date = datetime.datetime.now().strftime("%Y%m%d")
        id_last4 = id_card[-4:] if id_card and len(id_card) >= 4 else "xxxx"
        return f"{person_name}-{prefix}{date}{id_last4}"
    def init_comboboxes(self):
        """初始化所有组合框"""
        self.init_combobox(self.company_pane, self.items_list1)
        self.init_combobox(self.construction_company, self.items_list)
        self.init_combobox(self.construction_plant, self.items_list2)

        self.company_pane.setCurrentIndex(-1)
        self.construction_company.setCurrentIndex(-1)
        self.construction_plant.setCurrentIndex(-1)

    def company(self):
        """「用人单位」控件变化时同步数据。

        本人角色 → 案件级用人单位（全案文书共用）；其余角色 → 该人记录里的 unit。
        证人/家属的工作单位不必与案件用人单位相同，故不能一律写入案件级。
        """
        name = self.company_pane.currentText().strip()
        try:
            role = self.get_current_role_type()
        except Exception:
            role = "本人"
        if role == "本人":
            self.set_data('用人单位', name, 'company')
        else:
            self.set_data(person_flat_key(role, 'unit'), name, 'basic')

    def sync_employer_to_dict(self):
        """更新用工单位信息（construction_company 现为用工单位）"""
        company_name = self.construction_company.currentText().strip()
        self.set_data('用工单位', company_name, 'company')

    def c_plant(self):
        """更新工地名称信息"""
        site_name = self.construction_plant.currentText().strip()
        self.set_data('工地名称', site_name, 'company')

    def id_clicked(self):
        """读取身份证信息"""
        try:
            dll = windll.LoadLibrary("./sdtapi.dll")
            port = c_int32(1001)
            ifopen = c_int32(1)
            pucManaInfo = create_string_buffer(4)
            pucManaMsg = create_string_buffer(8)
            dll.SDT_StartFindIDCard(port, pucManaInfo, ifopen)
            dll.SDT_SelectIDCard(port, pucManaMsg, ifopen)
            pucCHMsg = create_unicode_buffer(256)
            pucPHMsg = create_string_buffer(1024)
            puiCHMsgLen = c_uint(0)
            puiPHMsgLen = c_uint(0)
            ret = dll.SDT_ReadBaseMsg(port, pucCHMsg, byref(puiCHMsgLen), pucPHMsg,
                                      byref(puiPHMsgLen), ifopen)
            if ret == 65:
                return
            dll.SDT_ClosePort(port)
            self.set_data('当前时期', date_now() + time_now(), 'output')

            role = self.get_current_role_type()
            self.process_id(pucCHMsg, role)

        except Exception as e:
            print(f"读取身份证信息时出错: {str(e)}")

    def process_id(self, pucCHMsg, role):
        """处理身份证信息"""
        try:
            name = pucCHMsg.value[0:15].strip()
            self.data_model.update_basic_info(role, {'姓名': name})
            self.set_data(f"{role}姓名", name, 'basic')
            self.name_pane.setText(name)

            if len(pucCHMsg.value) >= 79:
                id_number = pucCHMsg.value[61:79].strip()
                self.data_model.update_basic_info(role, {'身份证号': id_number})
                self.set_data(f"{role}身份证号", id_number, 'basic')
                self.idnumer_pane.setText(id_number)

            if len(pucCHMsg.value) >= 61:
                address = pucCHMsg.value[26:61].strip()
                self.data_model.update_basic_info(role, {'身份证地址': address})
                self.set_data(f"{role}身份证地址", address, 'basic')
                self.textEdit.setText(address)

            self.process_id_info(role)
            self.calculate_age_from_id(role)

        except Exception as e:
            import traceback
            traceback.print_exc()

    def update_role_info(self, role):
        """按角色把共享表单内容写入兼容扁平键（统一人记录 schema），并处理角色化副作用"""
        try:
            self._save_date_inputs()
            person = self._read_form_as_person()
            self._person_to_flat(role, person)

            if role == "本人":
                # 本人的「单位」即案件级用人单位（其余角色只写各自的 单位名称）
                self.set_data('用人单位', person.get('unit', ''), 'company')
                # 自动生成案本号
                current_case = self.lineEdit_2.text().strip()
                if not current_case:
                    case_num = self._auto_generate_case_number(
                        person.get('name', ''), person.get('id_card', '')
                    )
                    self.lineEdit_2.setText(case_num)
                    self.set_data('案本号', case_num, 'case')

            if role == "法人":
                company_name = self.get_data('用工单位', '')
                if not company_name:
                    company_name = self.construction_company.currentText().strip()
                    if company_name:
                        self.set_data('用工单位', company_name, 'company')

        except Exception as e:
            import traceback
            traceback.print_exc()
    def process_id_info(self, role):
        """处理身份证信息并更新性别显示（使用DataService）"""
        try:
            idcard = self.get_data(f"{role}身份证号", "")
            if not idcard:
                return

            # 使用DataService提取性别
            gender = self.data_service.extract_gender_from_idcard(idcard)
            if gender is None:
                print(f"[process_id_info] 无法从身份证提取性别: {idcard}")
                return

            # 设置性别数据
            self.set_data(f"{role}性别", gender, 'basic')
            self.lineEdit.setText(gender)

        except Exception as e:
            print(f"[process_id_info] 错误: {e}")
            import traceback
            traceback.print_exc()

    def get_current_role_type(self) -> str:
        """获取当前选中的角色类型"""
        if self.radioButton.isChecked():
            return "本人"
        elif self.radioButton_2.isChecked():
            return "证人"
        elif self.radioButton_3.isChecked():
            return "法人"
        elif hasattr(self, 'radioButton_4') and self.radioButton_4.isChecked():
            return "家属"
        return "本人"
    def smart_search_cases(self):
        """按案本号在 cases_data.json 中模糊搜索，并回填主界面"""
        try:
            keyword = self.lineEdit_2.text().strip()
            if not keyword:
                keyword = self.get_data("本人姓名", "")
            if not keyword:
                keyword = self.name_pane.text().strip()
            if not keyword:
                QMessageBox.warning(self, "提示", "请输入案本号进行搜索")
                return

            cases = self._load_cases_data()  # {case_id: case_obj}
            if not cases:
                QMessageBox.information(self, "提示", "还没有保存任何案件数据")
                return

            # 模糊匹配：案本号包含关键字
            kw = keyword.lower()
            matched = [(cid, obj) for cid, obj in cases.items()
                       if kw in str(cid).lower()]

            if not matched:
                QMessageBox.information(self, "提示", f"未找到案本号包含「{keyword}」的案件")
                return

            if len(matched) == 1:
                self._load_case_to_form(matched[0][1])
            else:
                self._show_case_search_dialog(matched)

        except Exception as e:
            QMessageBox.critical(self, "错误", f"搜索失败: {str(e)}")
            import traceback
            traceback.print_exc()

    def _load_case_to_form(self, case_obj: Dict[str, Any]):
        """把单个案件 JSON 回填主界面与数据模型"""
        # 切回本人角色再回填
        self.radioButton.setChecked(True)
        self.clear_role_fields()
        self._apply_case_object(case_obj)
        # 恢复该案件的证人列表到内存（统一人记录 schema），供后续切换/生成使用
        self.data_model.witnesses = [dict(w) for w in case_obj.get('witnesses', [])]
        self.data_model.current_witness_index = -1
        if hasattr(self, 'witness_combo'):
            self._refresh_witness_combo()
        self.current_case_id = str(case_obj.get('case_id', ''))
        self._set_status(f"已加载案件：{self.current_case_id}", 'green')
        QMessageBox.information(
            self, "加载成功",
            f"已加载案件数据：\n案本号：{self.current_case_id}\n姓名：{case_obj.get('name', '')}"
        )

    def _show_case_search_dialog(self, matched):
        """弹出窗口列出匹配案件供选择"""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"选择案件（{len(matched)} 条）")
        dialog.resize(720, 460)

        layout = QVBoxLayout()
        title = QLabel(f"找到 {len(matched)} 条匹配案件，请选择：")
        layout.addWidget(title)

        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(['案本号', '姓名', '案件性质', '申请类型', '用人单位', '拟用条例'])
        table.setRowCount(len(matched))
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)

        for i, (case_id, case_obj) in enumerate(matched):
            table.setItem(i, 0, QTableWidgetItem(str(case_id)))
            table.setItem(i, 1, QTableWidgetItem(str(case_obj.get('name', ''))))
            table.setItem(i, 2, QTableWidgetItem(str(case_obj.get('case_nature', ''))))
            table.setItem(i, 3, QTableWidgetItem(str(case_obj.get('applicant_type', ''))))
            table.setItem(i, 4, QTableWidgetItem(str(case_obj.get('labor_unit', ''))))
            table.setItem(i, 5, QTableWidgetItem(str(case_obj.get('proposed_article', ''))))

        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        if table.rowCount() > 0:
            table.selectRow(0)
        layout.addWidget(table)

        # 双击直接选择
        table.cellDoubleClicked.connect(
            lambda r, c: self._on_case_search_selected(matched, r, dialog)
        )

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        select_btn = QPushButton("选择")
        select_btn.clicked.connect(
            lambda: self._on_case_search_selected(matched, table.currentRow(), dialog)
        )
        btn_layout.addWidget(select_btn)
        layout.addLayout(btn_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def _on_case_search_selected(self, matched, row: int, dialog: QDialog):
        """用户选中某条案件后的处理"""
        if row < 0 or row >= len(matched):
            QMessageBox.warning(dialog, "提示", "请先选择一行")
            return
        case_obj = matched[row][1]
        dialog.accept()
        self._load_case_to_form(case_obj)



if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())