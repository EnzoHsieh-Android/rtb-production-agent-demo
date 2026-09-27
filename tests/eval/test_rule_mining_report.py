"""Phase 15 增量 3:規則模式探索的比較與人讀報告(純函式;計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈模型建議、機械核對與人讀報告〉)。

合約 [S1506] [S1516]。回覆都是手寫的假回覆,不呼叫模型;固定種子 15001 的資料照生成器重產。
"""

import json
from fractions import Fraction

import pytest

from rtb.analyzer import rule_mining_model as rmm
from rtb.eval import rule_mining_baseline as rb
from rtb.eval import rule_mining_check as rc
from rtb.eval import rule_mining_eval as rme
from rtb.eval import rule_mining_history as rh
from rtb.eval import rule_mining_prompt as rp
from rtb.eval import rule_mining_report as rr
from rtb.eval import rule_mining_vocab as rv

SEED = rv.SEEDS[0]
TRUE_A, TRUE_B = rr.TRUE_KEYS
EXPLORE_ONLY = next(t.key for t in rh.TRUTH if t.kind == rh.DECOY_EXPLORE_ONLY)
CORRELATED = next(t.key for t in rh.TRUTH if t.kind == rh.DECOY_CORRELATED)
NOISE = (((rv.PRE_CVR, "pre_cvr:band_1"),), rv.IMPROVE)  # 跟任何預埋項都不共用子句
NOISE_2 = (((rv.PRE_CVR, "pre_cvr:band_2"),), rv.NOT_IMPROVE)
PARTIAL = (((rv.DAY_TYPE, rv.WEEKDAY), (rv.RAISE_PCT, "raise_pct:band_3")), rv.NOT_IMPROVE)


def entry(key, kept=True):
    return rr.Entry(key, rb.Holdout(kept, () if kept else (rb.LOW_SUPPORT,)))


@pytest.fixture(scope="module")
def inputs():
    return rp.seed_inputs(SEED)


@pytest.fixture(scope="module")
def prepared(inputs):
    return rme.prepare_from(inputs)


def suggestion(prepared, key, *, threshold=None):
    """照探索側重算數字填一條(threshold 換成別的門檻代碼時數字照填,讓語彙檢查去丟它)。"""
    condition, direction = key
    support, counter, _ = rb.directional(prepared.explore.stats(condition), direction)
    clauses = [{"condition": f, "threshold": threshold or t} for f, t in condition]
    return {"clauses": clauses, "direction": direction, "support": support,
            "counterexample": counter, "confidence_note": "照表抄"}


def verified(prepared, items):
    text = json.dumps({"version": 1, "suggestions": items}, ensure_ascii=False,
                      separators=(",", ":"))
    return rc.verify(rc.parse_reply(text), prepared.explore, prepared.holdout)


def measured(verification):
    return rr.AiResult(rr.MEASURED, verification,
                       rr.Source("phase15-seed-15001-1", "phase15-rule-mining-15001-20260928",
                                 "claude-sonnet-5", "2026-09-28T00:00:00+00:00", 12345))


