"""Phase 15 增量 1:規則模式探索的合成歷史與無模型基準(計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈合成歷史與可觀察效果〉〈封閉條件語彙、彙總與窮舉基準〉)。

合約 [S1501] [S1502] [S1503](只驗彙總內容與位元組閘,不接模型) [S1511] [S1512] [S1513] [S1515]
[S1519] [S1520]。純離線:不呼叫任何模型、不讀寫 ~/.rtb。手造的小歷史只用來驗單一規則;固定三種子
的整批生成用模組範圍的 fixture,整個檔只各生成一次。
"""

import dataclasses
import hashlib
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

import pytest

from rtb import modelcore
from rtb.domain import metrics as m
from rtb.eval import rule_mining_baseline as rb
from rtb.eval import rule_mining_history as rh
from rtb.eval import rule_mining_prompt as rp
from rtb.eval import rule_mining_vocab as rv
from rtb.eval import scoring

START = date(2026, 8, 3)


# ---- 手造歷史的小工具 ----
def _bucket(day, clicks=100, conv=3, spend=3000, impressions=None):
    return rh.DayBucket(day=day, impressions=clicks * 30 if impressions is None else impressions,
                        clicks=clicks, conversions=conv, spend_cents=spend,
                        revenue_cents=conv * 4000, no_data=False)


