"""Phase 15 增量 2:規則模式探索的模型回覆解析與機械核對(計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈模型建議、機械核對與人讀報告〉)。

合約 [S1504] [S1505]。純離線:不呼叫任何模型、不開閘道;回覆是手寫的假回覆,探索集是固定種子
15001 的合成歷史或手造小歷史。
"""

import dataclasses
import json
import sys
import threading

import pytest

from rtb.eval import rule_mining_baseline as rb
from rtb.eval import rule_mining_check as rc
from rtb.eval import rule_mining_history as rh
from rtb.eval import rule_mining_prompt as rp
from rtb.eval import rule_mining_vocab as rv
from tests.eval.test_rule_mining import RAISE_2, _ad, _history

SEED = rv.SEEDS[0]
RAISE_BAND_2 = [{"condition": "raise_pct", "threshold": "raise_pct:band_2"}]


@pytest.fixture(scope="module")
def sides():
    """種子 15001 的兩側重算器與彙總(整個檔只生成一次)。"""
    history = rh.generate(SEED)
    split = rh.split(a.ad_id for a in history.ads)
    return (history, split, rc.Recounter(history, split.explore),
            rc.Recounter(history, split.holdout), rb.summarize(history, split.explore))


def _clauses(key):
    return [{"condition": c, "threshold": t} for c, t in key]


def _s(clauses=None, direction="improve", support=24, counter=6, note="待核對", **extra):
    item = {"clauses": RAISE_BAND_2 if clauses is None else clauses, "direction": direction,
            "support": support, "counterexample": counter, "confidence_note": note}
    return {**item, **extra}


def _reply(*suggestions, **top):
    """緊湊 JSON、寫原字;內容有孤立代理字元時改寫成 \\uXXXX 逸出(模型只能這樣送出它)。"""
    body = {"version": 1, "suggestions": list(suggestions), **top}
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(body, separators=(",", ":"))
    return text


def _normalized(ranked):
    return ranked.key, ranked.direction


def _honest(ranked):
    """照探索側重算數字填的一條建議(模型照抄彙總表時的樣子)。"""
    return _s(_clauses(ranked.key), ranked.direction, ranked.support, ranked.counter)