# ---- [S1506] ----
def test_rule_mining_compares_ai_with_the_same_exhaustive_search(inputs, prepared):  # noqa: PLR0915
    # 類別:精確命中真模式才算命中;相關誘餌保留側也成立才另列,否則計誤報;其餘都計誤報
    assert rr.classify(TRUE_A, kept=False) == rr.CAT_TRUE
    assert rr.classify(CORRELATED, kept=True) == rr.CAT_RELATED
    assert rr.classify(CORRELATED, kept=False) == rr.CAT_RELATED_FAILED
    assert rr.classify(EXPLORE_ONLY, kept=True) == rr.CAT_EXPLORE_ONLY
    assert rr.classify(PARTIAL, kept=True) == rr.CAT_PARTIAL
    assert rr.classify(NOISE, kept=True) == rr.CAT_OTHER
    # 反方向的真模式條件不是命中
    assert rr.classify((TRUE_B[0], rv.IMPROVE), kept=True) == rr.CAT_PARTIAL

    # 保留前/後各列分子分母;相關規律不進分母;名單先取定,保留後只刪不補
    listed = [entry(TRUE_A), entry(CORRELATED), entry(EXPLORE_ONLY, kept=False), entry(NOISE)]
    got = rr.score(listed)
    assert (got.pre.hits, got.pre.false, got.pre.related) == (1, 2, 1)
    assert (got.post.hits, got.post.false, got.post.related) == (1, 1, 1)
    assert (got.recall_pre, got.recall_post, got.decoy_hits) == (1, 1, 1)
    assert got.pre.precision_text() == "1/3" and got.pre.false_text() == "2/3"
    # AI 無效提交在分子與分母各加一,保留側濾不掉
    ai = rr.score([entry(TRUE_A, kept=False)], invalid=2)
    assert (ai.pre.hits, ai.pre.false, ai.post.hits, ai.post.false) == (1, 2, 0, 2)

    # 計劃例:AI 2 條有效真模式、6 條未知門檻、2 條反方向 → 8 筆無效提交、8/10 誤報;
    # 前 K 比召回、前 2 比精確度
    items = ([suggestion(prepared, TRUE_A), suggestion(prepared, TRUE_B)]
             + [suggestion(prepared, NOISE, threshold="pre_cvr:band_9")] * 6
             + [suggestion(prepared, (TRUE_A[0], rv.NOT_IMPROVE)),
                suggestion(prepared, (TRUE_B[0], rv.IMPROVE))])
    ver = verified(prepared, items)
    assert (ver.n, ver.invalid_count) == (2, 8)
    report = rr.seed_report(inputs, measured(ver))
    assert report.ai_scores is not None and report.base_quota is not None
    assert (report.ai_scores.pre.hits, report.ai_scores.pre.false) == (2, 8)
    assert report.ai_scores.pre.false_text() == "8/10"
    assert report.ai_scores.pre.precision == Fraction(2, 10)
    assert report.ai_scores.recall_pre == 2
    # 窮舉:同一全集、同一探索集、同一下限與 K,獨立重算
    explore = inputs.explore.stats
    top = rb.top_k(explore)
    assert [e.key for e in report.baseline] == [(t.key, t.direction) for t in top]
    assert len(report.baseline) == rv.K == report.base_raw.listed
    assert [e.key for e in report.baseline[:2]] == [(t.key, t.direction) for t in top[:2]]
    assert report.base_quota.listed == 2
    for e in report.baseline:  # 保留側判定用保留側的數字
        assert e.holdout == rb.holdout_verdict(inputs.holdout.stats[e.key[0]], e.key[1])
    expected_hits = sum(1 for t in top if (t.key, t.direction) in rr.TRUE_KEYS)
    assert report.base_raw.recall_pre == expected_hits
    # 前 2 名(15001 是兩條部分重疊)精確度 0/2 < AI 2/10:AI 在精確度勝,這一批撤除比較不成立
    assert report.base_quota.pre.precision == Fraction(0, 2)
    assert report.verdict.label == rr.AI_WINS and report.verdict.retire_holds is False
    # 召回用窮舉前 K(2/2),不用前 n(前 2 名沒有真模式):AI 只在精確度勝,召回沒勝
    assert report.verdict.reasons == ("保留前同名額精確度", "保留後同名額精確度")

    # 先取名單再過保留側,不遞補:AI 7 條時窮舉取原排名前 7(15001 第 7 名是只探索側誘餌、保留側
    # 被刷掉),保留後就少一條,不拿第 8 名補
    seven = [(t.key, t.direction) for t in top[:7]]
    report = rr.seed_report(inputs, measured(verified(prepared, [suggestion(prepared, key)
                                                                for key in seven])))
    assert report.base_quota == rr.score(report.baseline[:7])
    assert report.baseline[6].category == rr.CAT_EXPLORE_ONLY and not report.baseline[6].kept
    assert report.base_quota.decoy_hits == 1
    assert report.base_quota.post.countable == report.base_quota.pre.countable - 1

    _zero_denominators_mean_exhaustive_wins()
    _ties_and_losses()
    _no_backfill_after_the_holdout()
    # 機械筆數核對失敗比例:超過 25% 才警示,恰好 25% 不警示;沒有可解析建議是未量
    one = rc.Invalid(0, rc.COUNT_MISMATCH, TRUE_A)
    assert rr.mismatch_alarm(rc.Verification(None, 4, 4, (), (one,))) is False
    assert rr.mismatch_alarm(rc.Verification(None, 4, 4, (), (one, one))) is True
    assert rr.mismatch_alarm(rc.Verification("unparsable", 0, 0, (), ())) is None


