# -*- coding: utf-8 -*-
"""service_flow.py —— 「个人申请」工伤案件的文书送达待办状态引擎（纯逻辑，无 Qt 依赖）。

设计原则：持久化的只有"事实"（当前文书、送达方式、寄送/送达时间、操作日志），
"当前应显示哪条待办 / 剩余几天 / 是否到期"一律由 derive() 依据今天日期重算，
因此天然满足「每天自动更新、启动即重算、到期自动进入下一流程」。

环节：举证通知书(proof) → 工伤认定告知书(notice) → 工伤认定决定书(decision)。
倒计时唯一起点是「送达时间」；「寄送时间」仅邮寄方式记录，不影响倒计时。
"""

import re
import copy
import datetime as _dt

# ---------------------------------------------------------------------------
# 送达方式与期限规则
# ---------------------------------------------------------------------------
METHODS = ("邮寄", "现场", "公告", "留置")

# 举证通知书期限（送达时间 + N 天）：公告=25（10 公告期 + 15 举证期），其余 15
PROOF_DAYS = {"现场": 15, "邮寄": 15, "公告": 25, "留置": 15}
# 工伤认定告知书期限：公告=13（10 公告期 + 3 日），其余 3 日
NOTICE_DAYS = {"现场": 3, "邮寄": 3, "公告": 13, "留置": 3}

DOC_DAYS = {
    "proof": PROOF_DAYS,     # 举证通知书
    "notice": NOTICE_DAYS,   # 工伤认定告知书
}

DOC_LABEL = {
    "proof": "举证通知书",
    "notice": "工伤认定告知书",
}

# stage.status
S_ASK = "ask_send"      # 尚未记录送达时间（待选方式/待录时间）
S_COUNTING = "counting"  # 已记录送达时间，倒计时中（到期与否由 derive 判定）

# derive() 返回的 phase 全集
PHASES = (
    "none", "done",
    "proof_confirm", "proof_counting", "proof_expired",
    "notice_ask", "notice_counting",
    "decision_ask",
)


# ---------------------------------------------------------------------------
# 日期工具（统一为 'YYYY-MM-DD' 文本存储/计算）
# ---------------------------------------------------------------------------
def today_iso() -> str:
    return _dt.date.today().isoformat()


def _to_date(s: str) -> _dt.date:
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", str(s or "").strip())
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _dt.date(y, mo, d)
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", str(s or "").strip())
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _dt.date(y, mo, d)
    m = re.match(r"^(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})日?$", str(s or "").strip())
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _dt.date(y, mo, d)
    raise ValueError(f"无法解析日期: {s!r}（支持 2026-09-07 / 20260907 / 2026年9月7日）")


def normalize_date(s: str) -> str:
    """把各种常见输入归一为 'YYYY-MM-DD'；解析失败抛 ValueError。"""
    return _to_date(s).isoformat()


def add_days(iso_date: str, n: int) -> str:
    return (_to_date(iso_date) + _dt.timedelta(days=int(n))).isoformat()


def days_between(iso_early: str, iso_late: str) -> int:
    """late - early 的天数（可为负）。"""
    return (_to_date(iso_late) - _to_date(iso_early)).days


def deadline_iso(deliver_time: str, doc: str, method: str) -> str:
    """期限日 = 送达时间 + 该文书/方式的期限天数。"""
    days = DOC_DAYS.get(doc, {}).get(method, 15)
    return add_days(deliver_time, days)


def _now_str() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 持久化结构
# ---------------------------------------------------------------------------
def initial_sf(case_id: str = "", role: str = "") -> dict:
    """新建流程：从「举证通知书待确认发出」开始。"""
    return {
        "created_at": _now_str(),
        "case_id": case_id,
        "created_by_role": role,
        "stage": {
            "doc": "proof",
            "status": S_ASK,
            "method": "",
            "send_time": "",
            "deliver_time": "",
        },
        "attempts": [],
        "logs": [f"{_now_str()} 已建卡：{case_id} 举证通知书待确认送达"],
        "done": False,
        "done_at": "",
    }


