# -*- coding: utf-8 -*-
"""案件数据的版本校验、备份与原子写入。

案卷是全程序唯一不可再生的东西：改坏了没有第二份来源。这里锁住三层保护——
版本不对要出声音、每次保存要留回滚点、写入过程崩了不能毁掉原文件。
"""
import json
import logging

import pytest

import app_main
from app_main import is_newer_version, version_tuple


@pytest.fixture
def store(win, tmp_path, monkeypatch):
    """把窗口的数据目录指到临时目录，返回 (窗口, cases_data.json 路径)"""
    monkeypatch.setattr(win, "BASE_PATH", str(tmp_path))
    return win, tmp_path / "cases_data.json"


def _write(path, version, cases):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": version, "cases": cases}, ensure_ascii=False),
                    encoding="utf-8")


def _bak(path):
    return path.parent / (path.name + ".bak")


# ============================================================================
# 版本号解析与比较（纯函数）
# ============================================================================

class Test版本比较:

    @pytest.mark.parametrize("text,expect", [
        ("3.0", (3, 0)), ("2.0", (2, 0)), ("10.1", (10, 1)), ("3", (3,)),
    ])
    def test_可解析(self, text, expect):
        assert version_tuple(text) == expect

    @pytest.mark.parametrize("bad", ["", None, "abc", "3.x", []])
    def test_无法解析返回None(self, bad):
        assert version_tuple(bad) is None

    @pytest.mark.parametrize("disk,expect", [
        ("4.0", True), ("10.0", True), ("3.1", True),
        ("3.0", False), ("2.0", False), ("", False), (None, False), ("乱填", False),
    ])
    def test_是否更新版(self, disk, expect):
        assert is_newer_version(disk) is expect

    def test_按数值比较而非字符串(self):
        """'10.0' 按字符串比 '3.0' 小，按版本号应更大"""
        assert is_newer_version("10.0") is True


# ============================================================================
# 备份
# ============================================================================

class Test备份:

    def test_保存后生成bak(self, store):
        win, path = store
        _write(path, "3.0", {"c1": {"case_id": "c1", "name": "旧内容"}})
        win._save_cases_data({"c2": {"case_id": "c2", "name": "新内容"}})
        assert _bak(path).exists()
        assert "旧内容" in _bak(path).read_text(encoding="utf-8")

    def test_每次保存都刷新bak(self, store):
        """回归：原先只在 .bak 不存在时备份一次，.bak 永远停在最早的版本"""
        win, path = store
        _write(path, "3.0", {"c1": {"case_id": "c1", "name": "第一版"}})
        win._save_cases_data({"c1": {"case_id": "c1", "name": "第二版"}})
        win._save_cases_data({"c1": {"case_id": "c1", "name": "第三版"}})

        assert "第二版" in _bak(path).read_text(encoding="utf-8"), \
            ".bak 应是「上一次保存前」的内容"

    def test_升级前的老档单独留一份(self, store):
        win, path = store
        _write(path, "2.0", {"c1": {"case_id": "c1", "name": "老张"}})
        win._save_cases_data({"c1": {"case_id": "c1", "name": "老张"}})

        legacy = path.parent / (path.name + ".v2.bak")
        assert legacy.exists(), "v2 原档要单独留一份，便于退回旧版程序"
        assert json.loads(legacy.read_text(encoding="utf-8"))["version"] == "2.0"

    def test_v2原档不会被后续保存覆盖(self, store):
        win, path = store
        _write(path, "2.0", {"c1": {"case_id": "c1", "name": "老张"}})
        win._save_cases_data({"c1": {"case_id": "c1", "name": "老张"}})
        win._save_cases_data({"c1": {"case_id": "c1", "name": "改过了"}})

        legacy = json.loads((path.parent / (path.name + ".v2.bak")).read_text(encoding="utf-8"))
        assert legacy["version"] == "2.0"
        assert "老张" in json.dumps(legacy, ensure_ascii=False)

    def test_文件不存在时不报错(self, store):
        win, path = store
        assert win._save_cases_data({"c1": {"case_id": "c1"}}) is True
        assert path.exists()


# ============================================================================
# 每日快照
# ============================================================================

