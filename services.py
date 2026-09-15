"""services.py - merged backend services.

Public class names: DataService / FileService / TemplateVariableManager.
"""
import os
import subprocess
import datetime
import logging
import win32com.client
import pandas as pd
from typing import Dict, Any, Optional


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
    """把序号转成中文证人编号：1→证人一, 10→证人十, 11→证人十一, 21→证人二十一"""
    if n <= 0:
        return f"证人{n}"

    if n <= 10:
        body = _CN_DIGITS[n] if n < 10 else "十"
    elif n < 20:
        body = "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    else:
        tens = n // 10
        ones = n % 10
        body = _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")

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
