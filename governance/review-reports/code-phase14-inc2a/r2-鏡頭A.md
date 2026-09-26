severity: major

# 代碼審 r2 鏡頭A:DSP 儲存層、推導、遷移、同時寫入與讀取

審材:`r2-delta-dsp.patch`(seed.py / server.py / store.py / 兩支測試)逐 hunk 讀完;需要時對照 `src/rtb/dsp/store.py`、`src/rtb/sqlitekit.py` 現檔。
`PYTHONPATH=src .venv/bin/python -m pytest -q tests/dsp` 今天(2026-09-26)跑的結果:254 passed。

---

## 發現 1:溢位測試的 HTTP 段仍用真時鐘配固定 NOW,2026-10-01 UTC 起一定翻紅(10-02 起變 404)
severity: major
blocking: 是

引句:「    dsp = start_dsp(seed=False)  # 真時鐘:跨了幾天就少幾天,合計仍遠超上限」
引句:「    assert status == 200 and body["impressions"] > 2**63 - 1」
引句:「固定日期的測試一律注入固定時鐘(代碼審 r1 鏡頭1:原本用真時鐘配固定 NOW,過了 UTC 午夜就翻紅)。」

這是 r1 鏡頭1 發現 1 同一類的問題(測試用真時鐘,卻搭配寫死的日期),而且出現在這輪新加的測試 `test_seven_day_totals_never_overflow_into_a_server_error` 裡。測試檔開頭宣稱「固定日期的測試一律注入固定時鐘」,但這一支沒做到,檔內說法前後不一。

- 種子:`seed_daily("c1", [huge]*7, now=NOW)`,NOW 是 2026-09-25,存進 9/18 到 9/24 這七天,沒有樣板。
- `start_dsp` 另開一個子行程,用真時鐘。這輪改成「比最新存下日期還新的完成日,沒有樣板就是沒資料」,所以真實日期每往後一天,7 天窗裡就少一天有資料。
- 2026-10-01 當天,7 天窗只剩 9/24 一天的資料,impressions 剛好等於 2**63-1,`> 2**63 - 1` 不成立,測試翻紅。
- 2026-10-02 起,七天全都沒資料,`window_figures` 回 None,端點變成 404,`status == 200` 也不成立。
- 程式註解寫「跨了幾天就少幾天,合計仍遠超上限」,這句只在 9/30 之前成立。

最小重現(把測試複製到 scratchpad,只把 NOW 往前推 5 天,效果等同真實日期來到 10/01;repo 沒動):
```
$ cd <scratchpad>/r2a   # tests/ 從 repo 複製,src 是指回 repo 的符號連結
$ sed -i '' 's/^NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)/NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)/' tests/dsp/test_investigation_data.py
$ PYTHONPATH=…/src python -m pytest -q tests/dsp/test_investigation_data.py -k never_overflow
>       assert status == 200 and body["impressions"] > 2**63 - 1
E       assert (200 == 200 and 9223372036854775807 > ((2 ** 63) - 1))
1 failed
（NOW 改回 9/25 → 1 passed）
```
另一個直接在儲存層重現 10/02 的腳本(`scratchpad/r2a/e2.py`),輸出:
```
2026-10-01T00:00:00+00:00 7d -> 9223372036854775807
2026-10-02T00:00:00+00:00 7d -> MetricsNotFound -> (404, 'metrics_not_found', False)
```
建議改法:這段 HTTP 改用 `DspServer(..., store_clock=_clock(NOW))` 在同一個行程裡起伺服器(同檔 `test_a_missing_first_day_seeds_an_empty_one_day_window` 已經這樣做),或是種子帶樣板。
同一檔另一支也用真時鐘:`test_the_daily_and_past_adjustment_endpoints_are_get_only_and_idempotent`。我查過,它在任何日期都會回 200:沒資料的日子回 no_data 桶,不是 None;過去調整也不受日期影響。所以不列。

## 發現 2:舊資料庫裡「補不回前值」的第一筆預算操作,會一直被當成最近一次加額,過去調整永遠判證據不足
severity: minor
blocking: 否

引句:「            if before is None or after > before:」
引句:「都沒有(補不回來)的那筆若比任何已知加額新,就回它、前值 None:分不出是不是加額,不能跳過」

