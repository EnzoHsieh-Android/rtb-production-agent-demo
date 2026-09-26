severity: minor

# 代碼審 r3 鏡頭A:全部修正差異(正確性、同時寫入、邊界、測試殺傷力)

審材:`r3-delta.patch` 逐 hunk 讀完。需要時對照 repo 現檔。
- 測試:`PYTHONPATH=src .venv/bin/python -m pytest -q tests/dsp tests/analyzer/test_dsp_client.py tests/analyzer/test_investigation_reads.py tests/analyzer/test_investigation_review.py`,結果 350 passed。
- claims 雜湊:五份清單裡改到的四支檔(store.py、dsp_client.py、instrumented.py、test_dsp_client.py),用 `shasum -a 256` 對實檔,全部一致。
- 重現腳本放在 scratchpad `r3a/`,repo 沒動。

本輪沒有 blocking 級問題。新找到 3 條 minor,都不觸發新一輪。

---

## 發現 1:「近期筆數」核對的誤判方向寫反了,而且時鐘同步時也會誤判
severity: minor
blocking: 否

引句:「DSP 時鐘比這裡快時,近期窗只會比 DSP 那邊窄,不會誤判;比這裡慢才可能」

**兩邊怎麼切「最近 3 天」**
- 讀取層:`committed_at > now − 3 天`。`now` 是這一步開始時拿到的時刻,在打 DSP 之前取。
- DSP 摘要:`committed_at > dsp_now − 3 天`(file: `src/rtb/dsp/store.py:874-880`)。`dsp_now` 是 DSP 處理這次讀取的時刻。

**為什麼方向寫反**
- 當 `dsp_now > now`(DSP 時鐘較快,或單純因為讀取本來就在 now 之後發生),DSP 的窗起點比較晚,數得比較少。
- 落在 `(now−3d, dsp_now−3d]` 這一小段的加額,讀取層會算進去,DSP 摘要不會。
- 結果是「列比摘要多」,整份判 invalid。
- 所以會誤判的是「DSP 比這裡快」這一邊,正好跟註解相反。
- 就算時鐘完全同步也會發生:一步最多約 58 秒的讀取延遲,本身就讓 `dsp_now > now`。

**重現**(`r3a/skew.py`:同行程起 DSP 並注入時鐘。60 筆加額,最新一筆 committed_at = NOW−3d+10s;分析端 now=NOW;DSP 讀取時刻 = NOW+lag)
```
lag=0s  read_query_options reason: None
lag=5s  read_query_options reason: None
lag=30s read_query_options reason: invalid   (摘要 budget_changes_last_3d=0)
```

**影響與建議**
- 影響是保守的:那一步證據不足,不會錯提案。只在 3 天或 7 天界線附近幾十秒內發生,所以列 minor。
- 算內部不一致,照規定要報:`dsp_client.history_recent_agrees` 的說明寫反了,圖譜〈分析行程流程與檢查點〉也寫反了。
- 建議把兩處都改成「DSP 讀取時刻晚於這一步的 now(時鐘偏快或讀取延遲)時,界線附近可能誤判 invalid」。
- 更根本的修法(可選):讀取層的界線改用列裡最新一筆 `committed_at` 當上界,或者放寬一個讀取逾時的容忍量。

## 發現 2:「最近 7 天」這半核對沒有測試咬住,刪掉之後測試照樣全綠
severity: minor
blocking: 否

引句:「and summary["budget_changes_7d"] >= last_7d)」

**為什麼沒被咬住**
- `test_a_truncated_history_summary_cannot_undercount_recent_rows` 的第三組輸入,把 7 天和 3 天同時設成 0。
- 因為 3 天那條已經先判 invalid,拿掉 7 天這條,這組照樣紅,測試分辨不出來。

**這條分支其實走得到**
- 情境:列裡有一筆 5 天前的加額,摘要寫 `budget_changes_7d=0`、`budget_changes_last_3d=0`。
- 這組摘要過得了 `_summary_agrees`,只有 7 天那條會擋。

**變異重現**(複製 src/tests 到 `r3a/mut`,只把回傳改成 `bool(summary["budget_changes_last_3d"] >= last_3d)`)
```
tests/analyzer/test_dsp_client.py tests/analyzer/test_investigation_reads.py tests/analyzer/test_investigation_review.py
96 passed
```

**建議**:加一組輸入:5 天前的加額,摘要 7d=0、3d=0、旗標 False,斷言結果是 invalid。

## 發現 3:舊庫遷移把五欄都記成缺值時 no_data 仍是假,整份逐日被判 invalid
severity: minor
blocking: 否