# ---- [S1504] ----
def test_rule_mining_rejects_out_of_vocabulary_suggestions(sides):  # noqa: PLR0915 - 逐種情境
    _, _, explore, holdout, summary = sides
    top = rb.top_k(summary.stats)
    good = _honest(top[0])

    def verified(text):
        return rc.verify(rc.parse_reply(text), explore, holdout)

    baseline = verified(_reply(good))
    assert (baseline.rejected, baseline.n, baseline.invalid_count) == (None, 1, 0)

    # 整份拒絕:記 1 筆無效提交、0 條有效,不截前 K、不讓命令列崩潰
    whole = {
        rc.DUPLICATE_KEY: ['{"version":1,"version":1,"suggestions":[]}',
                           _reply(good).replace('"direction":', '"direction":"improve",'
                                                '"direction":', 1),
                           _reply(good).replace('"condition":', '"condition":"x","condition":', 1)],
        rc.UNKNOWN_KEY: [_reply(good, rationale="x"), _reply(_s(rationale="x")),
                         _reply(_s([{**RAISE_BAND_2[0], "rationale": "因為"}]))],
        rc.NON_FINITE: [_reply(good).replace(f'"support":{top[0].support}', '"support":1e400'),
                        _reply(good).replace(f'"support":{top[0].support}', '"support":NaN'),
                        _reply(good).replace(f'"support":{top[0].support}',
                                             '"support":-Infinity'),
                        _reply(good).replace('"version":1', '"version":Infinity')],
        rc.UNPARSABLE: [_reply(good).replace(f'"support":{top[0].support}',
                                             '"support":' + "9" * 4301),
                        "```json\n" + _reply(good) + "\n```", "", "[" * 10],
        rc.TOO_DEEP: ["[" * 5000, "[" * 65 + "]" * 65],
        rc.TOO_MANY: [_reply(*[_honest(r) for r in top] + [good])],
        rc.TOO_LARGE: [_reply(good) + " " * (rv.REPLY_BYTES_LIMIT - len(_reply(good)) + 1)],
        rc.WRONG_VERSION: [_reply(good, version=2), _reply(good, version=True),
                           _reply(good, version="1"), _reply(good).replace('"version":1',
                                                                           '"version":1.0')],
        rc.NOT_OBJECT: ["[]", '"x"'],
        rc.MISSING_KEY: ['{"version":1}'],
        rc.NOT_LIST: ['{"version":1,"suggestions":{}}'],
    }
    for reason, texts in whole.items():
        for text in texts:
            result = verified(text)
            assert (result.rejected, result.n, result.invalid_count) == (reason, 0, 1), (
                reason, text[:120])
            assert rc.reasons(result) == {reason: 1}
    # 4300 位整數解析得了,但筆數超過九位:只丟該條
    long_ok = verified(_reply(good, _s(support=int("9" * 4300))))
    assert long_ok.rejected is None and long_ok.n == 1
    assert [(i.index, i.reason) for i in long_ok.invalid] == [(1, rc.BAD_COUNT)]
    assert rc.MAX_INT_DIGITS == 4300
    # 分界不看直譯器的整數位數設定:設成不限(0)或最小(640)都一樣(代碼審 r1)
    too_long = _reply(good).replace(f'"support":{top[0].support}', '"support":-' + "9" * 4301)
    ok_long = _reply(good, _s(support=int("9" * 4300)))
    negative_ok = _reply(good, _s(support=-int("9" * 4300)))  # 負號不算位數(代碼審 r2)
    for limit in (0, 640, 4300):
        before = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(limit)
        try:
            assert rc.parse_reply(too_long).rejected == rc.UNPARSABLE, limit
            parsed = rc.parse_reply(ok_long)
            assert parsed.rejected is None and len(parsed.accepted) == 1, limit
            assert [(d.index, d.reason) for d in parsed.dropped] == [(1, rc.BAD_COUNT)], limit
            parsed = rc.parse_reply(negative_ok)
            assert parsed.rejected is None and len(parsed.accepted) == 1, limit
            assert [(d.index, d.reason) for d in parsed.dropped] == [(1, rc.BAD_COUNT)], limit
        finally:
            sys.set_int_max_str_digits(before)
    # 位元組照實際 JSON 逸出後量:同一份內容寫原字過、寫 \uXXXX 就超過 6000
    full = [_s(_clauses(r.key), r.direction, 0, 0, "中" * 80) for r in top]
    raw = json.dumps({"version": 1, "suggestions": full}, ensure_ascii=False,
                     separators=(",", ":"))
    escaped = json.dumps({"version": 1, "suggestions": full}, separators=(",", ":"))
    assert len(raw.encode()) <= rv.REPLY_BYTES_LIMIT < len(escaped.encode())
    assert rc.parse_reply(raw).rejected is None
    assert rc.parse_reply(escaped).rejected == rc.TOO_LARGE
    # 界量的是 UTF-8 位元組不是字數:6001 位元組但不到 6000 字,照樣整份拒絕
    padded = raw + " " * (rv.REPLY_BYTES_LIMIT + 1 - len(raw.encode()))
    assert len(padded) < rv.REPLY_BYTES_LIMIT < len(padded.encode())
    assert rc.parse_reply(padded).rejected == rc.TOO_LARGE
    assert rc.parse_reply(padded[:-1]).rejected is None

    # 只丟該條:記原因、計無效提交(AI 誤報),其餘照常
    single = [
        (_s(support=True), rc.BAD_COUNT), (_s(counter=6.0), rc.BAD_COUNT),
        (_s(support=-1), rc.BAD_COUNT), (_s(support=10**9), rc.BAD_COUNT),
        (_s([{"condition": "raise_pct", "threshold": "raise_pct:band_9"}]),
         "threshold_not_in_condition"),
        (_s([{"condition": "raise_pct", "threshold": "pre_cvr:band_1"}]),
         "threshold_not_in_condition"),
        (_s([{"condition": "ctr", "threshold": "ctr:band_1"}]), "unknown_condition"),
        (_s([{"condition": "Raise_pct", "threshold": "raise_pct:band_2"}]), "bad_code"),
        (_s([{"condition": "raise_pct", "threshold": "r" * 25}]), "bad_code"),
        (_s([{"condition": 1, "threshold": "raise_pct:band_2"}]), "bad_code"),
        (_s([]), "clause_count"),
        (_s([*RAISE_BAND_2, {"condition": "pre_cvr", "threshold": "pre_cvr:band_1"},
             {"condition": "day_type", "threshold": "day_type:weekday"}]),
         "clause_count"),
        (_s([*RAISE_BAND_2, {"condition": "raise_pct", "threshold": "raise_pct:band_3"}]),
         "duplicate_condition"),
        (_s(direction="up"), "unknown_direction"), (_s(direction=None), "unknown_direction"),
        (_s(note="字" * 81), rc.BAD_NOTE), (_s(note="😀" * 61), rc.BAD_NOTE),
        (_s(note='含"引號'), rc.BAD_NOTE), (_s(note="反\\斜線"), rc.BAD_NOTE),
        (_s(note="換\n行"), rc.BAD_NOTE), (_s(note=3), rc.BAD_NOTE),
        # 孤立代理字元(模型寫成 \ud800 逸出)與格式字元只丟該條,不讓解析器崩潰(代碼審 r1)
        (_s(note="\ud800"), rc.BAD_NOTE), (_s(note="a\udfffb"), rc.BAD_NOTE),
        (_s(note="零\u200b寬"), rc.BAD_NOTE), (_s(note="\u200f方向"), rc.BAD_NOTE),
        ("不是物件", rc.BAD_SUGGESTION),
        ({k: v for k, v in _s().items() if k != "confidence_note"}, rc.MISSING_FIELD),
        (_s(clauses="raise_pct:band_2"), rc.BAD_CLAUSES),
        (_s([{"condition": "raise_pct"}]), rc.BAD_CLAUSES),
    ]
    for item, reason in single:
        result = verified(_reply(good, item))
        assert result.rejected is None and result.submitted == 2, (item, reason)
        assert [r.key for r in result.valid] == [_normalized(top[0])], (item, reason)
        assert [(i.index, i.reason) for i in result.invalid] == [(1, reason)], (item, reason)
        assert result.invalid_count == 1
    # 說明的字元規則跟系統提示、評估版本參數同一份(代碼審 r1:另擋的格式字元要告訴模型)
    assert {"Cc", "Cf", "Cs"} == rc.NOTE_BANNED_CATEGORIES
    assert "格式字元" in rp.SYSTEM_PROMPT and "控制字元" in rp.SYSTEM_PROMPT
    assert "Cc、Cf、Cs" in rv.version_params()["note_rule"]
    # 說明的界:80 字、240 位元組都剛好可以
    for note in ("字" * 80, "😀" * 60):
        assert verified(_reply(good, _honest(top[1]) | {"confidence_note": note})).n == 2
    # 例子:support:true 與未知 raise_pct:band_9 各只丟該條、各計 1 筆
    mixed = verified(_reply(good, _s(support=True),
                            _s([{"condition": "raise_pct", "threshold": "raise_pct:band_9"}])))
    assert (mixed.n, mixed.invalid_count) == (1, 2)
    assert rc.reasons(mixed) == {"bad_count": 1, "threshold_not_in_condition": 1}

    # 去重:同一正規化鍵(子句順序不同)與同條件反方向,留先出現的合法條
    two = next(r for r in top if len(r.key) == 2)
    reordered = _s(list(reversed(_clauses(two.key))), two.direction, two.support, two.counter)
    flipped = _s(_clauses(two.key), rv.IMPROVE if two.direction == rv.NOT_IMPROVE
                 else rv.NOT_IMPROVE, two.counter, two.support)
    result = verified(_reply(_honest(two), reordered, flipped))
    assert [r.key for r in result.valid] == [_normalized(two)]
    assert [(i.index, i.reason) for i in result.invalid] == [(1, rc.DUPLICATE),
                                                             (2, rc.OPPOSITE)]
    assert result.invalid_count == 2
    # 先出現的那條不合法時,後面同鍵的合法條留下(不因不合法的條目丟掉合法的)
    result = verified(_reply(_honest(two) | {"support": True}, _honest(two)))
    assert [r.key for r in result.valid] == [_normalized(two)]
    assert [(i.index, i.reason) for i in result.invalid] == [(0, rc.BAD_COUNT)]


