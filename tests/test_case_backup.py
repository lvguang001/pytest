# -*- coding: utf-8 -*-
"""案件数据的版本校验、备份与原子写入（一案一文件布局）。

案卷是全程序唯一不可再生的东西：改坏了没有第二份来源。这里锁住这些保护——
版本不对要出声音、每次保存要留回滚点、写入过程崩了不能毁掉原文件、
单个案件的文件坏了不能连累别的案件。

磁盘布局：<BASE_PATH>/<案本号>/case.json（每个案件一份，与它生成的文书同处一个案卷文件夹）；
旧的「一个全库文件」cases_data.json 只在迁移时读，迁移后改名 .migrated 留底。
"""
import datetime
import json
import logging
import re

import pytest

import app_main
from app_main import is_newer_version, version_tuple


@pytest.fixture
def store(win, tmp_path, monkeypatch):
    """把窗口的数据目录指到临时目录，返回 (窗口, 数据目录)"""
    monkeypatch.setattr(win, "BASE_PATH", str(tmp_path))
    return win, tmp_path


def _year_of(case_id):
    """案本号里的立案年份；取不到用当前年。

    这里**故意按布局约定独立实现一遍**，不调 app_main 的 `_year_for_case`——
    抄 app 的实现就等于抄它的 bug，独立算才能把年份算错这种情况暴露出来。
    """
    m = (re.search(r'(?:案本|工亡)(20\d{2})\d{4}', case_id)
         or re.search(r'(20\d{2})\d{2}\d{2}', case_id))
    return m.group(1) if m else str(datetime.datetime.now().year)


def _case_file(root, case_id):
    """某个案件的数据文件路径：<BASE>/<年份>/<案本号>/case.json"""
    return root / _year_of(case_id) / case_id / "case.json"


def _bak(path):
    return path.parent / (path.name + ".bak")


def _legacy(root, version, cases):
    """写一个旧版「一个全库文件」，用于迁移相关用例"""
    p = root / "cases_data.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": version, "cases": cases}, ensure_ascii=False),
                 encoding="utf-8")
    return p


def _write_case_file(root, case_id, version, **block):
    """直接写一个案件文件（绕过 save，便于构造各种坏数据）"""
    f = _case_file(root, case_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"version": version, "case_id": case_id, **block},
                            ensure_ascii=False), encoding="utf-8")
    return f


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
# 一案一文件的基本往返
# ============================================================================

class Test一案一文件:

    def test_存两个案件落成两份文件(self, store):
        win, root = store
        win._save_cases_data({"甲案": {"case_id": "甲案", "name": "甲"},
                              "乙案": {"case_id": "乙案", "name": "乙"}})
        assert _case_file(root, "甲案").exists()
        assert _case_file(root, "乙案").exists()
        assert not (root / "cases_data.json").exists(), "不该再有全库文件"

    def test_读写往返一致(self, store):
        win, root = store
        cases = {"甲案": {"case_id": "甲案", "name": "甲", "materials": [{"name": "身份证"}]},
                 "乙案": {"case_id": "乙案", "name": "乙",
                          "family_reps": [{"name": "丙", "identity": "夫妻"}]}}
        win._save_cases_data(cases)
        back = win._load_cases_data()
        assert set(back) == {"甲案", "乙案"}
        assert back["甲案"]["name"] == "甲"
        assert back["乙案"]["family_reps"][0]["identity"] == "夫妻"

    def test_单个案件坏掉不连累其它案件(self, store, caplog):
        """这是从「一个全库文件」改成一案一文件要买的东西"""
        win, root = store
        win._save_cases_data({"好案": {"case_id": "好案", "name": "好好的"},
                              "坏案": {"case_id": "坏案", "name": "坏的"}})
        _case_file(root, "坏案").write_text("{ 这不是合法 JSON", encoding="utf-8")

        with caplog.at_level(logging.ERROR):
            cases = win._load_cases_data()

        assert cases["好案"]["name"] == "好好的", "好案件必须照常读出来"
        assert "坏案" not in cases, "坏掉的案件跳过即可"
        assert any("跳过读不出来的案件文件" in r.message for r in caplog.records)

    def test_目录名是定位依据(self, store, caplog):
        """文件里的 case_id 与目录名不一致时以目录名为准，并出个声"""
        win, root = store
        f = _case_file(root, "目录名案")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"version": "3.0", "case_id": "别的案本号",
                                 "case_info": {}, "injured_worker": {"name": "甲"}},
                                ensure_ascii=False), encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            cases = win._load_cases_data()
        assert list(cases) == ["目录名案"]
        assert cases["目录名案"]["case_id"] == "目录名案"
        assert any("不一致" in r.message for r in caplog.records)

    def test_案本号里的非法字符被安全化(self, store):
        win, root = store
        cid = "莫/言:案本2*0?2<6>|"
        win._save_cases_data({cid: {"case_id": cid, "name": "莫言"}})

        # 案卷目录在年份目录的下一层，名字里不该有 Windows 非法字符
        case_dirs = [p for year in root.iterdir() if year.is_dir() and year.name.isdigit()
                     for p in year.iterdir() if p.is_dir()]
        assert case_dirs, "应该建出了案卷目录"
        assert not set('\\/:*?"<>|') & set(case_dirs[0].name)

        # 读回来的键是「安全化后的目录名」——磁盘身份以目录名为准
        name = win._safe_case_dirname(cid)
        assert win._load_cases_data()[name]["name"] == "莫言"


