#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Enzo Hsieh
# SPDX-License-Identifier: MIT
# MIT licensed. Full text: scripts/lumos header, or LICENSE at
# https://github.com/EnzoHsieh-Android/Lumos
"""dispatch-lens-hook — Claude Code PreToolUse(matcher Agent)薄殼(Projects/派工鏡頭注入_計劃,2026-09-03);
Codex 側掛 SubagentStart(Projects/Codex完全支援_計劃 d3,2026-09-04):叫 `lumos dispatch-lens --claim` 領一席 → additionalContext。

Claude 路徑只做三件事:①派工詞裡逐行找 `LUMOS-IMPACT: <base>..<head>` ②subprocess 叫 `lumos dispatch-lens`
③把回傳文字接在派工詞尾端,經 updatedInput 送給子代理(additionalContext 實測到不了子代理)。
其餘判斷(範圍文法、base 主線可達、消毒、快取)全在 lumos 端。
永不 deny、永不改 permissionDecision;失敗一律放行。★2026-09-05 起超時不再靜默★:附一行固定超時句進派工詞(Codex 走 additionalContext);其他失敗仍靜默(LUMOS_HOOK_DEBUG=1 才印 stderr)。
本檔在 ANCHOR_FILES 內:改它要 `lumos anchor approve --note`。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

MARKER_RE = re.compile(r"^LUMOS-IMPACT:\s*(\S+)\s*$")
SPEC_RE = re.compile(r"^LUMOS-SPEC:\s*(\S+)\s*$")   # 設計審用:給計劃筆記路徑(2026-09-05 第二輪審視 d2)
# ★2026-09-07 改寫(全 repo 審視 #14)★:舊文字教人「派工前先手跑一次暖快取」——
# 那件事現在機器自己做了(超時不再殺子行程,它會繼續算完寫進快取)。
# ★刻意不寫「下一席大概率就有」★:那是機率宣稱,而這個專案的規矩是機率宣稱要附
# 回頭看的條件、要有量測。目前沒有量過「超時後下一席命中率」,所以只講機制事實
# (那支還在背景算),不講機率。
# REVISIT:2026-12-07 量一次「超時之後、同範圍下一席的快取命中率」;
#   命中率低就表示放手不殺沒買到東西,那時該改回殺掉或另找解法
TIMEOUT_NOTE = ("LUMOS-LENS:鏡頭超時,這次沒附節點({what})。"
                "算到一半的那支沒有被砍掉,它會繼續把結果算完寫進快取(20 分鐘內有效),"
                "所以同一個範圍的下一席有機會直接拿到、不必再等。"
                "想現在就看算不算得出來:`lumos dispatch-lens {cmd}`"
                "({n} 個 commit 以上約 25 秒起)。")

# ★認領席位那條路要用不同的說明★(2026-09-07 代碼審 r1 通才席抓到)
# 上面那句在講「背景會把快取算完」——但認領走的是完全不同的機制:它只是從派工前
# 就武裝好的檔案裡原子領一席,**全程不寫任何快取**。對這條路講快取是每次都錯,
# 不是邊界情況。真正該講的是:這次沒領到,要重新武裝。
# ★設計審那條路也要用不同的說明★(2026-09-07 代碼審 r2 通才席抓到)
# 上面那句在講「背景會把快取算完」,但設計審模式(LUMOS-SPEC:)★本來就不快取★
# (計劃常改,快取會給過期答案),所以也沒有背景暖快取這回事——超時就是這次沒有,
# 下一次還是要重算。對這條路講快取是每次都錯。
SPEC_TIMEOUT_NOTE = ("LUMOS-LENS:鏡頭超時,這次沒附節點({what})。"
                     "★設計審這條路不快取★(計劃常改,快取會給過期答案),"
                     "所以沒有背景暖快取——下一次還是要重算。"
                     "想自己看算不算得出來:`lumos dispatch-lens --spec {what}`。")

CLAIM_TIMEOUT_NOTE = ("LUMOS-LENS:領席超時,這次沒附節點(Codex 席:--claim)。"
                      "★這條路不會有背景暖快取★——認領是從派工前武裝好的檔案裡領一席,"
                      "沒領到就是沒領到。編排者:重新武裝再派 "
                      "`lumos dispatch-lens --arm <base>..<head> --seats N`"
                      "(--status 只看剩幾席、不重算)。")
# ★2026-09-07 改成從天花板算★:原本寫死 45(外層 60 的 75%),兩邊各自演化就會漂。
def _lens_timeout():
    return _inner_budget(default=60)


# ── ★內層逾時一律從外層天花板算出來,不寫死★ ──────────────────────────────
# 單源說明在 Systems/hook逾時預算;這段在幾支 hook 裡是逐字相同的複本,有守衛盯著不准漂
# (hook 是獨立檔、複製到全域後彼此 import 不到)。
#
# 出身(2026-09-07 全 repo 審視 #14 量出來的):外層天花板寫在註冊表、內層逾時寫在各 hook,
# 兩邊各自演化。五支裡三支違反自家「外要明顯大於內」的規則,其中一支內層是外層的 2.5 倍。
#
# ★內層 ≥ 外層代表什麼★:內層那條「逾時就 fail-open」的分支**結構上永遠跑不到**——
# 外面會先把整支 hook 砍掉(SIGKILL,繞過 try/except)。影響鏡頭那支的 fail-open 分支裡
# 有「把冷卻窗記號清掉」,跑不到就表示超時之後那個檔被鎖住 20 分鐘完全不注入,而沒人知道。
# **一個為了 fail-open 而寫的分支,自己被 fail-closed 掉了。**
#
# 現在:天花板由註冊表一處生成,用 --budget 傳進來;內層一律取「天花板 × 0.7 減掉已耗」,
# 留 30% 給 hook 自己的啟動、收尾與寫輸出。拿不到 --budget(舊註冊還沒更新)就用保守預設。
_BUDGET_RATIO = 0.7
_BUDGET_FLOOR = 1.0          # 再怎麼扣也留 1 秒,不要算出 0 或負數
_BUDGET_START = None         # 這支 hook 開始跑的時刻(第一次用到時記)


def _outer_budget(default=10.0):
    """從 argv 讀 --budget <秒>;沒有就回 default(保守值,不是猜大的)。
    ★上限夾住★:異常大的值會讓內層跟著失控放大,等於整個 fail-open 形同虛設。"""
    import sys as _s
    argv = _s.argv
    got = None
    for i, a in enumerate(argv):
        if a == "--budget" and i + 1 < len(argv):
            try:
                got = float(argv[i + 1])
            except ValueError:
                got = None
            break
        if a.startswith("--budget="):
            try:
                got = float(a.split("=", 1)[1])
            except ValueError:
                got = None
            break
    if got is None or got <= 0:
        got = default
    return min(float(got), 600.0)


def _inner_budget(elapsed=None, default=10.0):
    """內層某一段還能用幾秒:天花板 × 0.7 − 這支 hook 到目前為止已經花掉的時間,下限 1 秒。

    ★已耗時間自己算,不靠呼叫端記得傳★(2026-09-07 代碼審 r1 通才席抓到):
    第一版的 elapsed 預設 0,而六個呼叫點裡只有一個真的傳了值——於是同一支 hook
    只要依序呼叫兩次,理論上限就是 2 × 0.7 × 外層 = 1.4 倍外層,**結構性地超過天花板**。
    實測:進場提醒那支 7+7=14 秒 vs 外層 10;CI 狀態那支 10.5+10.5=21 秒 vs 外層 15。
    ★這正是這批改動宣稱要修掉的問題,只是換個地方重新發生。★

    ★★這是「還剩多少」不是「每段配額」★★——很容易誤解,所以講清楚:
    回傳的是「從現在到預算用完還有幾秒」。所以連續呼叫兩次拿到的兩個數字**不該相加**:
    第一次拿到 7 秒、真的用掉 5 秒之後,第二次會拿到 2 秒。
    只有在「第一段其實沒用多久」時第二次才會拿到接近 7 秒——那也是對的,因為時間真的還在。
    ★守衛要驗的是「真的用掉時間之後,下一次拿到的會變少,而且總和不超過天花板」★,
    不是「兩次的數字相加小於天花板」(那個判準本身就錯,會逼出錯誤的修法)。

    ★誠實邊界★:天花板小於約 1.43 秒時,下限 1 秒會反過來大於外層。目前註冊表最小值是 10,
    離這個門檻很遠;真要調到那麼小的話這個假設就不成立了。
    """
    import time as _tm
    global _BUDGET_START
    if _BUDGET_START is None:
        _BUDGET_START = _tm.monotonic()
    used = (_tm.monotonic() - _BUDGET_START) if elapsed is None else float(elapsed)
    return max(_BUDGET_FLOOR, _outer_budget(default) * _BUDGET_RATIO - used)
# ── ★逾時預算結束★ ──────────────────────────────────────────────────

def _debug(msg: str) -> None:
    if os.environ.get("LUMOS_HOOK_DEBUG"):
        print(f"[dispatch-lens-hook] {msg}", file=sys.stderr)


def find_marker(prompt: str) -> str | None:
    for line in prompt.split("\n"):
        m = MARKER_RE.match(line.strip())
        if m:
            return m.group(1)
    return None


def find_spec_marker(prompt: str) -> str | None:
    for line in prompt.split("\n"):
        m = SPEC_RE.match(line.strip())
        if m:
            return m.group(1)
    return None


def _emit_updated(tool_input: dict, prompt: str, text: str) -> None:
    new_input = dict(tool_input)
    new_input["prompt"] = prompt.rstrip("\n") + "\n\n" + text + "\n"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": new_input}}, ensure_ascii=False))


# ── ★只執行「可信來源」的 lumos,絕不執行被打開那個資料夾裡的碼★ ──────────────
# 單源說明在 Systems/hook信任邊界;這段在幾支 hook 裡是逐字相同的複本,
# 有守衛測試盯著不准漂(hook 是獨立檔、複製到 ~/.claude/hooks 後彼此 import 不到,
# 所以用「複製 + 守衛」而不是抽共用模組)。
#
# 出身(2026-09-06 實地重現):這支 hook 原本執行的是 `<被打開的資料夾>/scripts/lumos`,
# 唯一判準是那個資料夾有 docs/*-knowledge。**clone 一個陌生 repo、開一下 Claude,
# 對方的 python 就在你機器上跑了**——而且因為是拿 python 去執行它,
# 那個檔連執行權限都不需要。
#
# 解析順序:系統裝好的 → $LUMOS_HOME 指的 → 預設來源位置 → 都沒有就回 None。
# 回 None 時呼叫端要靜默跳過那段功能(這套本來就是 fail-open:寧可少一層提醒,
# 不可執行不該信任的碼)。
#
# ★2026-09-07 外家審查席補的一刀★:光是「從 PATH 或 $LUMOS_HOME 找到」不算可信——
# 那兩個都是繼承來的環境值,workspace-local 的 bin(direnv / node_modules/.bin 之類)
# 一進專案就可能改掉 PATH。所以找到之後還要驗那個檔本身:不是 symlink 指到別處、
# 是自己的、group/other 不可寫。
# ★誠實邊界★:完全控制你 PATH 的人本來就能在你帳號下跑任何東西,這條擋不住那種;
# 它擋的是「專案順手塞一個 bin 進 PATH」與「別人可寫的目錄裡放一支同名的」。
def _trusted_lumos():
    import shutil as _sh, os as _os, stat as _st
    from pathlib import Path as _P

    def _ok(cand):
        try:
            p = _P(cand)
            if not p.is_file():
                return None
            st = p.stat()
            if hasattr(_os, "getuid") and st.st_uid != _os.getuid():
                return None          # 不是自己的檔
            if st.st_mode & (_st.S_IWGRP | _st.S_IWOTH):
                return None          # 別人可寫 = 別人可換內容
            par = p.parent.stat()
            if hasattr(_os, "getuid") and par.st_uid != _os.getuid():
                return None          # 放在別人的目錄裡
            if par.st_mode & (_st.S_IWGRP | _st.S_IWOTH):
                return None          # 目錄別人可寫 = 可被換掉
            return str(p)
        except OSError:
            return None

    found = _sh.which("lumos")
    if found:
        good = _ok(found)
        if good:
            return good
    for base in (_os.environ.get("LUMOS_HOME"), str(_P.home() / "harness" / "lumos-toolchain")):
        if not base:
            continue
        good = _ok(_P(base) / "scripts" / "lumos")
        if good:
            return good
    return None
# ── ★可信來源解析結束★ ────────────────────────────────────────────────

def _find_lumos_script() -> str | None:
    """★2026-09-07 外家審查席抓到:這支是第四支,前一批漏了★
    原本 which 找不到就退回「這支 hook 檔往上四層」的 scripts/lumos——這支 hook 會被複製進
    消費專案,那時往上四層正好是**消費專案自己的根**,等於又回到「執行手邊資料夾的碼」。
    前一批只改了三支、測試也只掃那三支,所以沒抓到。現在改用共用的可信來源解析。"""
    return _trusted_lumos()





def _claim_codex_seat(payload: dict) -> int:
    """Codex 側(SubagentStart):派工訊息對 hook 是密文、改不了(實驗 A),改由 `lumos dispatch-lens --claim`
    從派工前武裝的 armed 檔原子領一席,經 additionalContext 給子代理(實驗 5 證到得了)。
    沒武裝/過期/領完 → 什麼都不回(lumos 端判,這裡只轉送)。"""
    repo = payload.get("cwd", "") or os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not repo:
        _debug("Codex SubagentStart 沒有 cwd,放行")
        return 0
    lumos = _find_lumos_script()
    if lumos is None:
        _debug("找不到 lumos,放行")
        return 0
    # ★薄殼★:叫 lumos、等它、拿結果。超時之後怎麼辦是 lumos 那邊的事
    #   (2026-09-07 代碼審 r1 架構席:hook 只做三件事,其餘住 lumos 子命令)。
    try:
        r = subprocess.run([sys.executable, lumos, "dispatch-lens", "--claim", "--repo", repo, "--json"],
                           capture_output=True, text=True, timeout=_lens_timeout())
    except (subprocess.TimeoutExpired, OSError):
        r = None
    if r is None or r.returncode == 5:
        # 架構 r1 C:與 Claude 分支同語意——超時不再靜默,經 additionalContext 給一行固定說明
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": _frame_injected(CLAIM_TIMEOUT_NOTE)}}, ensure_ascii=False))
        _debug("lumos dispatch-lens --claim 超時,已附超時說明(這條路沒有背景暖快取)")
        return 0

    if r.returncode != 0:
        _debug(f"lumos dispatch-lens --claim rc={r.returncode}:{r.stderr.strip()[:200]},放行")
        return 0
    try:
        data = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        _debug("--claim 回傳讀不懂,放行")
        return 0
    text = data.get("text") if isinstance(data, dict) else None
    if not text:
        _debug(f"Codex 沒領到席({data.get('reason') if isinstance(data, dict) else '?'})")
        return 0
    # 這段 text 是 lumos dispatch-lens 產的,框已經加在那邊(單源:框跟內容同一個地方組)。
    # 這裡不重複框——重複會變成框中框,反而讓邊界更難讀。
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SubagentStart",
                                             "additionalContext": text}},   # framed-upstream
                     ensure_ascii=False))
    return 0


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    if payload.get("hook_event_name") == "SubagentStart":
        return _claim_codex_seat(payload)
    if payload.get("tool_name") != "Agent":
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return 0
    rng = find_marker(prompt)
    spec = find_spec_marker(prompt) if rng is None else None
    if rng is None and spec is None:
        return 0
    repo = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd", "")
    if not repo:
        _debug("沒有 CLAUDE_PROJECT_DIR 也沒有 cwd,放行")
        return 0
    lumos = _find_lumos_script()
    if lumos is None:
        _debug("找不到 lumos,放行")
        return 0
    argv = [sys.executable, lumos, "dispatch-lens"] + ([rng] if rng else ["--spec", spec]) + ["--repo", repo, "--json"]
    # ★薄殼★:帶 --deadline 叫 lumos;它自己會在超時時派一個脫離的行程把快取算完,
    #   rc5 = 「這次沒算完,但背景還在算」。hook 只負責把說明接進派工詞。
    # ★不要取整★:內層預算可能是 0.x 秒(天花板小、或已經耗掉不少),取整會變 0
    #   → lumos 那邊當成「沒給 deadline」或立刻超時,每次都走超時那條路。
    #   (第一版寫 int(),假環境算出 0.7 秒被取成 0,測試當場翻紅。)
    # ★下限不能訂太小★:光是起一個 python 行程就要 0.4 秒起跳(實測小專案整趟 0.46 秒),
    #   下限 0.5 秒等於「每次都超時」,連一瞬間算得完的小專案都被推去走背景那條路。
    #   訂 3 秒:夠小專案跑完,對大範圍又遠小於天花板、該超時的照樣超時。
    # ★deadline 與外層 timeout 都必須留在天花板之內★(2026-09-07 代碼審 r2 兩席都提):
    #   第一版寫 max(3.0, 內層預算),而外層 subprocess timeout 是「內層預算 + 5」——
    #   兩個數字都可能超過天花板:①天花板調小時 0.7×天花板+5 會大於天花板
    #   ②已耗很多時內層剩 1 秒,卻被下限抬成 3 秒。那正是這批要修的「內層 ≥ 外層」。
    #   現在:兩個都夾在「天花板 × 0.9」之內,下限也不許超過它。
    _cap = _outer_budget(default=60) * 0.9
    _dl = min(max(3.0, _lens_timeout()), _cap)
    _run_tmo = min(_dl + 5, _cap)
    argv = argv + (["--deadline", f"{_dl:.2f}"] if rng else [])
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=_run_tmo)
    except (subprocess.TimeoutExpired, OSError):
        r = None
    if r is None or r.returncode == 5:
        # 2026-09-05 第二輪審視 d1:超時不再靜默——今天 39 次派工 21 次放空,編排者完全不知道。附一行固定句(零自由文字)。
        what = rng or spec
        # 兩條路的機制不同,說明也要不同(r2 通才席:共用那句對設計審是假話)
        _note = (TIMEOUT_NOTE.format(what=what, cmd=rng, n=10) if rng
                 else SPEC_TIMEOUT_NOTE.format(what=what))
        _emit_updated(tool_input, prompt, _note)
        _debug("lumos dispatch-lens 超時,已附超時說明行(那支仍在背景把快取算完)")
        # ★吞掉逾時的地方要自己講一聲★(#19 r1 外家否決席 blocker):
        # 這一條是「捕捉逾時 → 附說明 → 正常 return 0」,而 guard() 只看得到有沒有丟例外,
        # 所以不講的話這次會被記成「成功跑完」——一支每次都逾時的 hook 會穩定顯示
        # 「近期跑過 N 次」,正是這一批要防的假綠。
        try:
            import sys as _s2, pathlib as _p2
            _s2.path.insert(0, str(_p2.Path(__file__).resolve().parent))
            from _hookevent import mark as _mark
            _mark("timeout", "lumos dispatch-lens 逾時,附了說明行")
        except Exception:
            pass
        return 0

    if r.returncode != 0:
        _debug(f"lumos dispatch-lens rc={r.returncode}:{r.stderr.strip()[:200]},放行")
        return 0
    try:
        data = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        _debug("回傳讀不懂,放行")
        return 0
    text = data.get("text") if isinstance(data, dict) else None
    if not text:
        _debug(f"固定席 0 篇(pinned={data.get('pinned') if isinstance(data, dict) else '?'}),不注入")
        return 0
    _emit_updated(tool_input, prompt, text)
    _debug(f"已附 {data.get('shown')} 篇(固定席 {data.get('pinned')},主線 {data.get('mainline', data.get('mode'))})")
    return 0


if __name__ == "__main__":
    # #19 S4:把「我跑完了」記成一筆事件,讓 lumos enforcement 答得出
    # 「它最近有沒有真的跑過」,而不是只答「有沒有註冊」。共用寫入器在 _hookevent.py。
    import sys as _s, pathlib as _p
    _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
    try:
        from _hookevent import guard as _guard
    except Exception:
        _guard = None
    sys.exit(_guard("pretooluse-dispatch-lens-hook", __file__, main) if _guard else main())