# ---- [S1505] ----
def test_rule_mining_recounts_every_suggestion_before_reporting(sides):  # noqa: PLR0915
    history, split, explore, holdout, summary = sides
    # 重算沿用增量 1 的配對與彙總:對封閉全集的每一條都跟探索側彙總一致
    for key in rv.all_conditions():
        assert explore.stats(key) == summary.stats[key], key
    held_summary = rb.summarize(history, split.holdout)
    top = rb.top_k(summary.stats)
    note = "模型的信心說明"
    honest = [_honest(r) | {"confidence_note": note} for r in top[:3]]
    lied = _honest(top[3]) | {"support": top[3].support + 1}
    swapped = _honest(top[4]) | {"support": top[4].counter, "counterexample": top[4].support}
    counter_only = _honest(top[5]) | {"counterexample": top[5].counter + 1}  # 只有反例不符
    low_key = next(k for k in rv.all_conditions() if not rb.meets_floor(summary.stats[k]))
    low_support, low_counter, _ = rb.directional(summary.stats[low_key], rv.IMPROVE)
    low = _s(_clauses(low_key), rv.IMPROVE, low_support, low_counter)  # 照實抄、但樣本不夠
    result = rc.verify(rc.parse_reply(_reply(*honest, lied, swapped, counter_only, low)),
                       explore, holdout)
    assert result.submitted == 7 and result.recounted == 7
    assert [c.key for c in result.valid] == [_normalized(r) for r in top[:3]]
    assert [(i.index, i.reason) for i in result.invalid] == [
        (3, rc.COUNT_MISMATCH), (4, rc.COUNT_MISMATCH), (5, rc.COUNT_MISMATCH),
        (6, rc.BELOW_FLOOR)]
    assert (result.n, result.invalid_count, result.mismatches) == (3, 4, 3)
    # 有效條目只帶程式重算的數字與保留側判定;模型的說明不在任何欄位
    for checked, ranked in zip(result.valid, top[:3], strict=True):
        condition, direction = checked.key
        stats = summary.stats[condition]
        support, counter, mean = rb.directional(stats, direction)
        assert (checked.support, checked.counter, checked.directional_mean) == (
            support, counter, mean)
        assert (checked.ties, checked.directed, checked.raised_ads, checked.control_ads,
                checked.dates) == (stats.ties, stats.directed, stats.raised_ads,
                                   stats.control_ads, stats.dates)
        assert checked.holdout == rb.holdout_verdict(held_summary.stats[condition], direction)
        assert checked.holdout_stats == held_summary.stats[condition]
        assert ranked.support == support
    fields = {f.name for f in dataclasses.fields(rc.Checked)}
    assert not fields & {"confidence_note", "note", "reported_support", "text"}
    assert note not in repr(result)

    # 19 平手 + 1 正差、模型照實報 1/0:數字相符,但重算只有 1 個有方向配對,仍無效
    raised = [_ad(f"a{i:02d}", raise_at=5, pre=3, post=4) for i in range(19)]
    raised.append(_ad("a19", raise_at=5, pre=3, post=6))
    controls = [_ad(f"c{i:02d}", pre=3, post=4) for i in range(20)]
    tiny = _history(raised + controls)
    everyone = frozenset(a.ad_id for a in tiny.ads)
    stats = rc.Recounter(tiny, everyone).stats(RAISE_2)
    assert (stats.pairs, stats.ties, stats.directed) == (20, 19, 1)
    tiny_recounter = rc.Recounter(tiny, everyone)
    result = rc.verify(rc.parse_reply(_reply(_s(support=1, counter=0))), tiny_recounter,
                       tiny_recounter)
    assert (result.n, result.invalid_count) == (0, 1)
    assert [i.reason for i in result.invalid] == [rc.BELOW_FLOOR]