# ============================================================================
# 年份目录（第二步）：<BASE>/<年份>/<案本号>/
# ============================================================================

class Test年份目录:
    """案卷按年份分层。年份取案本号里的立案日期——那本来就是立案时的系统时间。"""

    def test_案本号里的年份进目录(self, store):
        # 故意用一个跟「当前年」不同的年份，否则年份算错也看不出来
        win, root = store
        cid = "莫言-案本202409071111"
        win._save_cases_data({cid: {"case_id": cid, "name": "莫言"}})
        assert (root / "2024" / cid / "case.json").exists()

    def test_工亡案本号也认(self, store):
        win, root = store
        cid = "赵六-工亡202512313333"
        win._save_cases_data({cid: {"case_id": cid, "name": "赵六"}})
        assert (root / "2025" / cid / "case.json").exists()

    def test_取不到年份时用当前年份(self, store):
        win, root = store
        win._save_cases_data({"无名案": {"case_id": "无名案", "name": "甲"}})
        assert (root / str(datetime.datetime.now().year) / "无名案" / "case.json").exists()

    def test_案卷目录就是文书目录(self, store):
        """文书路径也走同一个 helper，否则数据和文书会分家"""
        win, root = store
        assert win._case_dir("莫言-案本202609071111") == str(root / "2026" / "莫言-案本202609071111")

    def test_老布局_无年份目录照样读得到且不被搬走(self, store):
        """分年份之前存的案卷留在原地——搬它会把同处一处的文书和数据分开"""
        win, root = store
        f = root / "老案" / "case.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"version": "3.0", "case_id": "老案",
                                 "case_info": {}, "injured_worker": {"name": "老"},
                                 "witnesses": [], "legal_reps": [], "family_reps": []},
                                ensure_ascii=False), encoding="utf-8")

        assert win._load_cases_data()["老案"]["name"] == "老"
        assert win._case_dir("老案") == str(root / "老案"), "老案卷的目录应保持原位"

        win._save_cases_data(win._load_cases_data())
        assert f.exists(), "老案卷被搬走了"
        assert not (root / str(datetime.datetime.now().year) / "老案").exists()

    def test_两种布局能一起读出来(self, store):
        win, root = store
        cid = "莫言-案本202609071111"
        win._save_cases_data({cid: {"case_id": cid, "name": "莫言"}})
        f = root / "老案" / "case.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"version": "3.0", "case_id": "老案", "case_info": {},
                                 "injured_worker": {"name": "老"},
                                 "witnesses": [], "legal_reps": [], "family_reps": []},
                                ensure_ascii=False), encoding="utf-8")

        assert set(win._load_cases_data()) == {cid, "老案"}


# ============================================================================
# 备份
# ============================================================================