情境:一個舊資料庫,沒有舊種子表。某廣告的第一筆 `update_budget` 在 200 天前(new_budget=120),後面接一筆減額(100 天前,120→110)。
- 遷移時,第二筆靠接龍補回前值 120。第一筆補不回來,只能記前值 null。
- `_latest_raise` 從新往舊找:第二筆是減額,跳過;第一筆因為 `before is None` 就被回傳。
- 之後只要沒有新的加額,端點就一直回這筆:days_ago 一路往上長,前值一直是 null。
- 領域 `_past_decision` 看到 `budget_before is None`,判 `MISSING_ROW_VALUE` 證據不足(src/rtb/domain/nine_rules.py:271)。
- 結果:只要沒有別人加額,這個廣告在過去調整這一步永遠證據不足。如果代理是唯一的寫入者,就沒有東西能解開它。

重現(`scratchpad/r2a/e3.py`:舊資料庫 → 開庫遷移 → 再做一筆減額 → 60 天後再讀):
```
migrate: [PastAdjustment(days_ago=200, budget_before=None, budget_after=120, before={…None}, after={…None}, committed_at='2026-03-09T12:00:00+00:00')]
60 天後: 260 None 120
```
嚴重度只給 minor,理由:
- r1 的寫法在這個情境回 AdjustmentsNotFound,一樣卡住,所以這不是 r2 新加的洞。
- 只影響舊資料庫。
- 正式九條決策在 2a 還沒啟用。

仍然值得在計劃或 Mock-DSP 筆記寫一句邊界:補不回前值的那筆多舊之後就不再算「最近一次加額」,或者明寫接受永久證據不足。現在圖譜 `Systems/Mock-DSP.md` 第 123 行只寫了「照樣回、`budget_before` 為 null」,沒寫它會一直卡住。

---

## 上輪 DSP 相關項目驗收

| 上輪項目 | 驗收 |
|---|---|
| 鏡頭1-1 真時鐘配固定 NOW | 舊的幾支已改注入時鐘;**新加的溢位測試又犯同一類問題**(本報告發現 1) |
| 鏡頭1-2 七天全缺 → 7d 404 | 已修:`window_figures` 回 None,端點 404;一致性核對不誤報,測試已加 |
| 鏡頭1-3 合計溢位成 500 | 已修:讀取時用 Python 整數加總、不寫回資料庫;金額上限提前在種子邊界擋 |
| 鏡頭1-5 一致性第三條恆真 | 已撤,說明文字也同步改了;新的殺傷力測試同時抓得到兩條投影算錯 |
| 鏡頭3-1 物化綁真時鐘 | 已修(時鐘可注入) |
| 鏡頭3-2 讀取搶寫入鎖 | 已修:四支讀取都包在 `read_snapshot` 裡。另驗證了伺服器「每個請求新開一個 store」這條路徑:別的連線握著寫入鎖時,開庫加讀取 0.0 秒完成(`scratchpad/r2a/e1.py`) |
| 鏡頭3-3 遷移補不回 → 404 | 已修:改成回前值 null。留下的尾巴見發現 2 |
| 外家 finder 1 物化與投影各讀一次時鐘 | 已修:每個端點只讀一次 `_today()`,測試已加 |
| 外家 finder 2 鎖外記住最新日期 | 已修:`_persist_days` 在寫入鎖內才讀最新日期,測試已加 |
| 外家 finder 3 舊 REAL 金額讓升級失敗 | 已修:存不下的記成缺值(包括 1e308 這類 quantize 會丟 InvalidOperation 的值,也有接住) |
| 外家 finder 5 單日合法、七日溢位 | 已修(同鏡頭1-3) |
| 外家否決 1 七日物化整批回滾 | 已修(讀取不再物化) |
| 外家否決 2 摘要與列不同快照 | 已修:同一個 `read_snapshot`。插隊測試用舊寫法會得到 (1,0),確實抓得到 |
| spec-conformance 1、2 | 已修:不超過 50 筆時維持 `{"history": [...]}` 形狀;超過才附 `summary`,並加上 `truncated: true` |
| 偏離 (b) DSP 自己留一份 13 位金額規則 | 已核對:`_AMOUNT_TEXT` 跟 `rtb.domain._checks._FIXED_AMOUNT` 的正則式逐字相同,負號、前導零、非 ASCII 數字的處理一致 |

