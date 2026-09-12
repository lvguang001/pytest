# 数据格式与模板占位符

## 一、案件数据 `cases_data.json`

位置：桌面 `工伤助手存储案本/cases_data.json`（桌面路径经 `SHGetKnownFolderPath` 取真实值）。

### 当前版本：3.0

顶层 `version` 字段标明结构版本（`app_main.SCHEMA_VERSION`）。读到更高版本时程序会记
ERROR 日志并提示改用新版程序，但**仍会读出来**，不会因为版本号就让用户看不到案件。

```json
{
  "version": "3.0",
  "cases": {
    "赵六-工亡202609051111": {
      "case_id": "赵六-工亡202609051111",

      "case_info": {
        "case_nature": "工亡案件",
        "applicant_type": "个人申请",
        "unit_type": "企业",
        "employer": "永嘉县XX建设工程有限公司",
        "labor_unit": "温州YY建筑劳务有限公司",
        "site": "ZZ新城项目一期工地",
        "apply_time": "20260910",
        "accept_time": "",
        "visit_time": "",
        "injury_time": "20260802",
        "proposed_article": "第十四条第（一）项",
        "proposed_article_elements": ["工作时间", "工作场所", "工作原因"],
        "recorder": "吕广",
        "applicant_name": "赵六",
        "service_flow": { "…送达状态机…" },
        "folder_name": "赵六-工亡202609051111",
        "transcript_file": "…",
        "analysis_result": { "…" },
        "conclusion": "…"
      },

      "injured_worker": {
        "name": "赵六", "gender": "男", "age": "45",
        "id_card": "330324198111223333", "phone": "13600003333",
        "address": "浙江省温州市XX路", "position": "钢筋工",
        "identity": "职工", "unit": "温州ZZ劳务有限公司",
        "injury_description": "在工地作业时突发疾病…",
        "materials": [{ "name": "身份证复印件", "provided": true, "notes": "" }]
      },

      "witnesses": [
        { "role": "证人", "seq": "证人一", "name": "刘大", "gender": "男", "age": "35",
          "id_card": "…", "address": "…", "phone": "…",
          "position": "钢筋工", "identity": "职工", "unit": "别的公司" }
      ],

      "legal_reps": [
        { "role": "法人", "name": "王老板", "position": "总经理",
          "identity": "法定代表人", "unit": "…", "materials": [] }
      ],

      "family_reps": [
        { "role": "家属", "name": "赵妻", "position": "缝纫工",
          "identity": "夫妻", "unit": "温州XX服装有限公司" }
      ]
    }
  }
}
```

### 键的归属规则

`pack_case()` 按两条表分派：

- **`_INJURED_WORKER_FIELDS`** → 进 `injured_worker`：
  `name`、`gender`、`age`、`id_card`、`address`、`phone`、`position`、
  `identity`、`unit`、`injury_description`、`materials`
- **`_NAMED_BLOCKS`**（`witnesses`/`legal_reps`/`family_reps`）与
  **`_TOP_LEVEL_KEYS`**（`case_id`）→ 各自独立成块，不重复进 `case_info`
- **其余一律进 `case_info`**（含尚未认识的扩展键——这样加新字段不会丢数据）

### 内存形态与投影

磁盘是分块的，但**内存里是 flat 的**（本人字段平铺在顶层）。转换只在
`_load_cases_data` / `_save_cases_data` 两端发生：

```
_load_cases_data:  磁盘分块 --unpack_case()--> flat
_save_cases_data:  flat --pack_case()-------> 磁盘分块
```

新增字段时不需要改下游的读取代码，只要在 `pack_case` / `unpack_case` 里放对位置。

### 老档兼容

- **v2（无 `injured_worker` 块的平铺结构）**：`unpack_case()` 原样返回，首次保存时升为 3.0
- **家属槽位迁移**：v2 时代家属的 `position` 存的是「与死者关系」，现改存该家属自己的
  岗位、关系移入 `identity`。`migrate_case()` 负责搬运，判据是 `identity` 键**不存在**
  （新记录一律带该键，所以不能用「值为空」判断）

### 备份

见 README「案件数据的三层备份」。写入采用「临时文件 + `os.replace` 原子替换」。

---

## 二、人记录 schema

一份人记录 = `PERSON_BASE_FIELDS` 这 9 个键：

| 键 | 含义 | 本人 | 证人 | 法人 | 家属 |
|---|---|---|---|---|---|
| `name` | 姓名 | 受伤职工 | 证人 | 法人 | 家属 |
| `gender` / `age` | 性别 / 年龄 | ✓ | ✓ | ✓ | ✓ |
| `id_card` | 身份证号 | ✓ | ✓ | ✓ | ✓ |
| `address` | 身份证地址 | ✓ | ✓ | ✓ | ✓ |
| `phone` | 手机号 | ✓ | ✓ | ✓ | ✓ |
| `position` | 语义随角色 | 岗位 | 岗位 | **职务** | 岗位 |
| `identity` | 语义随角色 | 身份 | 身份 | 身份 | **与死者关系** |
| `unit` | 该人自己的工作单位 | 即案件级用人单位 | 自己的单位 | 自己的单位 | 自己的单位 |