class Test备份:

    def test_保存前生成bak(self, store):
        """写前把旧内容转存 .bak：等于「撤销上一次保存」的回滚点"""
        win, root = store
        win._save_cases_data({"c1": {"case_id": "c1", "name": "旧内容"}})
        win._save_cases_data({"c1": {"case_id": "c1", "name": "新内容"}})

        bak = _bak(_case_file(root, "c1"))
        assert bak.exists()
        assert "旧内容" in bak.read_text(encoding="utf-8")

    def test_每次保存都刷新bak(self, store):
        """回归：原先只在 .bak 不存在时备份一次，.bak 永远停在最早的版本"""
        win, root = store
        for v in ("第一版", "第二版", "第三版"):
            win._save_cases_data({"c1": {"case_id": "c1", "name": v}})

        assert "第二版" in _bak(_case_file(root, "c1")).read_text(encoding="utf-8"), \
            ".bak 应是「上一次保存前」的内容"

    def test_第一次保存没有旧文件不生成bak(self, store):
        win, root = store
        win._save_cases_data({"c1": {"case_id": "c1", "name": "甲"}})
        assert not _bak(_case_file(root, "c1")).exists(), "没有旧内容就没什么可备份的"

    def test_只有写失败的案件没被备份(self, store, monkeypatch):
        """某个案件写失败，不该动它自己的 .bak（旧内容仍是上一版）"""
        win, root = store
        win._save_cases_data({"c1": {"case_id": "c1", "name": "第一版"}})
        win._save_cases_data({"c1": {"case_id": "c1", "name": "第二版"}})

        def boom(*args, **kwargs):
            raise OSError("磁盘写满")

        monkeypatch.setattr(app_main.json, "dump", boom)
        assert win._save_cases_data({"c1": {"case_id": "c1", "name": "第三版"}}) is False
        assert "第一版" in _bak(_case_file(root, "c1")).read_text(encoding="utf-8")
        assert "第二版" in _case_file(root, "c1").read_text(encoding="utf-8")


# ============================================================================
# 旧数据的迁移（一个全库文件 → 一案一文件）
# ============================================================================

class Test迁移:

    def test_拆成一案一文件并留底(self, store):
        win, root = store
        legacy = _legacy(root, "3.0", {
            "甲案": {"case_id": "甲案", "name": "甲"},
            "乙案": {"case_id": "乙案", "name": "乙"},
        })

        cases = win._load_cases_data()

        assert set(cases) == {"甲案", "乙案"}
        assert _case_file(root, "甲案").exists()
        assert _case_file(root, "乙案").exists()
        assert (root / "cases_data.json.migrated").exists(), "原文件要留底，便于退回旧版"
        assert not legacy.exists(), "原文件要改名，别留着继续当数据源"

    def test_版本非当前时另留v2原档(self, store):
        """旧版程序只能读全库文件，所以升级前的原档要原样留一份"""
        win, root = store
        _legacy(root, "2.0", {"c1": {"case_id": "c1", "name": "老张"}})

        assert win._load_cases_data()["c1"]["name"] == "老张"

        bak = root / "cases_data.json.v2.bak"
        assert bak.exists()
        assert json.loads(bak.read_text(encoding="utf-8"))["version"] == "2.0"
        assert "老张" in json.dumps(json.loads(bak.read_text(encoding="utf-8")),
                                    ensure_ascii=False)

    def test_v2原档不会被后续保存覆盖(self, store):
        win, root = store
        _legacy(root, "2.0", {"c1": {"case_id": "c1", "name": "老张"}})
        win._load_cases_data()
        win._save_cases_data({"c1": {"case_id": "c1", "name": "改过了"}})

        bak = json.loads((root / "cases_data.json.v2.bak").read_text(encoding="utf-8"))
        assert bak["version"] == "2.0"
        assert "老张" in json.dumps(bak, ensure_ascii=False)

    def test_迁移幂等_再启动不重复迁移(self, store, monkeypatch):
        win, root = store
        _legacy(root, "2.0", {"c1": {"case_id": "c1", "name": "老张"}})
        win._load_cases_data()
        first = _case_file(root, "c1").read_text(encoding="utf-8")

        # 再读一次（相当于再启动一次程序）：原档已改名 .migrated，不该再写任何案件文件
        writes = []
        monkeypatch.setattr(win, '_write_case_file',
                            lambda *a, **k: writes.append(a) or '')
        assert win._load_cases_data()["c1"]["name"] == "老张"
        assert writes == [], "原文件已改名留底，不该再迁移一次"
        assert _case_file(root, "c1").read_text(encoding="utf-8") == first
        assert not (root / "cases_data.json").exists()

    def test_已存在的案件文件不被旧档覆盖(self, store):
        """迁移中途失败过、或程序已经写过新数据时，别拿旧档把它盖回去"""
        win, root = store
        win._save_cases_data({"c1": {"case_id": "c1", "name": "新内容"}})
        _legacy(root, "2.0", {"c1": {"case_id": "c1", "name": "旧内容"},
                              "c2": {"case_id": "c2", "name": "另一个"}})

        cases = win._load_cases_data()

        assert cases["c1"]["name"] == "新内容", "已存在的案件文件被旧档盖掉了"
        assert cases["c2"]["name"] == "另一个", "缺的那个要从旧档补上"
        assert not (root / "cases_data.json").exists(), "补完就该把原档改名留底"

    def test_中途失败不动原文件(self, store, monkeypatch):
        """迁移写盘失败时原文件必须保持不动，下次启动还能重试"""
        win, root = store
        legacy = _legacy(root, "3.0", {"c1": {"case_id": "c1", "name": "老张"}})

        def boom(*args, **kwargs):
            raise OSError("磁盘写满")

        monkeypatch.setattr(app_main.json, "dump", boom)
        win._load_cases_data()

        assert legacy.exists(), "迁移失败不能把原文件弄没了"
        assert not (root / "cases_data.json.migrated").exists()
        assert not _case_file(root, "c1").exists()

    def test_旧文件结构异常时跳过迁移(self, store, caplog):
        win, root = store
        p = root / "cases_data.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": "3.0", "cases": []}), encoding="utf-8")
        with caplog.at_level(logging.ERROR):
            assert win._load_cases_data() == {}
        assert any("结构异常" in r.message for r in caplog.records)
        assert p.exists(), "结构不对就别动它"


