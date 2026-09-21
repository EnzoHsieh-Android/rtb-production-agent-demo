#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Enzo Hsieh
# SPDX-License-Identifier: MIT
"""五支 hook 共用的「我跑完了」事件寫入器(全 repo 審視 #19 [S4])。

★為什麼要有這個檔★:`lumos enforcement` 原本只驗「有沒有註冊在設定檔裡」,
答不出「它最近有沒有真的跑過」。而一個只驗註冊的判準,連「註冊成什麼樣子」都會判錯
——2026-09-08 實測:它把兩道每次推送都在跑的 git 閘印成 inactive。

★三件是設計審 r1 三席各自挖出來、逐條折進來的★:

1. **檔案放 repo 樹內,不放家目錄。** 原本寫 `~/.cache/lumos`,理由是「不是治理帳」
   ——r1 架構對齊席指出那個理由回答的是「這筆帳算哪一類」,不是「檔案該放哪裡」,兩件事。
   這個 repo 的慣例是產生物放 repo 樹內、`.gitignore` 蓋掉(`scripts/bin/`、
   `governance/.notifier/` 都是),碰家目錄只限 OS 逼你放那裡的東西。
   放 repo 樹內同時解掉跨 repo 污染:別的專案的事件進不來。

2. **只寫在成功完成點,不寫在進入點。** 逾時與例外各記成不同的 kind,★不得算成「跑過」★。
   同型前例就在隔壁:`Systems/hook逾時預算` 記的正是「寫在 except 分支裡的清理動作
   結構上永遠跑不到,而且沒人發現」。

3. **五支共用這一份,不要各寫各的。** 原子寫入、上限、例外處理漏一處就是一個獨立的坑,
   而且改格式要五處同步改。

★不記「注入 N 次」★:那是**成效**,而派工鏡頭那案的 d1 明令「不擋不驗不記不量」。
本批只做「活著沒」(跑過 / 逾時 / 失敗),不做「有沒有用」。這條分界不要順手加回去。
"""
import datetime
import json
import os
import time
from pathlib import Path

REL = "governance/runtime/hook-events.jsonl"
MAX_BYTES = 512 * 1024          # 上限:超過就從頭砍掉一半,不讓它無限長
KINDS = ("ok", "timeout", "error")


def _fingerprint(p: Path) -> str:
    """hook 檔自己的指紋——換版本要看得出來(判定式第②條)。"""
    import hashlib
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def record(repo_root, hook_name, kind, note="", hook_file=None):
    """寫一行。★全程 fail-open:寫不進去絕不影響 hook 自己的工作★。

    回傳 True/False 只是給測試看的;呼叫端不該因為它改變行為
    ——hook 的本業是它自己那件事,觀測壞掉不能讓本業停擺。
    """
    if kind not in KINDS:
        return False
    try:
        root = Path(repo_root)
        d = root / "governance" / "runtime"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "hook-events.jsonl"
        ev = {
            # ★不能用 time.strftime("%z")★(2026-09-08 r1 通才席 blocker,兩個版本實跑對照):
            # 它吐的偏移量沒有冒號(+0800),而讀側用 datetime.fromisoformat——
            # ★那在 Python 3.11 之前不吃無冒號格式★。實測 3.9 直接 ValueError,
            # 而 3.9 是這個 repo 的下限、3.10 是 Ubuntu 22.04 內建版本,不是冷門情境。
            # 後果是「兩秒前才成功記的一筆」被判成沒跑過 → 儀表板在那些機器上永久 stale,
            # 正好打臉這批的核心賣點。用 datetime 自己的 isoformat,兩邊同一套。
            "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "hook": hook_name,
            "kind": kind,
            "repo": str(root.resolve()),
            "fp": _fingerprint(Path(hook_file)) if hook_file else "",
        }
        if note:
            ev["note"] = str(note)[:200]
        line = json.dumps(ev, ensure_ascii=False) + "\n"
        # 原子寫入:單行 append 在 POSIX 上小於 PIPE_BUF 是原子的;
        # 這裡再加一道上限修剪,修剪走暫存→改名(這個 repo 對共用檔的既有教訓)。
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(line)
        try:
            if f.stat().st_size > MAX_BYTES:
                keep = f.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
                tmp = f.with_suffix(".jsonl.tmp")
                tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
                os.replace(tmp, f)
        except OSError:
            pass
        return True
    except Exception:
        return False


def _root_from_cwd():
    """★逾時砍到 3 秒★(r1 外家席 major):這支跑在每支 hook 的本業之前,
    正常態實測約 13 毫秒,但最壞態原本設 10 秒——跟 hook 的整體預算同量級,
    git 卡住時會先把預算吃完,讓本業根本沒機會跑。觀測絕不能排擠本業。"""
    import subprocess
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() or None
    except Exception:
        return None


_MARKED = {"kind": None, "note": ""}


def mark(kind, note=""):
    """hook 自己在「內部吞掉了逾時/失敗」的地方呼叫這一支(#19 r1 外家席 blocker)。

    ★為什麼需要它★:`guard()` 只看得到 `main()` 有沒有丟例外。
    而五支 hook 有好幾條「捕捉逾時 → 附一行說明 → 正常 return 0」的路徑
    (dispatch-lens 的 `except subprocess.TimeoutExpired` 就是),
    ★那種情況下 guard 會把它記成 ok——一支每次都逾時的 hook 會穩定顯示「跑過 N 次」★,
    正是這一批自己點名要防的假綠。而 KINDS 裡宣告的 timeout 原本沒有任何接線寫得到。

    所以吞掉的那一方要自己講一聲。沒講的才算 ok。
    """
    if kind in KINDS:
        _MARKED["kind"] = kind
        _MARKED["note"] = str(note)[:200]


def guard(hook_name, hook_file, main, swallow=False):
    """跑 main(),然後把結果記成一筆事件。★這是五支共用的那一份★。

    ★記在成功完成點★:main() 正常回來才記 `ok`;丟例外記 `error`。
    ★不在進入點記★——那會讓「每次都在起手式就當機」的 hook 穩定顯示「近期跑過 N 次」,
    正是這一批自己點名要防的假綠。

    swallow=True 的那兩支照它們原本的政策吞掉例外回 0(絕不擋 session 開場);
    其餘的照原樣把例外往上丟。★記帳絕不改變這個政策★——觀測壞掉不能讓本業停擺,
    本業壞掉也不能因為記了帳就假裝沒事。
    """
    root = _root_from_cwd()
    try:
        rc = main()
    except Exception as e:
        if root:
            if not record(root, hook_name, "error",
                          note=type(e).__name__ + ":" + str(e)[:120], hook_file=hook_file):
                print("  ⚠ telemetry-write-failed:hook 事件寫不進去",
                      file=__import__("sys").stderr)
        if swallow:
            return 0
        raise
    if root:
        # ★內部吞掉逾時/失敗時,記的是那個 kind,不是 ok★(見 mark 的說明)。
        _k = _MARKED["kind"] or "ok"
        ok = record(root, hook_name, _k, note=_MARKED["note"], hook_file=hook_file)
        if not ok:
            # ★S7 也要蓋到這本帳★(r1 外家席 major):原本 record 把寫入錯誤吞成 False
            # 而 guard 完全不看回傳值,所以磁碟滿/權限錯時 stderr 一個字都沒有,
            # 操作者只會看到 enforcement 慢慢變 stale,分不出是 hook 沒跑還是帳壞了。
            print("  ⚠ telemetry-write-failed:hook 事件寫不進去(hook 自己的工作不受影響)",
                  file=__import__("sys").stderr)
    return rc