# ---- [S1504] 代碼審 r1:崩潰輸入(報告原樣)一律收成整份拒絕或只丟該條,不往外丟 ----
def test_rule_mining_hostile_replies_never_crash_the_parser(sides, monkeypatch):
    _, _, explore, holdout, _ = sides
    wrap = '{{"version":1,"suggestions":[{{"clauses":[],"direction":{},"support":1,' \
           '"counterexample":0,"confidence_note":"a"}}]}}'
    # 報告的原始輸入(平衡 500 層、放在建議清單或欄位裡、2900 層):超過巢狀上限,整份拒絕
    for depth in (500, 2900):
        nested = "[" * depth + "]" * depth
        for text in (nested, '{"version":1,"suggestions":' + nested + "}", wrap.format(nested)):
            result = rc.verify(rc.parse_reply(text), explore, holdout)
            assert (result.rejected, result.n, result.invalid_count) == (rc.TOO_DEEP, 0, 1)
    # 報告 F2 輸入 A:說明是 \ud800 逸出 → 只丟該條
    attack_a = ('{"version":1,"suggestions":[{"clauses":[{"condition":"day_type","threshold":'
                '"day_type:weekday"}],"direction":"improve","support":1,"counterexample":0,'
                '"confidence_note":"\\ud800"}]}')
    parsed = rc.parse_reply(attack_a)
    assert parsed.rejected is None and [d.reason for d in parsed.dropped] == [rc.BAD_NOTE]
    # 報告 F2 輸入 B:原始文字本身含孤立代理字元 → 整份拒絕
    for text in ("\ud800", _reply() + "\ud800", attack_a.replace("\\ud800", "\ud800")):
        assert "\ud800" in text
        assert rc.parse_reply(text).rejected == rc.UNPARSABLE
    # 防線:解析或逐條檢查途中任何意外都收成整份拒絕/只丟該條
    def boom(*_args):
        raise RecursionError("深")

    monkeypatch.setattr(rc, "_structure", boom)
    assert rc.parse_reply(_reply()).rejected == rc.UNPARSABLE
    monkeypatch.undo()
    monkeypatch.setattr(rc, "_checked_suggestion", boom)
    parsed = rc.parse_reply(_reply(_s()))
    assert parsed.rejected is None and [d.reason for d in parsed.dropped] == [
        rc.UNREADABLE_SUGGESTION]