# ============================================================================
# 写全量的语义：磁盘上有、这次没保存的案件数据会被删掉
# ============================================================================

class Test删除语义:

    def test_没保存的案件数据文件被删(self, store):
        win, root = store
        win._save_cases_data({"甲案": {"case_id": "甲案", "name": "甲"},
                              "乙案": {"case_id": "乙案", "name": "乙"}})
        win._save_cases_data({"甲案": {"case_id": "甲案", "name": "甲"}})

        assert _case_file(root, "甲案").exists()
        assert not _case_file(root, "乙案").exists(), "乙案不在这一份里，数据文件该删"
        assert not _bak(_case_file(root, "乙案")).exists(), "备份也一起清掉"

    def test_只删数据不动案卷里的文书(self, store):
        """文书是办案成果，不该因为数据里没这个案子就被清掉"""
        win, root = store
        cid = "乙案-案本202609071111"
        win._save_cases_data({cid: {"case_id": cid, "name": "乙"}})
        doc = _case_file(root, cid).parent / "乙本人谈话笔录.docx"
        doc.write_bytes(b"fake docx")

        win._save_cases_data({"甲案-案本202609071111": {"case_id": "甲案-案本202609071111",
                                                        "name": "甲"}})

        assert not _case_file(root, cid).exists()
        assert doc.exists(), "案卷文件夹和里面的文书都不该动"


# ============================================================================
# 每日快照（仍是「一份全库」）
# ============================================================================

class Test每日快照:

    def _snaps(self, root):
        d = root / "backups"
        return sorted(d.glob("cases_data_*.json")) if d.exists() else []

    def test_生成当日快照(self, store):
        win, root = store
        win._save_cases_data({"c1": {"case_id": "c1", "name": "甲"}})
        assert len(self._snaps(root)) == 1

    def test_快照里含全部案件(self, store):
        """一案一文件之后，快照仍要是一份能整体回滚的全库"""
        win, root = store
        win._save_cases_data({"甲案": {"case_id": "甲案", "name": "甲"},
                              "乙案": {"case_id": "乙案", "name": "乙"}})
        data = json.loads(self._snaps(root)[0].read_text(encoding="utf-8"))
        assert data["version"] == app_main.SCHEMA_VERSION
        assert set(data["cases"]) == {"甲案", "乙案"}

    def test_同一天不重复快照(self, store):
        win, root = store
        for i in range(3):
            win._save_cases_data({"c1": {"case_id": "c1", "name": f"第{i}版"}})
        assert len(self._snaps(root)) == 1

    def test_快照保留上限(self, store, monkeypatch):
        win, root = store
        monkeypatch.setattr(app_main, "_SNAPSHOT_KEEP", 3)
        backup_dir = root / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for d in ("20260101", "20260102", "20260103", "20260104", "20260105"):
            (backup_dir / f"cases_data_{d}.json").write_text("{}", encoding="utf-8")

        win._save_cases_data({"c1": {"case_id": "c1"}})

        names = [f.name for f in self._snaps(root)]
        assert len(names) == 3
        assert "cases_data_20260105.json" in names, "应保留最新的"
        assert "cases_data_20260101.json" not in names, "应清掉最老的"