class Test每日快照:

    def _snaps(self, path):
        d = path.parent / "backups"
        return sorted(d.glob("cases_data_*.json")) if d.exists() else []

    def test_生成当日快照(self, store):
        win, path = store
        _write(path, "3.0", {"c1": {"case_id": "c1", "name": "甲"}})
        win._save_cases_data({"c1": {"case_id": "c1", "name": "甲"}})
        assert len(self._snaps(path)) == 1

    def test_同一天不重复快照(self, store):
        win, path = store
        _write(path, "3.0", {"c1": {"case_id": "c1", "name": "甲"}})
        for i in range(3):
            win._save_cases_data({"c1": {"case_id": "c1", "name": f"第{i}版"}})
        assert len(self._snaps(path)) == 1

    def test_快照保留上限(self, store, monkeypatch):
        win, path = store
        monkeypatch.setattr(app_main, "_SNAPSHOT_KEEP", 3)
        backup_dir = path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for d in ("20260101", "20260102", "20260103", "20260104", "20260105"):
            (backup_dir / f"cases_data_{d}.json").write_text("{}", encoding="utf-8")

        _write(path, "3.0", {"c1": {"case_id": "c1"}})
        win._save_cases_data({"c1": {"case_id": "c1"}})

        names = [f.name for f in self._snaps(path)]
        assert len(names) == 3
        assert "cases_data_20260105.json" in names, "应保留最新的"
        assert "cases_data_20260101.json" not in names, "应清掉最老的"


# ============================================================================
# 写入安全
# ============================================================================

class Test写入安全:

    def test_写入中途失败不毁原文件(self, store, monkeypatch):
        """直接以 'w' 打开会立刻截断原文件；写到一半失败，整份案卷就没了"""
        win, path = store
        _write(path, "3.0", {"c1": {"case_id": "c1", "name": "原有内容"}})

        def boom(*args, **kwargs):
            raise OSError("磁盘写满")

        monkeypatch.setattr(app_main.json, "dump", boom)

        assert win._save_cases_data({"c1": {"case_id": "c1", "name": "新内容"}}) is False
        assert "原有内容" in path.read_text(encoding="utf-8"), "写失败不能毁掉原文件"

    def test_原子写入不留临时文件(self, store):
        win, path = store
        win._save_cases_data({"c1": {"case_id": "c1", "name": "甲"}})
        assert path.exists()
        assert not (path.parent / (path.name + ".tmp")).exists()

    def test_保存后版本号正确(self, store):
        win, path = store
        win._save_cases_data({"c1": {"case_id": "c1"}})
        assert json.loads(path.read_text(encoding="utf-8"))["version"] == app_main.SCHEMA_VERSION

    def test_加载写入的数据结构正确(self, store):
        win, path = store
        win._save_cases_data({"c1": {"case_id": "c1", "name": "甲",
                                     "family_reps": [{"name": "乙", "identity": "夫妻"}]}})
        blk = json.loads(path.read_text(encoding="utf-8"))["cases"]["c1"]
        assert blk["injured_worker"]["name"] == "甲"
        assert blk["family_reps"][0]["identity"] == "夫妻"


# ============================================================================
# 版本与结构的噪声
# ============================================================================

class Test加载异常出声:

    def test_更高版本记错误日志(self, store, caplog):
        win, path = store
        _write(path, "9.0", {"c1": {"case_id": "c1", "name": "未来"}})
        with caplog.at_level(logging.ERROR):
            cases = win._load_cases_data()
        assert cases["c1"]["name"] == "未来", "仍要能读出来，不能让用户看不到案件"
        assert any("高于本程序支持" in r.message for r in caplog.records)

    def test_同级或更低版本不报警(self, store, caplog):
        win, path = store
        _write(path, "2.0", {"c1": {"case_id": "c1", "name": "老"}})
        with caplog.at_level(logging.ERROR):
            win._load_cases_data()
        assert not [r for r in caplog.records if "高于本程序支持" in r.message]

    def test_顶层结构异常记错误且返回空(self, store, caplog):
        win, path = store
        path.write_text(json.dumps({"version": "3.0", "cases": []}), encoding="utf-8")
        with caplog.at_level(logging.ERROR):
            assert win._load_cases_data() == {}
        assert any("结构异常" in r.message for r in caplog.records)

    def test_文件损坏不让程序崩(self, store, caplog):
        win, path = store
        path.write_text("{ 这不是合法 JSON", encoding="utf-8")
        with caplog.at_level(logging.ERROR):
            assert win._load_cases_data() == {}

    def test_peek_version只读开头(self, store):
        win, path = store
        _write(path, "3.0", {"c1": {"case_id": "c1"}})
        assert win._peek_version(str(path)) == "3.0"

    def test_peek_version读不到时返回空(self, tmp_path):
        assert app_main.MainWindow._peek_version(str(tmp_path / "不存在.json")) == ""

    def test_peek_version乱填时返回空(self, store):
        win, path = store
        path.write_text('{"version": 30, "cases": {}}', encoding="utf-8")
        assert win._peek_version(str(path)) == ""
