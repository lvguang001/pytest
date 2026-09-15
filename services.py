"""services.py - merged backend services.

Public class names: DataService / FileService / TemplateVariableManager.
"""
import os
import subprocess
import datetime
import logging
import win32com.client
import pandas as pd
from typing import Any, Dict, List, Optional


# ============================================================================
# 统一"人记录"schema（本人/证人/法人/家属共用一份字段结构）
# ----------------------------------------------------------------------------
# 原先定义在 app_main.py 里，2026-09 搬到本模块——它描述的是数据模型约定，
# 跟具体界面无关。app_main 的 _person_to_flat / _person_from_flat 直接用它。
# ============================================================================

# 一份"人记录"的规范英文键。case_obj 顶层的 本人 即 role=本人 的记录；
# 证人 / 法人 数组元素 = 同样这些字段 + role(+ 可选 seq / materials)。
# unit = 该人自己的工作单位，各自独立（证人/家属不必与案件用人单位相同）。
PERSON_BASE_FIELDS = ("name", "gender", "age", "id_card", "address", "phone",
                      "position", "identity", "unit")

# canonical 英文键 → 中文后缀（用于拼 本人姓名/证人姓名/… 兼容扁平键）
PERSON_CN_SUFFIX = {
    "name": "姓名",
    "gender": "性别",
    "age": "年龄",
    "id_card": "身份证号",
    "address": "身份证地址",
    "phone": "手机号",
    "identity": "身份",
    "unit": "单位名称",
}
# position 语义随角色：扁平兼容键 本人岗位/证人岗位/法人职务/家属岗位
_FLAT_POSITION_SUFFIX = {"本人": "岗位", "证人": "岗位", "法人": "职务", "家属": "岗位"}

_CN_DIGITS = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九']


def person_flat_key(role: str, field: str) -> str:
    """canonical 字段 → 角色前缀中文兼容扁平键，如 ('本人','name')→'本人姓名'、('法人','position')→'法人职务'"""
    if field == "position":
        return f"{role}{_FLAT_POSITION_SUFFIX.get(role, '岗位')}"
    return f"{role}{PERSON_CN_SUFFIX.get(field, field)}"


def witness_seq_label(n: int) -> str:
    """把序号转成中文证人编号：1→证人一, 10→证人十, 11→证人十一, 21→证人二十一。

    n>=100 也认（一百、一百零一、一百一十、一百二十一）。一个案子当然不会有
    100 个证人，但这里原先写成 `_CN_DIGITS[n // 10]`，十位取到 10 就 IndexError
    ——纯属没兜住，顺手补上。再往上（>=1000）退回阿拉伯数字，不硬凑中文。
    """
    if n <= 0:
        return f"证人{n}"
    if n < 10:
        return f"证人{_CN_DIGITS[n]}"
    if n < 20:
        body = "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    elif n < 100:
        tens, ones = divmod(n, 10)
        body = _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")
    elif n < 1000:
        hundreds, rest = divmod(n, 100)
        body = _CN_DIGITS[hundreds] + "百"
        if rest == 0:
            return f"证人{body}"
        if rest < 10:
            return f"证人{body}零{_CN_DIGITS[rest]}"
        tens, ones = divmod(rest, 10)
        body += _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")
    else:
        return f"证人{n}"

    return f"证人{body}"


