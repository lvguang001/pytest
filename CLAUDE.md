# 项目须知（给 Claude Code / 新同事）

工伤认定办案辅助工具。产品背景与运行方式见 [README.md](README.md)，
数据格式见 [docs/data-format.md](docs/data-format.md)。本文只讲**代码里非显而易见的部分**。

## 约定

- **提交信息用中文**，格式 `type: 描述`（`feat`/`fix`/`refactor`/`chore`/`test`），
  多处改动用「；」分隔，大改动在正文分条列要点。参考 `git log` 已有风格
- **注释用中文**，密度随现有代码
- **直接提交到 `master`**（本仓库历史一直是线性直提，没有分支流程）
- 改动前后都跑 `python -m pytest`（103 个用例，约 2 秒）

## 架构要点

### 1. 案件数据：内存 flat ↔ 磁盘分块，靠投影层隔离

**这是全项目最容易改坏的地方。**

磁盘上单个案件文件是「案本号下按人分块」：

```
case_id / case_info / injured_worker / witnesses[] / legal_reps[] / family_reps[]
```

但**内存里仍是「本人字段平铺在顶层」的 flat 形态**——因为下游有 80+ 处
`case_obj.get('name')` 这样的读取，还有提示词填充、模板渲染、待办看板全依赖它。

转换只发生在持久化的两端：

```
case_store.load_all:  磁盘分块 --unpack_case()--> flat
case_store.save_all:  flat --pack_case()-------> 磁盘分块
```

**整套磁盘逻辑都在 `case_store.py` 里**，分两层：

- **分块格式**（`pack_case` / `unpack_case` / `migrate_case` / `SCHEMA_VERSION`）
  —— 纯函数，不碰文件系统
- **落盘布局**（`case_dir` / `load_all` / `write_case` / `save_all` /
  `migrate_legacy_file` / `daily_snapshot` …）
  —— 一案一文件、按年份分层、写前备份、老档拆分

2026-09 从 MainWindow 里搬出来并把 `base_path` 显式当参数传，因此不用构造窗口
就能直接单测——`tests/test_case_store.py` 已覆盖（含原子写、删数据不连坐文书、
老档迁移幂等）。**改这里先跑它。**

`MainWindow` 上仍留着一层同名薄封装（`self._load_cases_data()` 等），只做
「绑定 `self.BASE_PATH`」——40 多处调用点因此不用改。

**所以改动时不要动下游的读取代码**，只在 `pack_case` / `unpack_case` 里调整。
新增字段时想清楚它该进 `case_info` 还是 `injured_worker`
（见 `case_store._INJURED_WORKER_FIELDS`）。

同一条链路上还有 `migrate_case()`（老档家属槽位迁移）——它靠「`identity` 键**不存在**」
而不是「值为空」来识别老档，因为新记录一律带该键。改这条判据会让新档数据被误搬。

### 2. 共享表单控件 + 按角色存人记录

界面上 `name_pane`、`lineEdit_5`、`identity_edit`、`company_pane` 等**都是四个角色共用**的，
切换角色时靠 `_read_form_as_person()` / `_write_person_to_form()` 存取该角色的人记录。

几个按角色变语义的槽位：

| 控件 | 本人 | 证人 | 法人 | 家属 |
|---|---|---|---|---|
| `lineEdit_5` | 岗位 | 岗位 | **职务** | 岗位 |
| `identity_edit` | 身份 | 身份 | 身份 | **与死者关系** |
| `company_pane` | **案件级用人单位** | 自己的单位 | 自己的单位 | 自己的单位 |

**`company_pane` 是坑点**：它的 `currentTextChanged` 直接接到 `company()`，
若在那里无条件写案件级 `用人单位`，任何角色填自己的单位都会污染全案文书。
`_restore_role_unit()` 负责按角色回填该控件，`_apply_case_object()` 里不要直接推它。

### 3. 扁平键命名

人记录 schema 与扁平键规则整套在 **`services.py`**（`PERSON_BASE_FIELDS` /
`PERSON_CN_SUFFIX` / `_FLAT_POSITION_SUFFIX` / `person_flat_key`），
2026-09 从 app_main.py 搬过去——它是数据模型约定，跟界面无关。
同一次搬过去的还有 `CaseDataModel`（案件数据模型本身）和
`date_now` / `time_now` / `timestamp_now` / `format_compact_time`
（中文格式的日期显示，与 `service_flow` 那套 ISO 期限计算是两回事）。

`person_flat_key(role, field)` 生成形如 `法人职务`、`家属单位名称` 的键，
存进 `data_model.basic_info`（`_store_to_data_model` 对带角色前缀的键强制进 basic_info）。

