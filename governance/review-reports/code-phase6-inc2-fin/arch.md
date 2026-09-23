severity: clean

以下逐項核對「這寫法跟專案既有的一不一樣」,均判定與既有做法一致,沒有第二種做法或跨層情況。

[S406] 舊收件表的造法,跟增量 1 [S338] 的測試手法(`tests/executor/test_aggregate_limit.py:298`
`test_an_old_inbox_database_accepts_every_current_block_code`)是同一套技巧:先用目前的原始碼建出
一個 InboxStore,把它自己的 `CREATE TABLE` SQL 字串裡指定的允許值刪掉,模擬「舊版列舉建的表」,
再重新開啟讓 `_proposals_outdated()` / `_rebuild_proposals()` 走一次遷移路徑。新測試只是多插入一筆
資料再驗證遷移後那筆資料不變,屬於同一手法的延伸,不是另一套做法。引句:「conn.executescript(f"ALTER
TABLE proposals RENAME TO p_old; {old_sql};"」與 S338 的對應行 `tests/executor/test_aggregate_limit.py:307`
幾乎逐字相同。前置斷言 `assert old_sql != sql and "'aggregate_limit_reached'" in old_sql` 也照抄 S338 的
`assert old_sql != sql` 風格,多加了一個「真的是增量 1 版」的前置檢查,合理。查過 `_rebuild_proposals`
(`src/rtb/executor/inbox_store.py:356`-`368`)確認重建時會把舊表資料原樣搬進新表,「舊列不變」的斷言
站得住,不是空判斷。

三份清單常數(`SINGLE_RULE_CODES`、`NON_SINGLE_CODES`、`HISTORICAL_CODES`)全部以模組層級 tuple
放在 `test_guardrails.py`,跟既有的 `SINGLE_RULE_CODES`(本來就在同一支檔案、`GUARDRAILS` 表旁邊,
`tests/executor/test_guardrails.py:154`)同一種放法、同一種註解風格(行內加括號說明分類理由),
沒有搬到別的檔案或搬進 `rtb/` 原始碼層。引句:「三份明列清單:之後誰加新擋下原因都得先決定歸哪一份,
忘了就紅」這句放在常數定義正上方,跟 `SINGLE_RULE_CODES` 上方「表上的順序就是程式判斷的順序」的
註解位置習慣一致。

`test_execution.py` 拿掉 `BLOCK_TRIGGERS` 那句「等於整個列舉」的斷言後,查證沒有留下漏洞:
`test_each_failed_precheck_blocks_the_proposal_without_a_write` 仍然是 `@pytest.mark.parametrize("code",
list(BlockCode))`,逐一用 `BLOCK_TRIGGERS[code](h)` 取用觸發函式,以後若加新 `BlockCode` 卻忘了填
`BLOCK_TRIGGERS`,這一行本身就會因 KeyError 紅掉,不需要那條顯式的集合相等斷言來抓。它跟
`test_guardrails.py` 新增的 `test_the_guardrail_table_covers_every_block_code`(三份清單聯集等於整個
列舉、兩兩不交集)是兩張不同的清單(`BLOCK_TRIGGERS` 是「觸發手法」表,三份清單是「歸類」表),
註解說「由護欄表的三份清單斷言取代」講的是「涵蓋完整列舉」這個責任轉移,不是同一張表搬過去,
兩邊各自仍靠自己的機制守住,沒有出現「拿掉舊守衛、新守衛沒接上」的缺口。

[S402] 從 1005 項參數化改成單一測試迴圈,是同一支檔案內、同一個測試函式內部的實作選擇,不涉及
跨層或另一套架構;而且計劃筆記早已把這個決定寫入狀態說明(增量 2 設計審已定案),不是審查時才冒出來
的新做法。順帶一提:這個專案的測試慣例以 `pytest.mark.parametrize` 為主(`tests/executor/*.py` 裡有
33 處使用),用 `next(...)` 找第一個違規值的寫法在本檔案是新出現,但同樣手法在
`tests/analyzer/test_f5_end_to_end.py:91` 已有「用 `next()` 找符合條件的第一項」的先例,只是規模與用途
不同(找特定一筆 vs. 掃過整批找第一個反例);判斷上屬於同層級的測試組織風格差異,不算違反既有架構,
只記一般觀察,不升 major。