## 查過、沒發現問題的地方(不列發現)
- **寫入端補寫與讀取推算是否一致**:
  - `_persist_days` 用 `derive_days({}, newest, …)` 補的日子全部都比最新日期新。讀取端碰到同樣的日子,也走「比最新日期新 → 樣板或沒資料」這條分支,所以寫入前後同一個讀取答案一樣。
  - 補完之後最新日期一定 ≥ 提交日前一天,保留期清理不會刪掉最新那天。
  - 最新日期在寫入鎖內讀,並行寫入時後到的那個什麼都不做。
- **跨午夜與時鐘倒退**:
  - 提交時間被 `_not_before_last` 往後墊高、或寫入端與讀取端日期差一天時,寫入端可能多補到讀取端的「今天」或更晚。讀取端一律忽略 `day >= today` 的桶,等那天變成完成日時,值跟推算的一樣。
- **保留期**:
  - 讀取只看最近 7 天,或最近加額的 D±3,前者一定在 30 天內,後者有保護。
  - 清理只可能影響「換了最近加額」那一次寫入本身。
- **極值與型別**:
  - `cents_of`:float 1e13 走科學記號,`1.005` 這種不是整數分的一律拒收。
  - `money_text(-5)` 輸出 `-0.05`,讀取白名單收得下。
  - 13 位整數加兩位小數共 15 位有效數字,轉浮點再轉回來不會變(DBL_DIG=15)。
  - 七天合計最多約 7e15 分,遠低於 SQLite 整數上限,而且只在記憶體裡算。
- **巢狀交易**:
  - `read_snapshot` 的呼叫端(四支讀取與 seed 的核對)都不在寫入交易裡呼叫,不會遇到 `BEGIN` 巢狀。
  - 寫入路徑沒有呼叫讀取端點。

## 圖譜鏡頭(LUMOS-IMPACT: origin/main..HEAD,固定席 9 篇;派工說明末尾沒附筆記,改用 `lumos impact --diff origin/main..HEAD --ranked` 取)
- **Systems/Mock-DSP**:本鏡頭的主要對象,不破壞。
  - 「一次寫入全有全無」:`_persist_days` 放在 `_execute_in_transaction` 裡、COMMIT 之前,出錯會跟著整筆回滾。
  - 冪等:重送走 `_existing_operation` 提早返回,不會補寫也不會清理。
  - F1 提交前逾時、版本衝突:這兩條走的程式碼沒被動到。
  - 第 123、124 行摘要描述的內容(讀取不寫資料庫、每個端點讀一次時鐘、補值表不改操作紀錄)跟程式一致。只缺發現 2 那個「永遠卡住」的邊界。
- **Systems/共用行程基礎**:不影響。只用到既有的 `read_snapshot`,sqlitekit 本身沒改;`DspServer` 多一個 `store_clock` 參數,沒動到只綁 127.0.0.1 與故障旗標那兩條。
- **Systems/確定性指標計算**:不影響,而且方向一致。缺值一律是 None 或 404,絕不變 0(`window_figures` 七天全缺回 None,有欄位缺值那欄就是 null)。
- **Systems/任務流程領域模型、分析行程流程與檢查點、提案收件口、執行迴圈**:DSP 的 delta 沒碰到這些模組的程式,它們的不變量(提案白名單、證據新鮮度、步驟落地、收件口修訂、F1–F4/F7 執行面)都不受影響。
- **Systems/正式九條判斷領域規則、評估與Jev決策點**:DSP 的 delta 只在測試裡呼叫 `_past_decision`,拿來驗證前值 null 會判證據不足,沒改領域程式。DSP 刻意不匯入領域層,這點仍然守住,金額規則是 DSP 自己留一份(見偏離 (b) 的驗收)。

## 表態記錄(棧別效能題)
- 「七天加總最多約 7e15 分,也遠低於 SQLite 整數上限」:驗過成立(7×(10^15−1) < 2^63−1)。
- 「13 位加兩位小數是 15 位有效數字,分析端轉浮點不會差一分」:成立(IEEE double 保證 15 位十進位來回不變)。
- 「寫入前後任何讀取的答案都不變」:依上面「查過、沒發現問題的地方」第一點的推導成立,`test_latest_raise_daily_buckets_survive_rolling_retention` 也有實測。反例只存在於種子重種、換掉樣板的時候,那屬於種子專用路徑。

總結:DSP 相關的上輪項目都確實修好;這輪新加的溢位測試重犯「真時鐘配固定 NOW」,五天後會讓 CI 翻紅,需要修;另有一個舊資料庫邊界值得寫進圖譜。
