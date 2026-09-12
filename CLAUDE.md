# 项目须知（给 Claude Code / 新同事）

工伤认定办案辅助工具。产品背景与运行方式见 [README.md](README.md)，
数据格式见 [docs/data-format.md](docs/data-format.md)。本文只讲**代码里非显而易见的部分**。

## 约定

- **提交信息用中文**，格式 `type: 描述`（`feat`/`fix`/`refactor`/`chore`/`test`），
  多处改动用「；」分隔，大改动在正文分条列要点。参考 `git log` 已有风格
- **注释用中文**，密度随现有代码
- **直接提交到 `master`**（本仓库历史一直是线性直提，没有分支流程）
- 改动前后都跑 `python -m pytest`（100 个用例，约 3 秒）

## 架构要点

### 1. 案件数据：内存 flat ↔ 磁盘分块，靠投影层隔离

**这是全项目最容易改坏的地方。**

磁盘上 `cases_data.json` 是「案本号下按人分块」：

```
case_id / case_info / injured_worker / witnesses[] / legal_reps[] / family_reps[]
```

但**内存里仍是「本人字段平铺在顶层」的 flat 形态**——因为下游有 80+ 处
`case_obj.get('name')` 这样的读取，还有提示词填充、模板渲染、待办看板全依赖它。

转换只发生在持久化的两端：

```
_load_cases_data:  磁盘分块 --unpack_case()--> flat
_save_cases_data:  flat --pack_case()-------> 磁盘分块
```

**所以改动时不要动下游的读取代码**，只在 `pack_case` / `unpack_case` 里调整。
新增字段时想清楚它该进 `case_info` 还是 `injured_worker`（见 `_INJURED_WORKER_FIELDS`）。

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

`person_flat_key(role, field)` 生成形如 `法人职务`、`家属单位名称` 的键，
存进 `data_model.basic_info`（`_store_to_data_model` 对带角色前缀的键强制进 basic_info）。

- `position` 走 `_FLAT_POSITION_SUFFIX`
- 其余走 `PERSON_CN_SUFFIX`（`unit` → `单位名称`）
- `PERSON_BASE_FIELDS` = name/gender/age/id_card/address/phone/position/identity/**unit**

`_person_to_flat` / `_mirror_witness_to_flat` **空值也要写**——表单是该角色的唯一数据来源，
跳过空值会导致「清空输入框」传不进去、旧值残留在笔录里。

## 容易踩的坑

- **`py_compile` 只查语法不查名字**。转换代码后必须真正 `import` 一遍：
  曾出现过漏 `import logging` 而编译照样通过、只有运行时才 `NameError` 的情况
- **脚本里按 AST 删代码时，别用「`ast.walk` 取最大 `lineno`」判断函数结束位置**：
  多行集合的收尾括号（`}`/`]`/`)`）不属于任何节点，会少删一行、把函数截断成语法错误。
  用 `node.end_lineno`（Python 3.8+）。删完每步都要 `ast.parse` 验证
- **删函数要查级联**：被删函数的唯一调用者会跟着变成死代码（本次就带出了
  `_update_case_in_data`、`get_company_info`、`create_enhanced_case_folder`、
  `get_missing`）。删完重跑一遍检测
- **`patch_templates_2026.py` 是幂等的补丁脚本**，里面写死了各角色笔录的表头文本。
  改了模板表头就必须同步改它，否则谁再跑一次补丁就把你的改动覆盖回去
- **`*.bak.docx` 是补丁脚本生成的备份**，已 gitignore，不用管
- **Windows 控制台 codepage 936**：在 `cmd` 里跑 pytest，中文测试名显示为乱码；
  Cursor/Windows Terminal（UTF-8）正常
- **`MainWindow` 构造很重**（路径、服务、AI 客户端、组合框数据），
  测试里用会话级 fixture 只建一次，用例之间靠 fixture 收尾重置状态

## 写测试的约定

- 纯函数放 `tests/test_case_storage.py`（无 Qt，最快）；要动界面的放 `tests/test_role_scope.py`
- 文件类放 `tests/test_case_backup.py`；日志类放 `tests/test_log_utils.py`
- **危险逻辑的测试要用变异验证**：把修复改回旧写法，确认对应用例真的会失败。
  写过一条「原子写入」用例只检查没留下 `.tmp`，改回截断写法照样通过——是假测试，
  后来补了「写入中途失败不毁原文件」才有牙齿
- `BASE_PATH` 必须指向临时目录，别碰真实案卷