四人记录额外带 `role`；证人多带 `seq`（证人一/二/…）；各人可带 `materials`。

### 扁平键命名

界面控件是四角色共享的，人记录按角色前缀存成中文扁平键（进 `basic_info`）：

```
person_flat_key(role, field) →
  field == "position"  →  role + _FLAT_POSITION_SUFFIX[role]   # 本人岗位/证人岗位/法人职务/家属岗位
  否则                  →  role + PERSON_CN_SUFFIX[field]       # 家属单位名称/法人身份证号/…
```

### 「用人单位」控件按角色取值

`company_pane` 是共享控件，其 `currentTextChanged` 接到 `company()`：

- **本人角色** → 写案件级 `用人单位`（`case_info.labor_unit`，全案文书共用）
- **其他角色** → 写该角色人记录的 `unit`

`_restore_role_unit()` 按当前角色回填该控件。**在 `_apply_case_object()` 里不要直接推它**——
那会经信号把案件级单位写进当前角色的 `unit` 槽。

### 家属的空值联动

家属笔录表头那行描述的是**家属自己的单位**，且：`家属单位名称` 为空时
`家属岗位` 一并输出为空（见 `_build_family_template_data()`）。

---

## 三、模板占位符

模板位于 `resource/模板文件/`。占位符由各 `_build_*_template_data()` 提供。

### 谈话模板（`谈话模板/`）

| 模板 | 占位符 |
|---|---|
| `本人谈话笔录（普通工伤案件）.docx` | 本人姓名、本人性别、本人年龄、本人身份证号、本人身份证地址、本人手机号、本人岗位、本人身份、单位名称、单位性质、用人单位、用工单位、当前时期、用户名 |
| `证人谈话笔录（普通工伤案件）.docx` | 证人姓名、证人性别、证人年龄、证人身份证号、证人身份证地址、证人手机号、证人岗位、证人身份、本人姓名、单位名称、单位性质、当前时期、用户名 |
| `法人谈话笔录（普通工伤案件）.docx` | 法人姓名、法人性别、法人年龄、法人身份证号、法人身份证地址、法人手机号、法人职务、法人身份、本人姓名、单位名称、单位性质、当前时期、用户名 |
| `家属谈话笔录（普通工伤案件）.docx` | 家属姓名、家属性别、家属年龄、家属身份证号、家属身份证地址、家属手机号、家属单位名称、家属岗位、与死者关系、本人姓名、单位性质、当前时期、用户名 |

各角色的表头行（由 `patch_templates_2026.py` 写入，**改动必须同步改那个脚本**）：

```
本人：工作单位：{{单位名称}}      职务或岗位：{{本人岗位}}  单位性质：{{单位性质}}  身份：{{本人身份}}
证人：工作单位：{{单位名称}}      职务或岗位：{{证人岗位}}  单位性质：{{单位性质}}  身份：{{证人身份}}
法人：工作单位：{{单位名称}}      职务或岗位：{{法人职务}}  单位性质：{{单位性质}}  身份：{{法人身份}}
家属：工作单位：{{家属单位名称}}  职务或岗位：{{家属岗位}}  单位性质：{{单位性质}}  身份：{{与死者关系}}
```

> 谈话模板按**角色**硬编码在 `app_main.py:236-251`，一律用「普通工伤案件」那一份。
> `谈话模板 - 副本/` 里的多案型模板（上下班时/因工外出/工作前后/患职业病/暴力伤害）
> **目前未被任何代码引用**。

### 文书模板（`文书模板/`）

| 模板 | 占位符 |
|---|---|
| `工伤认定告知书（样本）.docx` | 本人姓名、本人身份证号、本人所属表述、用人单位、受伤经过、医疗证明、申请时间、受理时间、**认定依据句**、当前时期 |
| `不予工伤认定告知书（样本）.docx` | 同上（认定依据句改为不予认定口径） |
| `接受谈话通知书（样本）.docx` | 本人姓名、本人身份证号、用人单位、受伤经过、医疗证明、申请时间、受理时间、当前时期 |
| `工伤案件审批表（模板）.docx` | 本人姓名、本人性别、本人身份证号、用人单位、受伤经过、医疗结论、申请时间、受理时间、引用条例、申请人名称 |
| `发送给AI的模板.docx` | 案本号、案件性质、申请类型、本人姓名/性别/年龄/身份证号/身份证地址/手机号/岗位、用人单位、用工单位、工地名称、受伤经过、申请时间、受理时间、拟用条例、法律要件、已提供材料 |
| `局案审会议材料（样本）.docx` | 无占位符（非程序生成） |

**两个是计算出来的，不是原始字段**：

- `{{本人所属表述}}` ← `_person_affiliation()`：企业→`{单位}职工`；机关（公务员）/事业单位→`{单位}（机关）{身份}`
- `{{认定依据句}}` ← `_notice_basis_sentence()`：机关/事业单位会在条例名前加「参照」

---

## 四、提示词

`resource/prompts/*.txt`，可用记事本直接编辑，存盘即生效（无需重启）。
动态数据用 `{{...}}` 标记，由 `prompt_manager.py` 按调用方替换。
文件缺失时自动用 `prompt_manager.DEFAULT_PROMPTS` 重建；文件为空时回退默认值，不覆盖用户文件。