def _ad(ad_id, days=10,  # noqa: PLR0913 - 造資料的小工具,各參數都有預設
        pre=3, post=3, clicks=100, spend=3000, raise_at=None, budget=2000,
        after=2600, changes=None, extra_raises=(), decreases=()):
    """一支廣告:raise_at 之前每天 pre 筆轉換、之後每天 post 筆;changes 覆寫某天的桶;
    extra_raises 另加幾筆加額,decreases 另加幾筆降額(日預算砍半)。"""
    buckets = []
    for index in range(days):
        conv = post if raise_at is not None and index > raise_at else pre
        if raise_at is None and index >= 5:
            conv = post
        buckets.append(_bucket(START + timedelta(days=index), clicks=clicks, conv=conv,
                               spend=spend))
    for index, bucket in (changes or {}).items():
        buckets[index] = bucket
    adjustments = []
    for k, at in enumerate(([] if raise_at is None else [raise_at]) + list(extra_raises)):
        adjustments.append(rh.Adjustment(
            op_id=f"{ad_id}-op{k}", committed_at=datetime.combine(
                START + timedelta(days=at), datetime.min.time(), tzinfo=UTC) + timedelta(hours=9),
            budget_before_cents=budget, budget_after_cents=after))
    for k, at in enumerate(decreases):
        adjustments.append(rh.Adjustment(
            op_id=f"{ad_id}-cut{k}", committed_at=datetime.combine(
                START + timedelta(days=at), datetime.min.time(), tzinfo=UTC) + timedelta(hours=9),
            budget_before_cents=budget, budget_after_cents=budget // 2))
    return rh.Ad(ad_id=ad_id, daily_budget_cents=budget, days=tuple(buckets),
                 adjustments=tuple(adjustments))


def _history(ads, days=10):
    return rh.History(seed=0, generator_version="test", start=START, day_count=days,
                      ads=tuple(ads))


def _summary(history, ids=None, key=None):
    side = frozenset(a.ad_id for a in history.ads) if ids is None else frozenset(ids)
    summary = rb.summarize(history, side)
    return summary if key is None else summary.stats[key]


RAISE_2 = ((rv.RAISE_PCT, "raise_pct:band_2"),)  # 2600/2000 = 加 30%
WEEKEND = ((rv.DAY_TYPE, "day_type:weekend"),)  # 第 5 天是週六


def _pair(k, diff, raised=None, control=None, day=START):
    return rb.Pair(op_id=f"op{k}", raised_ad=raised or f"a{k:02d}",
                   control_ad=control or f"c{k:02d}", day=day, diff=Fraction(diff))


def _stats(pairs, events=None):
    return rb.stats_of(RAISE_2, len(pairs) if events is None else events, tuple(pairs), 0)


# ---- 固定三種子(整個檔只生成一次) ----
@pytest.fixture(scope="module")
def generated():
    """種子 → 合成歷史;同一個測試檔裡每個種子只生成一次。"""
    cache = {}

    def get(seed):
        if seed not in cache:
            cache[seed] = rh.generate(seed)
        return cache[seed]

    return get


@pytest.fixture(scope="module")
def preflight_results():
    return rp.preflight()


# ---- [S1501] ----
def test_rule_mining_history_is_reproducible_and_consistent(generated):  # noqa: PLR0915 - 逐項自洽檢查
    assert rv.SEEDS == (15001, 15002, 15003)
    with pytest.raises(ValueError, match="固定種子"):
        rh.generate(15004)  # 任一批不得換種子
    for seed in rv.SEEDS:
        history = generated(seed)
        again = rh.generate(seed)
        text = rh.render(history)
        assert text == rh.render(again)  # 位元組一致
        assert rh.data_sha256(history) == hashlib.sha256(text.encode()).hexdigest()
        assert rh.data_sha256(history) == rh.EXPECTED_DATA_SHA256[seed]
        assert history.seed == seed and history.generator_version == rv.GENERATOR_VERSION
        assert len(history.ads) >= 300 and history.day_count >= 28
        assert len({a.ad_id for a in history.ads}) == len(history.ads)
        expected_days = [history.start + timedelta(days=i) for i in range(history.day_count)]
        for ad in history.ads:
            assert [b.day for b in ad.days] == expected_days  # 連續 UTC 日、每天一個桶
            budget = ad.daily_budget_cents
            by_day = {a.committed_at.date(): a for a in ad.adjustments}
            for bucket in ad.days:
                if bucket.day in by_day:  # 操作鏈:調整前預算接上一筆調整後
                    assert by_day[bucket.day].budget_before_cents == budget
                    budget = by_day[bucket.day].budget_after_cents
                if bucket.no_data:
                    assert bucket.clicks is None and bucket.spend_cents is None
                    continue
                values = (bucket.impressions, bucket.clicks, bucket.conversions,
                          bucket.spend_cents, bucket.revenue_cents)
                assert all(type(v) is int and v >= 0 for v in values)  # 整數分,不是浮點
                assert bucket.conversions <= bucket.clicks <= bucket.impressions
                assert bucket.spend_cents <= budget
            for adjustment in ad.adjustments:
                assert adjustment.committed_at.tzinfo is UTC
                assert history.start <= adjustment.committed_at.date() <= expected_days[-1]
                assert adjustment.budget_after_cents > adjustment.budget_before_cents
    # 真相清單同一生成版本固定;真模式改善/未改善各一,誘餌兩類各一,鍵都是語彙內的正規化鍵
    kinds = Counter((t.kind, t.direction) for t in rh.TRUTH)
    assert kinds[(rh.TRUE_PATTERN, rv.IMPROVE)] >= 1
    assert kinds[(rh.TRUE_PATTERN, rv.NOT_IMPROVE)] >= 1
    assert sum(n for (kind, _), n in kinds.items() if kind == rh.DECOY_EXPLORE_ONLY) >= 1
    assert sum(n for (kind, _), n in kinds.items() if kind == rh.DECOY_CORRELATED) >= 1
    for item in rh.TRUTH:
        assert rv.normalized_key(item.clauses, item.direction) == item.key
        assert item.key[0] in rv.all_conditions()
    # 真相清單不在評估集裡(評估集的文字不含類別名)
    text = rh.render(generated(rv.SEEDS[0]))
    assert rh.TRUE_PATTERN not in text and rh.DECOY_CORRELATED not in text


# ---- [S1502] ----
def _two_ads(**raised):
    raised_ad = _ad("a1", raise_at=5, pre=3, post=6, **raised)
    control = _ad("c1", pre=3, post=4)  # 未加額:第 5 天以後每天 4 筆
    return raised_ad, control


def test_rule_mining_effects_use_complete_utc_days_and_exact_ratios():  # noqa: PLR0915
    raised_ad, control = _two_ads()
    history = _history([raised_ad, control])
    events, excluded = rb.side_events(history, frozenset({"a1", "c1"}))
    assert excluded == {} and len(events) == 1
    (event,) = events
    assert event.day == START + timedelta(days=5)
    # 前三日 9/300、後三日 18/300:絕對差(不是 exact_change 的相對變化)
    assert event.change == Fraction(18, 300) - Fraction(9, 300)
    assert event.change == m.exact_ratio(18, 300) - m.exact_ratio(9, 300)
    stats = _summary(history, key=RAISE_2)
    assert stats.pairs == 1 and stats.positive == 1
    assert isinstance(stats.diff_sum, Fraction)
    # 對照 C 前三日 9/300、後三日(第 6-8 天)12/300 → 差值 9/300 - 3/300 = 1/50
    assert stats.diff_sum == Fraction(1, 50)
    # D 本身不算:把 D 當天改得很極端,效果不變
    wild = _ad("a1", raise_at=5, pre=3, post=6, changes={5: _bucket(START + timedelta(days=5),
                                                                     conv=90)})
    assert _summary(_history([wild, control]), key=RAISE_2).diff_sum == Fraction(1, 50)

    def reasons(*ads):
        return rb.side_events(_history(ads), frozenset(a.ad_id for a in ads))[1]

    # 未滿後三日 / 前三日不在觀察期內
    assert reasons(_ad("a1", raise_at=7)) == {rb.INCOMPLETE_WINDOW: 1}
    assert reasons(_ad("a1", raise_at=2)) == {rb.INCOMPLETE_WINDOW: 1}
    # 視窗內另有調整:兩筆都不推斷
    assert reasons(_ad("a1", raise_at=4, extra_raises=(6,))) == {rb.OVERLAPPING_ADJUSTMENT: 2}

    # 重疊判準(v2,代碼審 c_2/c_4):另一筆調整(加額或降額)的 D±3 視窗與本筆相交,即相距 ≤ 6 天
    def overlap(others=(), decreases=()):
        ad = _ad("a1", days=16, raise_at=5, extra_raises=others, decreases=decreases)
        return rb.side_events(_history([ad], days=16), frozenset({"a1"}))

    assert overlap((8,))[1] == {rb.OVERLAPPING_ADJUSTMENT: 2}  # 相距恰好 3
    assert overlap((11,))[1] == {rb.OVERLAPPING_ADJUSTMENT: 2}  # 相距 6:前一筆後三日落進本筆前三日
    events, excluded = overlap((12,))  # 相距 7:視窗不相交,兩筆都推斷
    assert excluded == {} and len(events) == 2
    assert overlap(decreases=(7,))[1] == {rb.OVERLAPPING_ADJUSTMENT: 1}  # 重疊的是降額也排除
    assert overlap(decreases=(11,))[1] == {rb.OVERLAPPING_ADJUSTMENT: 1}
    assert overlap(decreases=(12,))[1] == {}
    # 前一筆是降額(代碼審 r2_2):降額第 3 天、加額第 9 天相距 6 → 排除;相距 7 → 推斷
    def after_cut(cut_day):
        ad = _ad("a1", days=16, raise_at=9, decreases=(cut_day,))
        return rb.side_events(_history([ad], days=16), frozenset({"a1"}))

    assert after_cut(3)[1] == {rb.OVERLAPPING_ADJUSTMENT: 1}
    events, excluded = after_cut(2)
    assert excluded == {} and len(events) == 1
    # no_data、缺值
    gap = rh.DayBucket(day=START + timedelta(days=6), impressions=None, clicks=None,
                       conversions=None, spend_cents=None, revenue_cents=None, no_data=True)
    assert reasons(_ad("a1", raise_at=5, changes={6: gap})) == {rb.MISSING_VALUE: 1}
    none_clicks = dataclasses.replace(_bucket(START + timedelta(days=3)), clicks=None)
    assert reasons(_ad("a1", raise_at=5, changes={3: none_clicks})) == {rb.MISSING_VALUE: 1}
    # 資料異常:點擊比曝光多、浮點金額
    odd = _bucket(START + timedelta(days=7), clicks=100, impressions=50)
    assert reasons(_ad("a1", raise_at=5, changes={7: odd})) == {rb.ANOMALOUS_DATA: 1}
    floaty = dataclasses.replace(_bucket(START + timedelta(days=4)), spend_cents=30.5)
    assert reasons(_ad("a1", raise_at=5, changes={4: floaty})) == {rb.ANOMALOUS_DATA: 1}
    # 轉換率分母為零:不當零
    zero = {i: _bucket(START + timedelta(days=i), clicks=0, conv=0) for i in (6, 7, 8)}
    assert reasons(_ad("a1", raise_at=5, changes=zero)) == {rb.ZERO_DENOMINATOR: 1}
    # 對照 C 於 D+2 曾加額(D 日沒加額)也不得入池;其自己那筆因未滿後三日排除
    raised_c = _ad("c1", pre=3, post=4, raise_at=7)
    summary = _summary(_history([raised_ad, raised_c]))
    assert summary.stats[RAISE_2].pairs == 0 and summary.stats[RAISE_2].no_control == 1
    assert summary.exclusions == {rb.INCOMPLETE_WINDOW: 1}
    # 對照的視窗不可算(分母為零)也不能用
    zero_control = _ad("c1", changes={i: _bucket(START + timedelta(days=i), clicks=0, conv=0)
                                      for i in (2, 3, 4)})
    assert _summary(_history([raised_ad, zero_control]), key=RAISE_2).no_control == 1


# ---- [S1503] ----
def test_rule_mining_prompt_contains_only_bounded_aggregates(generated):  # noqa: PLR0915
    seed = rv.SEEDS[0]
    history = generated(seed)
    sides = rh.split(a.ad_id for a in history.ads)
    summary = rb.summarize(history, sides.explore)
    table = rp.summary_table(summary)
    rows = [line for line in table.splitlines() if line and line[0].islower()]
    passing = [k for k in rv.all_conditions() if rb.meets_floor(summary.stats[k])]
    assert len(rows) == len(passing) > 0  # 完整表,不截列
    assert [r.split("|")[0] for r in rows] == [rv.key_text(k) for k in passing]
    for row, key in zip(rows, passing, strict=True):
        stats = summary.stats[key]
        cells = row.split("|")
        assert stats.directed >= 20 and stats.raised_ads >= 20 and stats.control_ads >= 20
        assert cells[1:10] == [str(v) for v in (
            stats.events, stats.pairs, stats.directed, stats.positive, stats.negative,
            stats.ties, stats.raised_ads, stats.control_ads, stats.dates)]
        assert cells[10] == m.percent_text(m.exact_ratio(stats.positive, stats.directed))
        mean = stats.diff_sum / stats.directed
        # 差值:百分點四位小數,共用既有 percent_text 的 half-even(代碼審 a_2);精確分數不外送
        assert cells[11] == m.percent_text(mean, places=4)
    assert rp.DIFF_PLACES == 4
    assert "/" not in table.replace("pp", "")
    # 不送保留側、逐日列、識別、時間戳或真相標籤
    prompt = rp.SYSTEM_PROMPT + table
    for ad in history.ads:
        assert ad.ad_id not in prompt
        assert all(a.op_id not in prompt for a in ad.adjustments)
    assert "2026-" not in prompt and "T0" not in prompt
    for label in (rh.TRUE_PATTERN, rh.DECOY_CORRELATED, rh.DECOY_EXPLORE_ONLY, "真模式", "誘餌"):
        assert label not in prompt
    holdout_changed = dataclasses.replace(history, ads=tuple(
        ad if ad.ad_id in sides.explore else dataclasses.replace(ad, days=ad.days[:5])
        for ad in history.ads))  # 保留側怎麼變,送出的表都一樣
    assert rp.summary_table(rb.summarize(holdout_changed, sides.explore)) == table
    # 詞彙全部在系統提示裡;要求 UTF-8 原字、緊湊 JSON
    for field in rv.FIELDS:
        assert all(code in rp.SYSTEM_PROMPT for code in rv.THRESHOLDS[field])
    assert "\\uXXXX" in rp.SYSTEM_PROMPT and "not_improve" in rp.SYSTEM_PROMPT
    # 位元組閘:20480 可、20481 拒;達既有 48 KiB 也拒;常數與模型用戶端一致
    assert rv.GATEWAY_PROMPT_BYTES == modelcore.MAX_PROMPT_BYTES == 48 * 1024
    assert rp.gate_problem(20480) is None
    assert rp.gate_problem(20481) is not None
    assert rp.gate_problem(48 * 1024) is not None
    assert rp.prompt_bytes(table) == len(rp.SYSTEM_PROMPT.encode()) + len(table.encode())


# ---- [S1511] ----
def _crowd(n_raised, n_controls, day=5, prefix="a"):
    raised = [_ad(f"{prefix}{i:02d}", raise_at=day, pre=3, post=6) for i in range(n_raised)]
    controls = [_ad(f"c{i:02d}", pre=3, post=4) for i in range(n_controls)]
    return raised, controls


def test_rule_mining_pairs_stay_within_split_and_use_distinct_ads():
    # 21 筆加額只有 19 支對照:19 對、2 筆無對照,不合格
    raised, controls = _crowd(21, 19)
    stats = _summary(_history(raised + controls), key=RAISE_2)
    assert (stats.pairs, stats.no_control, stats.control_ads) == (19, 2, 19)
    assert not rb.meets_floor(stats)
    # 21 對共用 1 支對照:相異對照只有 1 支,不合格
    shared = _stats([_pair(k, 1, control="c00") for k in range(21)])
    assert shared.directed == 21 and shared.control_ads == 1 and not rb.meets_floor(shared)
    # 20 對、20/20 相異廣告:合格;星期條件照算且報日期數(全在同一天 → 1 個日期)
    raised, controls = _crowd(20, 20)
    summary = _summary(_history(raised + controls))
    weekend = summary.stats[WEEKEND]
    assert rb.meets_floor(weekend) and weekend.dates == 1
    assert (weekend.raised_ads, weekend.control_ads) == (20, 20)
    # 每條件每支對照至多一次;同一支對照可在不同條件各用一次
    used = [p.control_ad for p in rb.pair_condition(
        WEEKEND, rb.side_events(_history(raised + controls), frozenset(
            a.ad_id for a in raised + controls))[0], rb.control_index(
            _history(raised + controls), frozenset(a.ad_id for a in raised + controls)))[0]]
    assert len(used) == len(set(used)) == 20
    # 探索側只找得到保留側的對照:不跨側
    history = _history([_ad("a1", raise_at=5, pre=3, post=6), _ad("c1", pre=3, post=4)])
    stats = _summary(history, ids={"a1"}, key=RAISE_2)
    assert stats.pairs == 0 and stats.no_control == 1
    # 對照在觀察期曾加額(即使視窗外):不入池
    history = _history([_ad("a1", raise_at=5, pre=3, post=6),
                        _ad("c1", pre=3, post=4, raise_at=0)])
    assert _summary(history, key=RAISE_2).no_control == 1
    # 對照整期只有降額也不入池(v2,代碼審 c_5:和事件側「任何調整都算」對稱)
    for day in (0, 6):
        cut = _ad("c1", pre=3, post=4, decreases=(day,))
        history = _history([_ad("a1", raise_at=5, pre=3, post=6), cut])
        assert _summary(history, key=RAISE_2).no_control == 1
        assert rb.control_index(history, frozenset({"c1"})) == {}
    # 「整期」不是「D±6」(代碼審 r2_1):20 天歷史、加額第 14 天,對照在第 2 天降額或加額(距 D 12 天)
    # 仍不入池;沒有任何調整的同款對照則配得上
    raised = _ad("a1", days=20, raise_at=14, pre=3, post=6)
    for far in ({"decreases": (2,)}, {"extra_raises": (2,)}, {}):
        control = _ad("c1", days=20, pre=3, post=3, **far)
        stats = _summary(_history([raised, control], days=20), key=RAISE_2)
        assert (stats.pairs, stats.no_control) == ((1, 0) if not far else (0, 1)), far


# ---- [S1512] ----
def test_rule_mining_ties_are_neutral_in_summary_and_recount():
    pairs = [_pair(k, Fraction(1, 100)) for k in range(12)]
    pairs += [_pair(k, Fraction(-1, 200)) for k in range(12, 20)]
    pairs += [_pair(20, 0)]
    stats = _stats(pairs)
    assert (stats.positive, stats.negative, stats.ties, stats.pairs) == (12, 8, 1, 21)
    assert stats.directed == 20 == stats.positive + stats.negative
    assert stats.pairs == stats.positive + stats.negative + stats.ties
    assert rb.directional(stats, rv.IMPROVE) == (12, 8, (Fraction(12, 100) - Fraction(8, 200)) / 20)
    assert rb.directional(stats, rv.NOT_IMPROVE) == (
        8, 12, -(Fraction(12, 100) - Fraction(8, 200)) / 20)
    # 平手的廣告不算相異廣告
    assert (stats.raised_ads, stats.control_ads) == (20, 20)
    # 19 平手 + 1 正差:有方向數 1,不達下限(即使總配對 20、涉及 20 支廣告)
    stats = _stats([_pair(k, 0) for k in range(19)] + [_pair(19, 1)])
    assert (stats.pairs, stats.directed, stats.raised_ads) == (20, 1, 1)
    assert not rb.meets_floor(stats)
    # 相異日期只數有方向配對:平手那天不算(代碼審 c_6)
    stats = _stats([_pair(0, 1, day=START + timedelta(days=5)),
                    _pair(1, 0, day=START + timedelta(days=6))])
    assert (stats.pairs, stats.directed, stats.dates) == (2, 1, 1)
    # 重算:實際配對的平手也不進支持/反例
    raised, controls = _crowd(3, 3)
    tie = _ad("a09", raise_at=5, pre=3, post=4)  # 變化同對照 → 差值 0
    stats = _summary(_history([*raised, tie, *controls, _ad("c09", pre=3, post=4)]), key=RAISE_2)
    assert (stats.positive, stats.negative, stats.ties) == (3, 0, 1)


# ---- [S1513] ----
def _holdout(n_pos, n_neg, n_tie=0, pos=Fraction(1, 100), neg=Fraction(-1, 100)):
    pairs = [_pair(k, pos) for k in range(n_pos)]
    pairs += [_pair(n_pos + k, neg) for k in range(n_neg)]
    pairs += [_pair(n_pos + n_neg + k, 0) for k in range(n_tie)]
    return _stats(pairs)


def test_rule_mining_holdout_rule_is_frozen_and_recounted():
    frozen = (rv.MIN_DIRECTED, rv.MIN_DISTINCT_ADS, rv.HOLDOUT_SUPPORT)
    assert frozen == (20, 20, Fraction(3, 5))
    verdict = rb.holdout_verdict(_holdout(12, 7), rv.IMPROVE)  # 19 個有方向
    assert not verdict.kept and rb.INSUFFICIENT_SAMPLE in verdict.reasons
    verdict = rb.holdout_verdict(_holdout(11, 9), rv.IMPROVE)  # 11/20 < 3/5
    assert not verdict.kept and verdict.reasons == (rb.LOW_SUPPORT,)
    assert rb.holdout_verdict(_holdout(12, 8), rv.IMPROVE).kept  # 12/20 且平均差值正
    # 平手不進保留比例分母:12 正 8 負 3 平手是 12/20 保留,不是 12/23(代碼審 c_1)
    assert rb.holdout_verdict(_holdout(12, 8, n_tie=3), rv.IMPROVE).kept
    # 12/20 但平均差值 ≤ 0:方向不符
    verdict = rb.holdout_verdict(_holdout(12, 8, neg=Fraction(-2, 100)), rv.IMPROVE)
    assert not verdict.kept and verdict.reasons == (rb.WRONG_SIGN,)
    verdict = rb.holdout_verdict(_holdout(12, 8, neg=Fraction(-3, 200)), rv.IMPROVE)
    assert not verdict.kept and verdict.reasons == (rb.WRONG_SIGN,)  # 平均剛好 0
    # 19 平手 + 1 正差:總配對 20 仍不保留
    verdict = rb.holdout_verdict(_holdout(1, 0, n_tie=19), rv.IMPROVE)
    assert not verdict.kept and rb.INSUFFICIENT_SAMPLE in verdict.reasons
    # 未改善方向對稱
    assert rb.holdout_verdict(_holdout(8, 12), rv.NOT_IMPROVE).kept
    assert not rb.holdout_verdict(_holdout(8, 12), rv.IMPROVE).kept
    # 分母為零:未量
    verdict = rb.holdout_verdict(_stats([]), rv.IMPROVE)
    assert not verdict.kept and verdict.reasons == (rb.INSUFFICIENT_SAMPLE, rb.UNMEASURED)
    # 相異廣告不足也不保留
    few = _stats([_pair(k, 1, control="c00") for k in range(20)])
    assert rb.INSUFFICIENT_SAMPLE in rb.holdout_verdict(few, rv.IMPROVE).reasons


# ---- [S1515] ----
def _ranked_stats(key, n_pos, n_neg, pos=Fraction(1, 100), neg=Fraction(-1, 100), n_tie=0):
    pairs = [_pair(k, pos) for k in range(n_pos)] + [
        _pair(n_pos + k, neg) for k in range(n_neg)] + [
        _pair(n_pos + n_neg + k, 0) for k in range(n_tie)]
    return rb.stats_of(key, len(pairs), tuple(pairs), 0)


def test_rule_mining_exhaustive_order_is_frozen_and_symmetric():  # noqa: PLR0915 - 逐鍵情境
    assert rv.K == 10
    a, b = ((rv.PRE_CVR, "pre_cvr:band_1"),), ((rv.PRE_CVR, "pre_cvr:band_2"),)
    # 17/21 比例較高,但 Wilson 下界 70/100 較高 → 70/100 先
    table = {a: _ranked_stats(a, 17, 4), b: _ranked_stats(b, 70, 30)}
    ranked = rb.rank(table)
    assert [r.key for r in ranked] == [b, a]
    assert ranked[0].wilson == scoring.wilson_lower(70, 100)  # 直接用既有函式與 Z_95
    assert ranked[1].wilson == scoring.wilson_lower(17, 21)
    # 兩方向對稱:鏡像的未改善得到同一下界與方向化平均
    mirrored = {a: _ranked_stats(a, 4, 17), b: _ranked_stats(b, 30, 70)}
    ranked_m = rb.rank(mirrored)
    assert [(r.key, r.direction) for r in ranked_m] == [(b, rv.NOT_IMPROVE), (a, rv.NOT_IMPROVE)]
    assert [r.wilson for r in ranked_m] == [r.wilson for r in ranked]
    assert [r.directional_mean for r in ranked_m] == [r.directional_mean for r in ranked]
    # 同鍵兩方向只留較高者;下界相同時依方向化平均、有方向數、鍵、方向代碼
    c = ((rv.SPEND_RATIO, "spend_ratio:band_1"),)
    d = ((rv.DAY_TYPE, "day_type:weekend"),)
    table = {c: _ranked_stats(c, 15, 10, pos=Fraction(1, 100)),
             d: _ranked_stats(d, 15, 10, pos=Fraction(2, 100))}
    ranked = rb.rank(table)
    assert [r.key for r in ranked] == [d, c] and len(ranked) == 2
    even = {c: _ranked_stats(c, 10, 10), d: _ranked_stats(d, 10, 10)}
    ranked = rb.rank(even)  # 下界、平均(0)、數量都相同 → 鍵升序、方向代碼升序
    assert [(r.key, r.direction) for r in ranked] == [(d, rv.IMPROVE), (c, rv.IMPROVE)]
    # 平手不進 Wilson 分母、也不進第三鍵的有方向數(代碼審 c_1):除平手外相同 → 依鍵
    tied = {a: _ranked_stats(a, 14, 6, n_tie=10), b: _ranked_stats(b, 14, 6)}
    ranked = rb.rank(tied)
    assert [r.key for r in ranked] == [a, b]
    assert ranked[0].wilson == ranked[1].wilson == scoring.wilson_lower(14, 20)
    assert [(r.support, r.counter, r.directed) for r in ranked] == [(14, 6, 20)] * 2
    # 第三鍵:下界與方向化平均相同時,有方向配對數多者先
    same = [rb.Ranked(a, rv.IMPROVE, 1, 0, 0.5, Fraction(1), 20),
            rb.Ranked(b, rv.IMPROVE, 1, 0, 0.5, Fraction(1), 30)]
    assert [r.key for r in sorted(same, key=lambda r: r.sort_key)] == [b, a]
    # 未達下限不入選;前 K 至多 K 條
    small = {a: _ranked_stats(a, 19, 0)}
    assert rb.rank(small) == ()
    many = {k: _ranked_stats(k, 20 + i, 5) for i, k in enumerate(rv.all_conditions())}
    top = rb.top_k(many)
    assert len(top) == rv.K
    assert top == rb.rank(many)[: rv.K]
    assert rb.top_k(many, 3) == top[:3]


# ---- [S1519] ----
def test_rule_mining_preflight_checks_all_fixed_seed_prompts(generated, preflight_results):
    results = preflight_results
    assert tuple(r.seed for r in results) == rv.SEEDS  # 三批全列,不換種子
    for result in results:
        history = generated(result.seed)
        sides = rh.split(a.ad_id for a in history.ads)
        table = rp.summary_table(rb.summarize(history, sides.explore))
        # 量的是完整系統提示加彙總表
        assert result.prompt_bytes == len(rp.SYSTEM_PROMPT.encode()) + len(table.encode())
        assert result.data_sha256 == rh.EXPECTED_DATA_SHA256[result.seed]
        assert [t.key for t in result.truths] == [t.key for t in rh.TRUTH]
        assert (result.explore_ads, result.holdout_ads) == (len(sides.explore),
                                                            len(sides.holdout))
    assert rp.preflight_problems(results) == ()
    assert rp.preflight_record(results) == rp.PREFLIGHT_RECORD  # 版本理由記的數字與重算一致
    # 第三批 20481 位元組:即使前兩批較小也整體失敗,並點名該批
    too_big = (*results[:2], dataclasses.replace(results[2], prompt_bytes=20481))
    problems = rp.preflight_problems(too_big)
    assert len(problems) == 1 and str(rv.SEEDS[2]) in problems[0]
    # 真模式/誘餌分母不可達也失敗
    first = results[0]
    unreachable = dataclasses.replace(first.truths[0], explore=rb.Reach(19, 19, 19))
    broken = (dataclasses.replace(first, truths=(unreachable, *first.truths[1:])), *results[1:])
    assert rp.preflight_problems(broken)
    # 少一批也失敗(不得只報兩批)
    assert rp.preflight_problems(results[:2])


# ---- [S1520] ----
def test_rule_mining_pairing_parameters_are_frozen():
    ids = [f"ad-{i:03d}" for i in range(7)]
    order = sorted(ids, key=lambda a: (hashlib.sha256(a.encode()).digest(), a.encode()))
    sides = rh.split(reversed(ids))
    assert sides.explore == frozenset(order[:3]) and sides.holdout == frozenset(order[3:])
    assert rh.split(ids) == sides  # 輸入順序不影響
    # 規模桶以前三日花費整數分
    assert [rv.scale_bucket(v) for v in (9999, 10000, 49999, 50000)] == [
        "lt_10000", "10000_49999", "10000_49999", "ge_50000"]
    # 兩筆同日同桶加額搶一支 C:固定鍵較前者拿到 C;重跑、換輸入順序結果相同
    first = _ad("a1", raise_at=5, pre=3, post=6)
    second = _ad("a2", raise_at=5, pre=3, post=6)
    c = _ad("c1", pre=3, post=4)

    def winners(ads):
        history = _history(ads)
        side = frozenset(a.ad_id for a in ads)
        pairs, missing = rb.pair_condition(RAISE_2, rb.side_events(history, side)[0],
                                           rb.control_index(history, side))
        return [(p.raised_ad, p.control_ad) for p in pairs], missing

    assert winners([first, second, c]) == ([("a1", "c1")], 1)
    assert winners([c, second, first]) == ([("a1", "c1")], 1)
    # 次序鍵先比 D 再比加額廣告編號(a0 < a1),不看輸入順序或操作識別碼
    early = dataclasses.replace(second, ad_id="a0", adjustments=(dataclasses.replace(
        second.adjustments[0], op_id="z", committed_at=second.adjustments[0].committed_at
        - timedelta(hours=1)),))
    assert winners([first, early, c]) == ([("a0", "c1")], 1)
    # 第二鍵(編號)先於第三鍵(committed_at):a0 比 a1 晚一小時提交,仍先拿到 C(代碼審 c_6)
    late = dataclasses.replace(second, ad_id="a0", adjustments=(dataclasses.replace(
        second.adjustments[0], op_id="a0-op0", committed_at=second.adjustments[0].committed_at
        + timedelta(hours=1)),))
    assert winners([first, late, c]) == ([("a0", "c1")], 1)
    # 對照按編號 UTF-8 位元組升序取第一支未用者
    assert winners([first, _ad("c2", pre=3, post=4), c])[0] == [("a1", "c1")]
    # 不同規模桶或不同前三日轉換率區間的對照不配
    big = _ad("c1", pre=3, post=4, spend=20000)
    assert winners([first, big]) == ([], 1)
    other_band = _ad("c1", pre=9, post=9)  # 9/100 ≥ 5%,前三日率落在別區
    assert winners([first, other_band]) == ([], 1)
    # 凍結:評估版本雜湊涵蓋切分、對照池、配對欄位與切點、搶用次序、效果公式、K 與下限
    params = rv.version_params()
    for name in ("split", "control_pool", "overlap", "pair_fields", "scale_cuts_cents",
                 "event_order",
                 "control_order", "effect", "mean", "k", "min_directed", "min_distinct_ads",
                 "holdout_support", "ranking", "seeds", "cuts", "formats"):
        assert name in params, name
    assert params["seeds"] == list(rv.SEEDS)
    assert params["scale_cuts_cents"] == [10000, 50000]
    assert rp.version_sha256() == rp.EXPECTED_VERSION_SHA256