- `position` 走 `_FLAT_POSITION_SUFFIX`
- 其余走 `PERSON_CN_SUFFIX`（`unit` → `单位名称`）
- `PERSON_BASE_FIELDS` = name/gender/age/id_card/address/phone/position/identity/**unit**

`_person_to_flat` / `_mirror_witness_to_flat` **空值也要写**——表单是该角色的唯一数据来源，
跳过空值会导致「清空输入框」传不进去、旧值残留在笔录里。

### 4. 界面：构建与业务分家，全用布局管理器

主界面原先靠「Qt Designer 绝对坐标 + 十几个 `_move_xxx` 补丁函数」拼出来，
那些补丁还带隐式的调用顺序依赖（`_move_unit_type_row` 必须最先、`_uniform_row_spacing`
必须最后……）。现在改成：

```
MainWindowUI (ui_main_build.py)   建控件 + 用 QVBoxLayout/QHBoxLayout 摆位置，不连信号
        ↑ 继承
MainWindow   (app_main.py)        业务逻辑；信号统一在 _connect_signals() 里接
```

- **控件变量名是业务契约**：`app_main.py` 有 80+ 处 `self.xxx`，一个都不能改。
  改名前先跑 `python -m pytest`——`tests/ui_helpers.py` 的 `LEFT_ROWS`
  和 `tests/ui_geometry_baseline.json` 就是控件清单，改错了会红。
- 控件尺寸钉在 `MainWindowUI._SIZES` —— 布局不改就按 `sizeHint` 收缩，各行高度全变。
- 信号连接**只在** `MainWindow._connect_signals()`（`.ui` 生成代码里那批除外）。
  新增信号请加在那里，别再散回各个初始化方法里——散着的时候没人看得出
  「谈话通知书」被重复接了 4 次。
- **`ui_main_build.py` 不许 `import app_main`**：两边会成环；而且
  `python app_main.py` 直接启动时该模块名是 `__main__`，延迟 import "app_main"
  会再加载一份模块、造出两个不同的类。依赖 app_main 常量的文案由
  `MainWindow.__init__` 补。
- **`QMetaObject.connectSlotsByName`（在 `ui_main_window.py` 末尾）会把任何
  `on_<子控件名>_<信号>` 形状的方法自动接上**，`clicked()` / `clicked(bool)`
  两个重载各接一条。新增方法别起成这个形状；已有的就 `disconnect()` 后再连。
- 浮层（`api_group`、`todo_board`）仍绝对定位，但位置用 `self.width()` 算。

## 容易踩的坑

- **`py_compile` 只查语法不查名字**。转换代码后必须真正 `import` 一遍：
  曾出现过漏 `import logging` 而编译照样通过、只有运行时才 `NameError` 的情况
- **脚本里按 AST 删代码时，别用「`ast.walk` 取最大 `lineno`」判断函数结束位置**：
  多行集合的收尾括号（`}`/`]`/`)`）不属于任何节点，会少删一行、把函数截断成语法错误。
  用 `node.end_lineno`（Python 3.8+）。删完每步都要 `ast.parse` 验证
- **删函数要查级联**：被删函数的唯一调用者会跟着变成死代码（本次就带出了
  `_update_case_in_data`、`get_company_info`、`create_enhanced_case_folder`、
  `get_missing`）。删完重跑一遍检测
- **模板表头不要再靠脚本改**。曾经的 `patch_templates_2026.py` 是一次性补丁，
  把各角色笔录表头改成「单位性质/身份」措辞——它写死了表头文本，谁改了模板、
  再有人手滑跑一遍，改动就被覆盖回去。2026-09 确认 4 个谈话模板和 2 份告知书
  都已是目标文本后把它删了（要捞回来看 `git log -- patch_templates_2026.py`）。
  以后再要批量改模板，用一次性脚本改完就删，别长期留在仓库里
- **`*.bak.docx` 是这类模板脚本生成的备份**，已 gitignore，不用管
- **Windows 控制台 codepage 936**：在 `cmd` 里跑 pytest，中文测试名显示为乱码；
  Cursor/Windows Terminal（UTF-8）正常
- **`MainWindow` 构造很重**（路径、服务、AI 客户端、组合框数据），
  测试里用会话级 fixture 只建一次，用例之间靠 fixture 收尾重置状态

## 写测试的约定

- 纯函数（最快，无 Qt）放 `tests/test_case_store.py`——案件数据投影层就在这测；
  界面结构/外观放 `tests/test_ui_layout.py`；点出来的行为放 `tests/test_ui_behavior.py`；
  信号连接放 `tests/test_ui_signals.py`；公共夹具与工具在 `conftest.py` / `ui_helpers.py`
- **危险逻辑的测试要用变异验证**：把修复改回旧写法，确认对应用例真的会失败。
  写过一条「原子写入」用例只检查没留下 `.tmp`，改回截断写法照样通过——是假测试，
  后来补了「写入中途失败不毁原文件」才有牙齿
- `BASE_PATH` 必须指向临时目录，别碰真实案卷。这条由 `conftest.py` 顶层保证——
  **改路径的代码必须在 `import app_main` 之前跑**，否则 MainWindow 构造时就用了真实路径
- **`window` 夹具必须 `show()` 一次**：布局管理器要到窗口显示时才把控件摆到位，
  没 show 之前读到的还是 `ui_main_window.py` 里那套绝对坐标。用 `WA_DontShowOnScreen`
  避免跑测试时真弹窗口
- 外观基准是 `tests/ui_geometry_baseline.json`。有意改界面后重新生成：
  `UI_BASELINE_UPDATE=1 python -m pytest tests/test_ui_layout.py -k baseline`，
  并在提交信息里写清动了哪里、为什么