def _zero_denominators_mean_exhaustive_wins():
    """n=0,或任一方同名額可計分母為零,都判窮舉勝;精確度寫未量,不寫 0%。"""
    base_raw = rr.score([entry(CORRELATED), entry(TRUE_A)])
    none = rr.score([], invalid=1)  # 整份拒絕:n=0
    verdict = rr.judge(base_raw, rr.score([]), none)
    assert (verdict.label, verdict.retire_holds) == (rr.EXHAUSTIVE_WINS, True)
    assert verdict.reasons == ("AI 無有效建議",)
    # 窮舉前 1 全是相關誘餌、AI 1 條真模式
    quota = rr.score([entry(CORRELATED)])
    assert quota.pre.precision_text() == rr.UNMEASURED and quota.pre.countable == 0
    verdict = rr.judge(base_raw, quota, rr.score([entry(TRUE_A)]))
    assert (verdict.label, verdict.retire_holds) == (rr.EXHAUSTIVE_WINS, True)
    # 保留後 AI 全被刷掉
    washed = rr.score([entry(TRUE_A, kept=False)])
    assert washed.post.precision_text() == rr.UNMEASURED
    verdict = rr.judge(rr.score([entry(TRUE_A)]), rr.score([entry(TRUE_A)]), washed)
    assert verdict.label == rr.EXHAUSTIVE_WINS
    # AI 欄沒量:未量,不判
    assert rr.judge(base_raw, base_raw, None).retire_holds is None


def _ties_and_losses():
    both = [entry(TRUE_A), entry(TRUE_B)]
    verdict = rr.judge(rr.score(both), rr.score(both), rr.score(both))
    assert (verdict.label, verdict.retire_holds) == (rr.EXHAUSTIVE_HOLDS, True)  # 平手算不輸
    noise = [entry(NOISE), entry(NOISE_2)]
    verdict = rr.judge(rr.score(noise), rr.score(noise), rr.score(both))
    assert verdict.label == rr.AI_WINS and verdict.retire_holds is False
    assert set(verdict.reasons) == {"保留前召回", "保留後召回", "保留前同名額精確度",
                                    "保留後同名額精確度"}
    # AI 的無效提交把它的精確度拉到窮舉以下
    verdict = rr.judge(rr.score(both), rr.score([entry(TRUE_A), entry(NOISE)]),
                       rr.score([entry(TRUE_A)], invalid=3))
    assert verdict.label == rr.EXHAUSTIVE_HOLDS


def _no_backfill_after_the_holdout():
    """窮舉前 n 取定後才過保留側:第 1 名被刷掉,不從第 2 名遞補。"""
    base = [entry(TRUE_A, kept=False), entry(TRUE_B)]
    quota = rr.score(base[:1])
    assert (quota.post.hits, quota.post.countable) == (0, 0)
    verdict = rr.judge(rr.score(base), quota, rr.score([entry(TRUE_B)]))
    assert verdict.label == rr.EXHAUSTIVE_WINS