# ---------------------------------------------------------------------------
# 迁移：每次动作 produce 新 sf（copy.deepcopy，不改入参），只追加 attempts/logs
# ---------------------------------------------------------------------------
def confirm_delivery(sf: dict, doc: str, method: str, deliver_time: str,
                     send_time: str = "", note: str = "", at: str = None) -> dict:
    """送达确认保存：记录方式与送达时间；送达时间为空则只记方式、不开始倒计时
    （回到待确认状态，下次打开时预填该方式）。doc 决定写回哪个环节。"""
    at = at or _now_str()
    ns = copy.deepcopy(sf)
    stage = ns.setdefault("stage", {})
    # 送达时间选填：填了才进入倒计时
    dt = normalize_date(deliver_time) if str(deliver_time or "").strip() else ""
    counting = bool(dt)
    stage.update({
        "doc": doc,
        "status": S_COUNTING if counting else S_ASK,
        "method": method,
        "deliver_time": dt,
        # 寄送时间仅邮寄记录；非邮寄清空以免误读
        "send_time": normalize_date(send_time) if method == "邮寄" and send_time else "",
    })
    ns["attempts"].append({
        "at": at, "doc": doc, "action": "confirm",
        "method": method, "send_time": stage["send_time"],
        "deliver_time": dt, "note": note or "",
    })
    if counting:
        tail = f"送达确认：{DOC_LABEL.get(doc, doc)}【{method}】送达时间 {dt}"
        if stage["send_time"]:
            tail += f"，寄送时间 {stage['send_time']}"
    else:
        tail = f"送达确认：{DOC_LABEL.get(doc, doc)}已选择【{method}】，送达时间待定"
    ns["logs"].append(f"{at} {tail}")
    return ns


def revert_to_ask(sf: dict, action: str = "resend", note: str = "",
                  at: str = None) -> dict:
    """邮寄追踪 ①②：未送达 → 退回「送达确认第一步」（重选方式/重录时间）。
    action: 'resend' 再次寄送（保留邮寄便于预填）/ 'switch' 改用其它方式（清空方式）。"""
    at = at or _now_str()
    action = action if action in ("resend", "switch") else "resend"
    ns = copy.deepcopy(sf)
    stage = ns.setdefault("stage", {})
    stage["status"] = S_ASK
    stage["deliver_time"] = ""
    if action == "switch":
        stage["method"] = ""
        stage["send_time"] = ""
    ns["attempts"].append({
        "at": at, "doc": stage.get("doc", "proof"), "action": action,
        "method": stage.get("method", ""), "send_time": stage.get("send_time", ""),
        "deliver_time": "", "note": note or "",
    })
    msg = "未送达，再次寄送" if action == "resend" else "未送达，改用其它方式"
    ns["logs"].append(f"{at} {msg}，退回送达确认。" + (f"备注：{note}" if note else ""))
    return ns


def delivered_again(sf: dict, new_deliver_time: str, note: str = "",
                    at: str = None) -> dict:
    """邮寄追踪 ③：已送达 → 以新的送达时间为倒计时起点。"""
    at = at or _now_str()
    ns = copy.deepcopy(sf)
    stage = ns.setdefault("stage", {})
    stage["status"] = S_COUNTING
    stage["deliver_time"] = normalize_date(new_deliver_time)
    ns["attempts"].append({
        "at": at, "doc": stage.get("doc", "proof"), "action": "delivered_again",
        "method": stage.get("method", ""), "send_time": stage.get("send_time", ""),
        "deliver_time": stage["deliver_time"], "note": note or "",
    })
    ns["logs"].append(
        f"{at} 邮寄确认已送达，送达时间更新为 {stage['deliver_time']}。"
        + (f"备注：{note}" if note else "")
    )
    return ns


