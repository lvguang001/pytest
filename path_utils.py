# path_utils.py
"""
统一的路径管理工具
保持向后兼容，不改变现有数据位置
"""
import os
import sys
from pathlib import Path


class PathUtils:
    """路径管理工具 - 单例模式"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return

        # 应用根目录（程序所在目录）
        self._determine_app_root()

        # 用户数据目录（保持现有位置）
        self._setup_user_dirs()

        self._initialized = True
        print(f"[OK] PathUtils初始化: app_root={self.app_root}")

    def _determine_app_root(self):
        """确定程序根目录"""
        if getattr(sys, 'frozen', False):
            # 打包后的exe
            self.app_root = Path(sys.executable).parent
        else:
            # 开发环境：main.py所在目录
            self.app_root = Path(__file__).parent

        # 确保目录存在
        self.app_root.mkdir(parents=True, exist_ok=True)

    def _setup_user_dirs(self):
        """设置用户目录（保持现有结构）"""
        # 1. 配置文件目录（保持当前目录）
        self.config_dir = self.app_root / "config"

        # 2. 数据文件目录（保持当前目录）
        self.data_dir = self.app_root

        # 3. 模板资源目录
        self.resource_dir = self.app_root / "resource"
        self.template_dir = self.resource_dir / "模板文件"

        # 4. 用户存储目录（取“屏幕上真实显示的桌面”，尊重桌面重定向，如 G:\桌面）
        desktop = self._get_shell_desktop()
        self.storage_dir = desktop / "工伤助手存储案本"

        # 确保所有目录存在
        for dir_path in [self.config_dir, self.resource_dir,
                         self.template_dir, self.storage_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _get_shell_desktop() -> Path:
        """获取系统“真实桌面”目录（跟随桌面位置重定向）。

        Windows 上很多机器把桌面挪到了其它盘/OneDrive（如 G:\\桌面），此时
        Path.home()/Desktop 指向的旧目录并不显示在屏幕上。这里用
        SHGetKnownFolderPath(FOLDERID_Desktop) 取真实路径；失败时退回
        Path.home()/Desktop。
        """
        if os.name == "nt":
            try:
                import ctypes
                import uuid
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", ctypes.c_ulong),
                                ("Data2", ctypes.c_ushort),
                                ("Data3", ctypes.c_ushort),
                                ("Data4", ctypes.c_ubyte * 8)]

                def _to_guid(s):
                    u = uuid.UUID(s)
                    g = _GUID()
                    g.Data1 = u.time_low
                    g.Data2 = u.time_mid
                    g.Data3 = u.time_hi_version
                    g.Data4 = (ctypes.c_ubyte * 8)(*u.bytes[8:])
                    return g

                FOLDERID_Desktop = _to_guid("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")
                func = ctypes.windll.shell32.SHGetKnownFolderPath
                func.argtypes = [ctypes.POINTER(_GUID), ctypes.c_ulong,
                                 wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
                buf = ctypes.c_wchar_p()
                hr = func(ctypes.byref(FOLDERID_Desktop), 0, None, ctypes.byref(buf))
                if hr == 0 and buf.value:
                    path = Path(buf.value)
                    try:
                        ctypes.windll.ole32.CoTaskMemFree(buf)
                    except Exception:
                        pass
                    if str(path).strip():
                        return path
            except Exception:
                pass
        return Path.home() / "Desktop"

    # ============ 主要路径获取方法 ============

    def get_template_path(self, *subpaths) -> Path:
        """获取模板路径"""
        return self.template_dir.joinpath(*subpaths)

    def get_storage_path(self, *subpaths) -> Path:
        """获取存储路径"""
        return self.storage_dir.joinpath(*subpaths)

    def get_config_path(self, *subpaths) -> Path:
        """获取配置路径"""
        return self.config_dir.joinpath(*subpaths)

    def get_data_path(self, *subpaths) -> Path:
        """获取数据路径"""
        return self.data_dir.joinpath(*subpaths)

    def get_talk_template_path(self, *subpaths) -> Path:
        """获取谈话模板路径"""
        return self.template_dir.joinpath("谈话模板", *subpaths)

    def get_document_template_path(self, *subpaths) -> Path:
        """获取文书模板路径"""
        return self.template_dir.joinpath("文书模板", *subpaths)

    def get_users_file(self) -> Path:
        """获取users_api.json路径（保持原位）"""
        return self.data_dir / "users_api.json"


# 全局单例实例
path_utils = PathUtils()