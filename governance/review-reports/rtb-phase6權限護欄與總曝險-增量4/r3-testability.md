severity: clean

鏡頭:可測性(S360–S370,S362 隨增量 3、只看敘述是否清楚)——逐條能不能真的寫出會翻紅的測試、拿掉對應實作會不會真的紅、有沒有會假綠的斷言,重點覆核 [S368] 改成對照情境數字、[S369] 用查詢計畫、[S370] 舊庫這三條。

已核對:凍結快照第 298–427 行(整個「## 增量 4 設計:可觀測」節,含審計修正紀錄);`src/rtb/executor/inbox_store.py`(`write_stops` 建表無任何索引、`_migrate_columns`/`_missing_columns`/`ADDED_COLUMNS` 補欄位流程、`Disposition`/`StopKind` 列舉、`record_stop`);`src/rtb/executor/attempt_store.py`(`aggregate_used`/`_counted`、`ADDED_COLUMNS` 已含 `tenant`/`reserved_amount`、`unresolved_count_query` 的部分索引寫法);`src/rtb/executor/execution.py`(`_sign`/`_take`/`_stop`,核對「沒有簽發到租戶的舊路徑」現況描述);`src/rtb/executor/capability_signer.py`(`load_tenants`/`aggregate_limit` 缺欄位當 0);`src/rtb/sqlitekit.py`(只有一種交易入口 `immediate_transaction`,確認「不另寫唯讀交易」屬實);`tests/executor/test_aggregate_limit.py`(`stops`/`used`/`drive`/`store_only` 造資料手法、`test_old_attempts_count_against_every_tenant_until_they_leave_the_window` 的 `ALTER TABLE ... DROP COLUMN` 造舊庫技巧、`test_an_old_inbox_database_accepts_every_current_block_code`);`tests/executor/test_attempt_store.py:852-859`(`EXPLAIN QUERY PLAN` 斷言先例);以及本目錄 `r1-testability.md`、`r2-testability.md` 兩份前輪報告(確認哪些洞已折平)。

## 前兩輪找到的洞這輪逐一重新驗證,確認都真的落地,不是措辭補丁

- r1 F1(S362 掛在本增量合約清單卻做不出來,major):現況快照第 355 行(對應 r3 第 348 行左右)仍明講「這條隨增量 3 實作交付,測試寫在增量 3 的核可測試檔」,跟其餘九條句式分開標示;`Disposition` 列舉(`src/rtb/executor/inbox_store.py:79-86`)目前確實沒有「待核可」成員,核可使用表也還沒建,跟文字一致,不會被誤讀成本增量要交。已解,沒有回退。
- r1 F2(S364 表清單寫死會漏增量 3 新表,minor):現行文字「表清單從資料庫當下讀出,不寫死」原樣保留在 r3(第 364 行)。已解。
- r1 F3 / r2 呼應(查詢四把舊列算進已用、查詢五排除舊列,兩邊對不起來):現在的「通過的」明講「Phase 6 之前沒有租戶的舊列…以新預算全額出現並標明是舊列」,跟 `_counted`(`src/rtb/executor/attempt_store.py:383-395`)的舊列規則一致,並由 [S368] 專門用混合情境釘住。已解。
- r2 F1(S368「加總應等於已用」單獨看是套套邏輯,因為「目前佔額度的」清單本來就是 `aggregate_used` 內部要加總的那份清單):r3 的 [S368] 已改成「加總應等於情境直接算出的數字(例:加 100、舊改成 30、舊從 900 減到 500 → 630,不拿兩支查詢互相比)」,並在審計修正紀錄 r2 段重申同一個例子。這條現在測的是「這個數字對不對」,不是「兩支查詢字面互相抄」,拿掉或改壞計入規則(例如誤把窗口算成 48 小時、或漏排一筆暫停)都會讓這個釘死的數字對不上,真的會翻紅。已解。

## S360、S361、S363、S365–S367 沿用既有手法,可測、拿掉實作會真的翻紅

跟前兩輪的結論一致,這輪核對程式碼與測試檔後性質沒變:S360/S361 是 `write_stops` 上帶篩選的 `COUNT(*)`,跟 `stops(h)` 直接戳表的手法同構,「同一份提案反覆延後只算一次」有現成場景可以多斷言一次計數;S363 的算法本身已有 S330–S343 守著,新測試只驗證封頂與門檻轉發,拿掉「剩餘 = max(門檻-已用,0)」的封底邏輯會真的紅;S365 直接在既有 `test_f7_end_to_end.py` 的並行情境上多查詢三支,核對到 `h.dsp.writes`/`stops(h)` 算出的獨立期望值,不是自己跟自己比;S366「舊資料庫補索引」跟 `test_an_old_inbox_database_accepts_every_current_block_code` 同構,拿掉任一索引,`PRAGMA index_list` 或後續 [S369] 的查詢計畫檢查都會抓到;S367「沒給範圍時應拒絕」可以直接 `pytest.raises`,「別的租戶的與範圍外的應不出現」是逐項排除斷言,不是加總比對,不會假綠。

## [S369] 用查詢計畫:文字本身已要求「不含全表掃描」,不是只抄先例的「含 INDEX」弱斷言

先例 `test_the_unresolved_counts_use_the_partial_indexes_instead_of_rereading_every_key`(`tests/executor/test_attempt_store.py:852-859`)只斷言 `"INDEX" in plan` 與 `"CORRELATED" not in plan.upper()`,這個寫法本身比「不含全表掃描」弱——SQLite 的 `EXPLAIN QUERY PLAN` 對兩表查詢可能一邊 `SEARCH ... USING INDEX`、另一邊 `SCAN TABLE`,只斷言字串裡出現過 `INDEX`會漏掉另一邊真的在掃全表的情況。但 r3 的 [S369] 合約文字本身已經明講「查詢計畫應不含全表掃描」,這比先例的斷言方式更嚴——照字面寫測試的人得去檢查 `EXPLAIN QUERY PLAN` 裡沒有 `SCAN TABLE`(或至少沒有無索引限制的 `SCAN`),而不能照抄先例只驗 `INDEX` 字樣有出現。三個 `write_stops` 索引都以「種類」開頭,而查詢一/二/五的「被停下的」內部一定會帶種類篩選(`AGGREGATE_LIMIT_REACHED` 或 `TABLE_FULL` 是查詢函式寫死的條件,不是呼叫端給的),所以文字講的「只依廣告」「只依時間」實際上都跟「租戶,種類,時間」/「廣告,種類,時間」/「種類,時間」三個索引的最左字首對得上,沒有選不到索引的組合;「依租戶和時間查嘗試紀錄第一列」對應新的 `(tenant, written_at) WHERE seq = 1` 部分索引,跟既有 `attempts_first_rows`(`campaign_id) WHERE seq = 1`)同一種做法,先例可循。這條可測、不假綠,只是要提醒:寫測試時不要照抄先例的斷言寬鬆度,要照 [S369] 自己的字面去驗「不含全表掃描」。

## [S370] 舊庫:兩步遷移的順序本身就是測試要打的靶,不是重複測已驗證過的東西

`attempt_store.ADDED_COLUMNS` 現在已含 `tenant`/`reserved_amount`(增量 1 已合併),`inbox_store._migrate_columns` 的「先 `ALTER TABLE ADD COLUMN`、缺才補」流程已有先例(`test_old_attempts_count_against_every_tenant_until_they_leave_the_window` 用 `ALTER TABLE attempts DROP COLUMN tenant` 造出「Phase 6 之前」的資料庫)。[S370] 要打的靶是文字裡點名的那個坑:「嘗試紀錄的部分索引參照的租戶欄是後補的欄位…放進建表語句會讓舊資料庫一開就失敗,所以放在補欄位流程裡、補完欄位之後才建」——如果實作把新索引寫進靜態 `SCHEMA` 字串(而不是補完欄位之後才建),對一個連 `tenant` 欄都沒有的真舊庫,`connect()` 時的 `executescript(SCHEMA)` 會在 `ALTER TABLE` 跑之前就因為索引引用不存在的欄位丟 `sqlite3.OperationalError`,直接在 `InboxStore.__init__` 炸出來。[S370] 的斷言「不丟例外、舊列不變」剛好精準對到這個具體的迴歸情境,拿掉「補完欄位後才建索引」這個順序保證,測試會真的紅,不是重複驗證增量 1 已經測過的東西。

## 結論

十一條合約(S360、S361、S363–S370,S362 隨增量 3 敘述本身清楚)這輪核對下來都可測、拿掉對應實作會真的翻紅,能沿用 `tests/executor/fakes.py` 的 `Harness`/`store_only`、`ALTER TABLE ... DROP COLUMN` 造舊庫、`EXPLAIN QUERY PLAN` 拼字串這三種既有手法,不需要新造測試基礎設施。前兩輪抓到的四個洞(S362 誤讀範圍、S364 表清單寫死、查詢四五舊列對不起來、S368 加總套套邏輯)這輪逐一回頭核對程式碼與測試先例,確認都已經是真的落地而不是措辭補丁。這輪唯一值得留意但不到 blocking 的地方是 [S369]:先例測試的斷言寫法比合約文字要求的「不含全表掃描」寬鬆,寫測試時要照 [S369] 自己的字面驗證,不要照抄先例的斷言寬鬆度——這是提醒,不是缺口,因為合約文字本身已經把正確的驗收標準寫清楚了。