# ---- [S1504] 代碼審 r2:巢狀的整份/單條分界由解析器常數決定,不隨執行緒堆疊大小漂移 ----
def _nesting_verdicts():
    wrap = '{{"version":1,"suggestions":[{{"clauses":[],"direction":{},"support":1,' \
           '"counterexample":0,"confidence_note":"a"}}]}}'
    outer = 4  # 欄位本身在第 4 層之下:頂層物件、建議陣列、建議物件
    at_limit = rc.MAX_NESTING - outer + 1
    texts = [wrap.format("[" * at_limit + "]" * at_limit),  # 剛好上限 → 只丟該條
             wrap.format("[" * (at_limit + 1) + "]" * (at_limit + 1)),  # 上限加一 → 整份
             wrap.format("[" * 2900 + "]" * 2900)]
    return [(p.rejected, [d.reason for d in p.dropped]) for p in map(rc.parse_reply, texts)]


def test_rule_mining_nesting_limit_is_fixed_by_the_parser():
    assert rc.MAX_NESTING == 64
    # 合法回覆最深 5 層,遠在上限內
    legal = _reply(_s(), _s([{"condition": "pre_cvr", "threshold": "pre_cvr:band_1"},
                             {"condition": "day_type", "threshold": "day_type:weekday"}]))
    assert rc.nesting_depth(legal) == 5
    expected = [(None, ["clause_count"]), (rc.TOO_DEEP, []), (rc.TOO_DEEP, [])]
    assert _nesting_verdicts() == expected
    # 字串裡的括號(含逸出的引號後面)不算深度
    deep_note = _s(note="[" * 70)
    assert rc.nesting_depth(_reply(deep_note)) == 5
    assert rc.parse_reply(_reply(deep_note)).rejected is None
    tricky = _reply(_s(note='x"' + "[" * 70))  # 字串裡有逸出的引號 \\"
    assert '\\"[[[' in tricky
    assert rc.nesting_depth(tricky) == 5
    parsed = rc.parse_reply(tricky)
    assert parsed.rejected is None and [d.reason for d in parsed.dropped] == [rc.BAD_NOTE]
    # 小堆疊執行緒(256 KiB 下 json 約 1647 層就遞迴爆)裡跑同一批輸入,判決完全一樣
    results = []
    before = threading.stack_size()
    threading.stack_size(256 * 1024)
    try:
        worker = threading.Thread(target=lambda: results.append(_nesting_verdicts()))
        worker.start()
        worker.join(60)
    finally:
        threading.stack_size(before)
    assert results == [expected]