class DataService:
    """数据处理服务 - 负责所有数据计算、验证、转换操作"""

    def __init__(self):
        """初始化DataService"""
        pass

    @staticmethod
    def calculate_age_from_idcard(idcard: str) -> Optional[int]:
        """
        根据身份证号计算年龄
        :param idcard: 身份证号码（15位或18位）
        :return: 年龄（整数），如果身份证无效返回None
        """
        try:
            if not idcard or len(idcard) not in (15, 18):
                return None

            # 提取出生日期
            if len(idcard) == 18:
                birth_date_str = idcard[6:14]  # YYYYMMDD
            else:  # 15位身份证
                birth_date_str = f"19{idcard[6:12]}"  # 19YYMMDD

            # 转换为日期
            birth_date = datetime.datetime.strptime(birth_date_str, "%Y%m%d")

            # 计算年龄
            today = datetime.datetime.now()
            age = today.year - birth_date.year

            # 如果今年生日还没过，减1岁
            if (today.month, today.day) < (birth_date.month, birth_date.day):
                age -= 1

            return age

        except Exception as e:
            print(f"[DataService] 计算年龄失败: {e}")
            return None

    @staticmethod
    def extract_gender_from_idcard(idcard: str) -> Optional[str]:
        """
        根据身份证号提取性别
        :param idcard: 身份证号码（15位或18位）
        :return: "男" 或 "女"，如果身份证无效返回None
        """
        try:
            if not idcard:
                return None

            if len(idcard) == 18:
                gender_digit = int(idcard[16])  # 第17位
            elif len(idcard) == 15:
                gender_digit = int(idcard[14])  # 第15位
            else:
                return None

            # 奇数男性，偶数女性
            return "男" if gender_digit % 2 == 1 else "女"

        except Exception as e:
            print(f"[DataService] 提取性别失败: {e}")
            return None


class FileService:
    def __init__(self, base_path, logger=None):
        self.BASE_PATH = base_path
        self.logger = logger or self._create_default_logger()
        self.WPS_PATH = self.find_wps_path()
        self.logger.setLevel(logging.WARNING)

    def _create_default_logger(self):
        """创建默认日志器"""
        import logging
        logger = logging.getLogger('FileService')
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def open_document(self, file_path):
        """打开文档（统一使用这个方法）"""
        self.logger.info(f"打开文档: {file_path}")
        try:
            # 检查文件是否存在
            if not os.path.exists(file_path):
                self.logger.error(f"文件不存在: {file_path}")
                return False, "文件不存在"

            # 尝试用WPS打开
            if self.WPS_PATH and os.path.exists(self.WPS_PATH):
                try:
                    self.logger.info(f"尝试用WPS打开: {self.WPS_PATH}")
                    subprocess.Popen([self.WPS_PATH, file_path])
                    self.logger.info("WPS打开成功")
                    return True, "用WPS打开成功"
                except Exception as e:
                    self.logger.error(f"WPS打开失败: {e}")

            # 尝试用Word打开
            try:
                self.logger.info("尝试用Word打开")
                word_app = win32com.client.Dispatch("Word.Application")
                word_app.Visible = True
                word_app.Documents.Open(file_path)
                self.logger.info("Word打开成功")
                return True, "用Word打开成功"
            except Exception as e:
                self.logger.error(f"Word打开失败: {e}")

            # 尝试用系统默认程序打开
            try:
                self.logger.info("尝试用默认程序打开")
                os.startfile(file_path)
                self.logger.info("默认程序打开成功")
                return True, "用默认程序打开成功"
            except Exception as e:
                self.logger.error(f"默认程序打开失败: {e}")
                return False, "所有打开方式都失败"

        except Exception as e:
            self.logger.error(f"打开文档异常: {e}")
            return False, f"打开文件时发生错误: {str(e)}"

    def find_wps_path(self):
        """从原类复制，完全一样"""
        possible_paths = [
            r"C:\Program Files (x86)\Kingsoft\WPS Office\ksolaunch.exe",
            r"C:\Program Files\Kingsoft\WPS Office\ksolaunch.exe",
            os.path.join(os.environ.get('LOCALAPPDATA', ''), "Kingsoft", "WPS Office", "ksolaunch.exe"),
        ]

        for path in possible_paths:
            if os.path.exists(path):
                return path
        return None

    def save_to_excel(self, template_path, excel_filename, column_name, new_item, existing_items):
        """保存到Excel - 使用文书模板目录"""
        from path_utils import path_utils
        excel_path = path_utils.get_document_template_path(excel_filename)

        print(f"💾 保存Excel到: {excel_path}")

        try:
            if os.path.exists(excel_path):
                existing_df = pd.read_excel(excel_path)
                existing_list = existing_df[column_name].tolist()
                all_items = list(set(existing_list + [new_item] + existing_items))
                df = pd.DataFrame(all_items, columns=[column_name])
            else:
                all_items = list(set([new_item] + existing_items))
                df = pd.DataFrame(all_items, columns=[column_name])

            df.to_excel(excel_path, index=False)
            return df[column_name].tolist()

        except Exception as e:
            print(f"[FileService ERROR] 保存到Excel失败: {str(e)}")
            return existing_items