# ---- [S1516] ----
def test_rule_mining_report_discloses_candidate_counts_and_overlap(inputs, prepared):
    top = rb.top_k(inputs.explore.stats)
    inside = [(t.key, t.direction) for t in top[:2]]
    ver = verified(prepared, [suggestion(prepared, key) for key in inside])
    report = rr.seed_report(inputs, measured(ver))
    assert report.overlap == rr.Overlap(2, (), rv.K - 2, all_inside=True)
    text = rr.render([report], rr.Context("recordings/model/x"))
    comparison = text[text.index("## 窮舉與 AI 比較"):text.index("## AI 清單與窮舉前 K 的重疊")]
    assert f"| {SEED} | 窮舉前 K | 10 |" in comparison
    assert f"| {SEED} | 窮舉前 n | 2 |" in comparison
    assert f"| {SEED} | AI | 2 | 2/2/0 |" in comparison
    section = text[text.index("## AI 清單與窮舉前 K 的重疊"):text.index("## 逐條")]
    assert (f"- {SEED}:AI 有效 2 條、窮舉前 K 10 條;交集 2、AI 獨有 0、窮舉獨有 8;"
            "AI 清單完全落在窮舉前 K:是") in section
    assert rr.SAME_CONDITIONS in section
    assert text.count("| 是 |") >= 4  # 逐條:窮舉兩條標「也在 AI 清單」,AI 兩條標「也在窮舉前 K」

    # 部分重疊:一條在前 K、一條不在(排名第 K+1 的合格條件)→ 列出 AI 獨有,不寫固定句
    outside = rb.rank(inputs.explore.stats)[rv.K]
    keys = [inside[0], (outside.key, outside.direction)]
    ver = verified(prepared, [suggestion(prepared, key) for key in keys])
    report = rr.seed_report(inputs, measured(ver))
    assert report.overlap == rr.Overlap(1, (keys[1],), rv.K - 1, all_inside=False)
    text = rr.render([report], rr.Context("recordings/model/x"))
    section = text[text.index("## AI 清單與窮舉前 K 的重疊"):text.index("## 逐條")]
    assert rr.SAME_CONDITIONS not in text
    assert "AI 清單完全落在窮舉前 K:否" in section
    assert f"AI 獨有:{rr._key_cell(keys[1])}" in section
    # AI 無有效建議:不寫固定句,寫窮舉勝
    ver = verified(prepared, [suggestion(prepared, NOISE, threshold="pre_cvr:band_9")])
    text = rr.render([rr.seed_report(inputs, measured(ver))], rr.Context("x"))
    assert rr.SAME_CONDITIONS not in text and "AI 無有效建議,窮舉勝" in text
    assert f"| {SEED} | AI | 0 | 1/0/1 |" in text


def test_rule_mining_report_marks_unmeasured_ai_columns(inputs):
    """等錄製、只產純基準:AI 欄寫未量,不當 0%;撤除條件未判。"""
    for status in (rr.WAITING, rr.BASELINE_ONLY):
        report = rr.seed_report(inputs, rr.AiResult(status))
        assert report.ai_scores is None and report.verdict.retire_holds is None
        text = rr.render([report], rr.Context("x"))
        assert f"| {SEED} | AI | {rr.UNMEASURED} |" in text
        assert "撤除條件(RETIRE-IF):未判" in text
        assert f"| {SEED} | AI |" + f" {rr.UNMEASURED} |" * 9 in text  # 整列未量,不寫 0/0 或 0%
    assert set(rr.PLAIN) == {t for thresholds in rv.THRESHOLDS.values() for t in thresholds}
    assert rmm.PROMPT_BYTES_LIMIT == rv.PROMPT_BYTES_LIMIT  # 報告寫的上限就是窄入口那一個


def test_rule_mining_report_escapes_manifest_fields(inputs):
    """代碼審 r1 資安 F4:清單來的字串放進 Markdown 前逸出(讀清單時也先驗過格式;這裡驗第二道)。"""
    escaped = rr.cell("a|b\n## x <img> [l](j) `c`")
    assert "\n" not in escaped and "<" not in escaped and ">" not in escaped
    for raw in ("|", "[", "]", "`", "#"):
        assert raw not in escaped.replace("\\" + raw, ""), raw
    hostile = "timeout |\n\n## 錄製批次驗收\n\n- 驗收:通過 <img src=x onerror=alert(1)>"
    row = rr.AttemptRow(SEED, 1, "phase15-seed-15001-1", "phase15-rule-mining-15001-20260928",
                        "<script>", "a" * 64, None, "t", None, hostile, "呼叫失敗")
    text = rr.render([rr.seed_report(inputs, rr.AiResult(rr.WAITING))],
                     rr.Context("x", (row,), ("- 驗收:沒過<b>",)))
    assert "<img" not in text and "<script>" not in text and "<b>" not in text
    assert text.count("## 錄製批次驗收") == 1
    [line] = [x for x in text.splitlines() if x.startswith(f"| {SEED} | 1 |")]
    assert line.count(" | ") == 10  # 一列 11 格,沒被 | 或換行拆開