def finish_flow(sf: dict, at: str = None) -> dict:
    at = at or _now_str()
    ns = copy.deepcopy(sf)
    ns["done"] = True
    ns["done_at"] = at
    ns.setdefault("stage", {}).update({"status": "done"})
    ns["logs"].append(f"{at} 流程结束（制作工伤认定决定书 / 案件审批表）。")
    return ns


# ---------------------------------------------------------------------------
# 派生：给定 sf 与今天，算出应显示的任务
# ---------------------------------------------------------------------------
def derive(sf: dict, today: str = None) -> dict:
    """返回 {"phase","label","remaining","action","doc","prefill","method"}。
    action ∈ {'delivery_confirm','postal_tracking','decision', None}
    doc  ∈ {'proof','notice'} 表示 delivery_confirm/postal_tracking 作用于哪个文书。"""
    empty = {"phase": "none", "label": "", "remaining": None,
             "action": None, "doc": "", "prefill": False, "method": ""}
    if not sf:
        return empty
    if sf.get("done"):
        empty["phase"] = "done"
        return empty

    stage = sf.get("stage") or {}
    doc = stage.get("doc", "proof") or "proof"
    method = stage.get("method", "") or ""
    deliver = str(stage.get("deliver_time", "") or "").strip()
    cid = str(sf.get("case_id", "") or "").strip()

    # 尚未记录送达时间 → 待确认环节（若已选过送达方式则预填）
    if not deliver:
        m = method or ""
        prefill = bool(m)
        if doc == "proof":
            return {
                "phase": "proof_confirm",
                "label": f"{cid}的举证通知书是否已经发出？",
                "remaining": None, "action": "delivery_confirm",
                "doc": "proof", "prefill": prefill, "method": m,
            }
        # notice：由举证到期进入 / 或告知书邮寄退回后再次确认
        return {
            "phase": "notice_ask",
            "label": f"{cid}的举证通知书已经到期，是否送达工伤认定告知书？",
            "remaining": None, "action": "delivery_confirm",
            "doc": "notice", "prefill": prefill, "method": m,
        }

    # 已记录送达时间 → 计算剩余
    try:
        dl = deadline_iso(deliver, doc, method)
        rem = days_between(today or today_iso(), dl)
    except ValueError:
        # 送达时间/方式异常（旧数据容错）：当作待确认处理
        return dict(empty, phase="proof_confirm" if doc == "proof" else "notice_ask",
                    label=(f"{cid}的举证通知书是否已经发出？"
                           if doc == "proof" else
                           f"{cid}的举证通知书已经到期，是否送达工伤认定告知书？"),
                    action="delivery_confirm", doc=doc, prefill=False)

    # —— 未到期（含期限到日当天 remaining=0）——
    if rem >= 0:
        if doc == "proof":
            return {
                "phase": "proof_counting",
                "label": f"{cid}的举证通知书（{method}）已送达，倒计时进行中（剩余：{rem}天）",
                "remaining": rem, "action": ("postal_tracking" if method == "邮寄"
                                             else "delivery_confirm"),
                "doc": "proof",
                "prefill": method != "邮寄", "method": method,
            }
        return {
            "phase": "notice_counting",
            "label": f"{cid}工伤认定告知书已送达，倒计时进行中（剩余：{rem}天）",
            "remaining": rem, "action": ("postal_tracking" if method == "邮寄"
                                         else "delivery_confirm"),
            "doc": "notice",
            "prefill": method != "邮寄", "method": method,
        }

    # —— 已过期限 ——
    if doc == "proof":
        # 举证期满 → 询问是否送达工伤认定告知书
        return {
            "phase": "proof_expired",
            "label": f"{cid}的举证通知书已经到期，是否送达工伤认定告知书？",
            "remaining": rem, "action": "delivery_confirm",
            "doc": "notice", "prefill": False, "method": method,
        }
    # 告知书期满后的次日 → 询问是否制作工伤认定决定书
    return {
        "phase": "decision_ask",
        "label": f"{cid}工伤认定告知书已经送达，是否制作工伤认定决定书？",
        "remaining": rem, "action": "decision",
        "doc": "notice", "prefill": False, "method": method,
    }