# ============================================================================
# 写入安全
# ============================================================================

class Test写入安全:

    def test_写入中途失败不毁原文件(self, store, monkeypatch):
        """直接以 'w' 打开会立刻截断原文件；写到一半失败，这个案件就没了"""
        win, root = store
        win._save_cases_data({"c1": {"case_id": "c1", "name": "原有内容"}})

        def boom(*args, **kwargs):
            raise OSError("磁盘写满")

        monkeypatch.setattr(app_main.json, "dump", boom)

        assert win._save_cases_data({"c1": {"case_id": "c1", "name": "新内容"}}) is False
        assert "原有内容" in _case_file(root, "c1").read_text(encoding="utf-8"), \
            "写失败不能毁掉原文件"

    def test_原子写入不留临时文件(self, store):
        win, root = store
        win._save_cases_data({"c1": {"case_id": "c1", "name": "甲"}})
        f = _case_file(root, "c1")
        assert f.exists()
        assert not (f.parent / (f.name + ".tmp")).exists()

    def test_保存后版本号正确(self, store):
        win, root = store
        win._save_cases_data({"c1": {"case_id": "c1"}})
        assert json.loads(_case_file(root, "c1").read_text(encoding="utf-8"))["version"] \
            == app_main.SCHEMA_VERSION

    def test_磁盘结构符合分块约定(self, store):
        win, root = store
        win._save_cases_data({"c1": {"case_id": "c1", "name": "甲",
                                     "family_reps": [{"name": "乙", "identity": "夫妻"}]}})
        blk = json.loads(_case_file(root, "c1").read_text(encoding="utf-8"))
        assert blk["injured_worker"]["name"] == "甲"
        assert blk["family_reps"][0]["identity"] == "夫妻"


# ============================================================================
# 版本与结构的噪声
# ============================================================================

class Test加载异常出声:

    def test_更高版本记错误日志(self, store, caplog):
        win, root = store
        _write_case_file(root, "c1", "9.0", case_info={},
                         injured_worker={"name": "未来"}, witnesses=[],
                         legal_reps=[], family_reps=[])
        with caplog.at_level(logging.ERROR):
            cases = win._load_cases_data()
        assert cases["c1"]["name"] == "未来", "仍要能读出来，不能让用户看不到案件"
        assert any("高于本程序支持" in r.message for r in caplog.records)

    def test_同级或更低版本不报警(self, store, caplog):
        win, root = store
        _write_case_file(root, "c1", "2.0", case_info={},
                         injured_worker={"name": "老"}, witnesses=[],
                         legal_reps=[], family_reps=[])
        with caplog.at_level(logging.ERROR):
            win._load_cases_data()
        assert not [r for r in caplog.records if "高于本程序支持" in r.message]

    def test_案件文件损坏不让程序崩(self, store, caplog):
        win, root = store
        f = _case_file(root, "c1")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{ 这不是合法 JSON", encoding="utf-8")
        with caplog.at_level(logging.ERROR):
            assert win._load_cases_data() == {}

    def test_peek_version只读开头(self, store):
        win, root = store
        f = _write_case_file(root, "c1", "3.0", case_info={}, injured_worker={},
                             witnesses=[], legal_reps=[], family_reps=[])
        assert win._peek_version(str(f)) == "3.0"

    def test_peek_version读不到时返回空(self, tmp_path):
        assert app_main.MainWindow._peek_version(str(tmp_path / "不存在.json")) == ""

    def test_peek_version乱填时返回空(self, store):
        win, root = store
        f = _case_file(root, "c1")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{"version": 30, "cases": {}}', encoding="utf-8")
        assert win._peek_version(str(f)) == ""
