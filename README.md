# 工伤助手

工伤认定办案辅助工具（Windows 桌面程序，PyQt5）。

录入案件信息 → 生成四类谈话笔录与文书 → AI 辅助审查与补问 → 文书送达待办看板。

## 运行环境

- **Windows**
- **32 位 Python 3.11**（本机为 `C:\Python311-32`）

> 必须 32 位：身份证读卡器动态库 `sdtapi.dll` 是 32 位，64 位 Python 加载不了。
> 未接读卡器也能用，只是得手工录入身份证信息。

## 安装与运行

```bash
python -m pip install -r requirements.txt
python main.py
```

## 测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

当前 134 个用例、约 3 秒。分四层：投影层纯函数（无 Qt，最快）、角色与布局（走 Qt）、
案件备份、日志与异常兜底。

## 目录结构

| 文件 | 行数（约） | 职责 |
|---|---:|---|
| `app_main.py` | 5193 | 主窗口与全部业务逻辑（**占全项目 62%**） |
| `ui_main_window.py` | 209 | Qt Designer 生成的界面定义（仓库内无 `.ui` 源文件，它就是唯一来源） |
| `main.py` | 210 | 程序入口、用户/API 配置管理、控制台编码修正 |
| `ai_service.py` | 475 | DeepSeek API 调用（提取文本、笔录生成、受伤经过/审批表等文书分析） |
| `prompt_manager.py` | 426 | 提示词外置管理，读写 `resource/prompts/*.txt` |
| `case_classifier.py` | 68 | 条例目录与法律要件 |
| `services.py` | 193 | 文件/数据服务和模板变量管理 |
| `config_service.py` | 512 | 配置读写 |
| `todo_board.py` | 403 | 文书送达待办看板与送达确认弹窗 |
| `service_flow.py` | 295 | 送达状态引擎（举证通知→告知书→认定决定书） |
| `path_utils.py` | 146 | 统一路径管理（单例） |
| `log_utils.py` | 144 | 统一日志与全局异常兜底 |
| `patch_templates_2026.py` | 80 | 一次性模板补丁脚本（**幂等，见下**） |

## 数据存放位置

| 内容 | 位置 |
|---|---|
| 案件数据 | 桌面 `工伤助手存储案本/cases_data.json` |
| 案件备份 | 同上目录：`.bak`、`.v2.bak`、`backups/` |
| 配置 | `<程序目录>/config/` |
| 日志 | `<程序目录>/logs/app.log` |
| 模板 | `<程序目录>/resource/模板文件/` |
| 提示词 | `<程序目录>/resource/prompts/*.txt`（可用记事本直接改，存盘即生效） |

桌面路径用 `SHGetKnownFolderPath` 取真实桌面，兼容桌面被重定向到其它盘（如 `G:\桌面`）的情形。

## 案件数据的三层备份

保存 `cases_data.json` 前会依次做（任一层失败都不阻断保存）：

| 文件 | 时机 | 用途 |
|---|---|---|
| `cases_data.json.v2.bak` | 仅首次（升级前是老格式时） | 退回旧版程序 |
| `cases_data.json.bak` | **每次保存前刷新** | 撤销上一次保存 |
| `backups/cases_data_YYYYMMDD.json` | 每天第一份，保留 30 天 | 找回更早的状态 |

写入用「临时文件 + 原子替换」，写到一半崩溃或磁盘写满不会毁掉原文件。

## 日志

- 文件：`logs/app.log`，单文件 2MB、保留 5 份，收 INFO 及以上
- 控制台：只收 WARNING 及以上（程序里有大量既有 `print`，避免重复刷屏）
- 未捕获异常（含子线程）会记 CRITICAL + 完整调用栈

> 注意：PyQt5 调用完 `sys.excepthook` 后仍会 `qFatal()` 中止进程，所以异常**会留证据但程序仍会崩**。

## 开发者文档

- [docs/data-format.md](docs/data-format.md) —— 案件数据存储格式、人记录 schema、模板占位符清单

## 已知待办

- [ ] **打包分发**：无 PyInstaller spec，目前只有开发机能跑。需一并打包 32 位 Python、`sdtapi.dll`、`resource/`、`config/`
- [ ] **备份恢复界面**：备份文件已生成，但办案人员没法自己恢复（需手工找文件覆盖）
- [ ] **拆分 `app_main.py`**：约 5368 行占全项目 63%，属高风险重构，应在测试更完备后再做
- [ ] **多案型谈话模板**：代码按角色硬编码，只用 4 份「普通工伤案件」模板（`app_main.py:236-251`），
      而条例目录有 10 项。若将来要按案型（上下班时/因工外出/患职业病等）切换模板，
      那批模板曾在 `resource/模板文件/谈话模板 - 副本/`（18 份），已因无人引用删除，
      可从 git 历史找回