class TemplateVariableManager:
    """模板变量管理器"""

    def __init__(self, data_model):
        self.data = data_model
        self.variables_cache: Dict[str, Any] = {}
        self.introduction_cache: Dict[str, str] = {}

    def clear_cache(self):
        """清空所有缓存（切换证人/角色等数据变化时调用，避免命中旧数据）"""
        self.variables_cache.clear()
        self.introduction_cache.clear()


# ============================================================================
# 日期 / 时间显示格式
# ----------------------------------------------------------------------------
# 原先散在 app_main.py 顶部（2026-09 搬来）。和 service_flow 里那套是两回事：
# 那边统一 'YYYY-MM-DD' 用于期限计算，这里的中文格式是给文书和提示词看的。
# ============================================================================

def date_now() -> str:
    """当前日期，格式：2025年01月01日"""
    return datetime.datetime.now().strftime('%Y年%m月%d日')


def time_now() -> str:
    """当前时间，格式：14时30分"""
    return datetime.datetime.now().strftime('%H时%M分')


def timestamp_now() -> str:
    """时间戳，格式：20250101_143000"""
    return datetime.datetime.now().strftime('%Y%m%d_%H%M%S')


def format_compact_time(value: str) -> str:
    """把受伤/就诊时间的紧凑格式 YYYYMMDDHHMM 变成「2026年07月20日16时20分」。

    长度不是 12 位、或含非数字时原样返回（不猜、不截断）；空值返回空串。
    月/日/时/分补零，与 _resolve_date_input 处理 申请/受理时间 的口径一致。
    """
    s = str(value or '').strip()
    if len(s) != 12 or not s.isdigit():
        return s
    return (f"{s[0:4]}年{s[4:6]}月{s[6:8]}日{s[8:10]}时{s[10:12]}分")


# ============================================================================
# 案件数据模型
# ============================================================================

class CaseDataModel:
    """案件数据模型 - 统一管理所有案件数据"""

    def __init__(self):
        self.basic_info: Dict[str, Any] = {}  # 基础个人信息
        self.company_info: Dict[str, Any] = {}  # 公司相关信息
        self.case_info: Dict[str, Any] = {}  # 案件信息
        self.investigation: Dict[str, Any] = {}  # 调查信息
        self.output_config: Dict[str, Any] = {}  # 输出配置
        self.witnesses: List[Dict[str, Any]] = []  # 多证人数据，每项含 序号/姓名/身份证号/身份证地址/手机号/岗位/性别/年龄
        self.current_witness_index: int = -1  # 当前正在编辑的证人下标，-1 表示无
        self._init_default_values()

    def _init_default_values(self):
        """初始化默认值"""
        self.case_info.update({
            '案件性质': '工伤案件',
            '申请类型': '单位申请'
        })
        self.output_config.update({
            '当前日期': date_now(),
            '当前时间': time_now()
        })

    def to_template_dict(self) -> Dict[str, Any]:
        """转换为模板渲染用的字典"""
        template_dict = {}

        # 确保日期时间是最新的
        self.output_config.update({
            '当前日期': date_now(),
            '当前时间': time_now()
        })

        # 按优先级合并
        template_dict.update(self.basic_info)
        template_dict.update(self.company_info)
        template_dict.update(self.case_info)
        template_dict.update(self.investigation)
        template_dict.update(self.output_config)

        return template_dict

    def update_basic_info(self, role: str, data: Dict[str, Any]):
        """更新基础信息"""
        prefixed_data = {}
        for key, value in data.items():
            if not key.startswith(role):
                new_key = f"{role}{key}" if key != "姓名" else f"{role}姓名"
            else:
                new_key = key
            prefixed_data[new_key] = value

        self.basic_info.update(prefixed_data)

    def clear_role_data(self, role: str):
        """清除特定角色的数据"""
        role_prefix = role if role in ["本人", "证人", "法人", "家属"] else ""
        if not role_prefix:
            return

        keys_to_remove = [
            key for key in self.basic_info.keys()
            if key.startswith(role_prefix)
        ]

        for key in keys_to_remove:
            self.basic_info.pop(key, None)