引句:「+            values = _within_daily_limits(」

**出事的順序**
- r3 起遷移會把超過每天上限的**計數**也記成缺值。r2 只會把金額記成缺值,所以以前不會出現「五欄全空」。
- 舊版一天的 impressions、clicks、conversions 若都大於 `(2**63−1)//7`,且金額大於約 1.43e12,五欄會全部變 None。
- 但 `no_data` 照抄舊值 0(file: `src/rtb/dsp/store.py:585-590`)。
- DSP 於是回出「五欄 null、no_data=false」這種列。這違反 `DailyRow` 自己寫的「缺資料那天五欄都是 None、no_data 為真」。
- 讀取層 `check_daily` 看到這種列,整份逐日不收。
- 這一天的日桶還在最近 7 天內時,每次讀逐日都是 invalid。

**重現**(`r3a/legacy_nodata.py`:舊 schema 第 1 天放 2**62 與 2e12)
```
day1: DailyRow(days_ago=1, impressions=None, clicks=None, conversions=None, spend=None, revenue=None, no_data=False)
check_daily accepted: False
```

**影響與建議**
- 只影響舊庫,而且需要極端值,方向保守,所以列 minor。
- 建議:遷移時五欄都變 None,就把 no_data 設為 1。或者只把超限的欄記缺值,但要保證不會出現「全空卻 no_data=0」。

---

## 第 2 輪修復驗收

| r2 項目 | 驗收 |
|---|---|
| 鏡頭A 1 溢位測試真時鐘配固定 NOW | **已修**:HTTP 段改在同一行程起 `DspServer(..., store_clock=_clock(NOW))`,而且種子帶樣板。實測在副本拿掉 `store_clock` 仍綠,原因是有樣板,這支本來就跟日期無關,這樣比只注入時鐘更穩。時鐘守衛有效:在副本把 `_seeded` 的時鐘注入拿掉,`test_seeded_daily_and_adjustment_data_agree_with_windows_and_history` 當場翻紅。`DspServer` 在 `store_clock=None` 時不傳 clock(file: `src/rtb/dsp/server.py:137`),所以守衛確實也管得到每個請求開的儲存層。store 內也沒有別處直接呼叫 `_utc_now`。 |
| 鏡頭A 2 舊庫第一筆補不回前值之後只有減額,永遠證據不足 | **已處理(接受)**:Mock-DSP 寫明了邊界、理由與 `REVISIT:2026-12-31`,方向保守。 |
| 鏡頭B 1 七天加總超出讀取白名單 | **已修**:日桶與樣板每天上限是 `MAX_CENTS//7`、`(2**63−1)//7`,7 天合計 ≤ 上限;加額前後各 3 天的合計更小。1 小時窗仍是單一值、照原上限。有日桶的廣告,1d/7d 不能另用 `seed_metrics` 種。新測試經 HTTP 由讀取層收下 7 天最大值。留下的遷移尾巴見發現 3。 |
| 外家 finder 1 預算調整＋暫停可以少於總數 | **已修**:改成 `==`。新測試兩個反例(49 筆暫停、61 筆總數)都會紅。 |
| 鏡頭B 2 截斷摘要的最近 3 天沒跟列核對 | **已修**:`read_query_options` 用這一步的 now 核對,instrumented 也傳入同一個 now。7 天那半沒有測試咬住,見發現 2;說明方向寫反,見發現 1。 |
| 鏡頭B 3 讀取白名單 RULE 過時 | **已修**:第 69 行 RULE 改成收固定兩位小數字串,並指向單一判準 `_checks`。 |
| 架構 r1 第 3 條 committed_at 還沒有讀取端消化 | **已處理**:計劃把它列為增量 2b 必做。 |

另外查過的地方:
- `read_query_options` 簽章改了,呼叫端只有 instrumented 與兩支測試,都已更新。
- 歷史列的 `committed_at` 經 `_aware` 驗過帶時區,跟帶時區的 now 比較不會丟 TypeError。
- 沒截斷的回應沒有 `truncated`,`history_recent_agrees` 恆為真,既有收據與錄製不受影響。

## 圖譜鏡頭(LUMOS-IMPACT: origin/main..HEAD)

派工尾端沒有附固定席筆記。我自己跑了 `scripts/lumos impact --diff origin/main..HEAD --ranked`,固定席 9 篇,逐篇判:

- **Mock-DSP(INVARIANT)**:
  - 冪等、寫入全有全無、F1 逾時、版本衝突這幾條走的路徑,本輪都沒動。
  - 每天上限在寫入邊界拒收,整份種子不寫,仍然全有全無。
  - 新增的邊界段與 REVISIT 跟程式一致。遷移 no_data 的尾巴見發現 3。
- **任務流程領域模型(INVARIANT)**:不影響。dsp_client 只多匯入領域常數 `RECENT_DAYS`,沒改提案解析、新鮮度與狀態機。
- **分析行程流程與檢查點(INVARIANT)**:
  - 每步落地一列不變:instrumented 只多傳 now,讀取次數不變,[S1113] 那支測試綠。
  - RULE 已更新。
  - 2a 段的誤判方向句寫反,見發現 1。
- **正式九條判斷領域規則**:不影響。只被讀取匯入 3 天切點,沒改判斷。
- **確定性指標計算(INVARIANT)**:「算不出值就沒有值,絕不變 0」仍成立,超限一律缺值或拒收。
- **評估與 Jev 決策點**:不影響。本輪沒碰評估集與收據函式,沒截斷的歷史路徑也沒變。
- **提案收件口、共用行程基礎、執行迴圈(hop 連過來,INVARIANT)**:本輪只重貼 claims 雜湊,實檔核對一致。沒碰送件、冪等鍵、租約、伺服器綁定或故障旗標。

### 表態記錄(可反駁的宣稱)

- 「1d/7d 與加額前後三天的合計一定在讀取白名單上限內」:成立。7×(MAX_CENTS//7) ≤ MAX_CENTS,7×((2**63−1)//7) ≤ 2**63−1,負數也對稱。前提是日桶只從種子、樣板與舊庫遷移這三條路寫入,三條都受上限管。
- 「DSP 時鐘比這裡快時不會誤判」:**推翻**,見發現 1。
- 「單步最壞 58 秒,小於 60 秒租約」:本輪讀取次數沒變,那支測試綠,宣稱未被推翻。

總結:第 2 輪各項都確實修好或已寫明接受;本輪只留三條不擋合入的小問題:一句方向寫反的說明、一條沒被測試咬住的核對分支、一個舊庫遷移的尾巴。
