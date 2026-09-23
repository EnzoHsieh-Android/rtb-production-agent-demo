severity: major

## F1 鎖外核可快照不等於交易內實際使用，會同時誤放過時決策與誤擋有效核可
severity: major
blocking: 是 — 破壞 [S512]「只有這一次真的需要的有效核可才免新鮮度」；既可能讓沒有實際使用核可的過時決策寫進 DSP，也可能忽略剛補發的有效核可而錯誤擋下。

引句:「處理一筆傳進來的是開始一筆前查好、用得到的那幾張」

`_approvals` 在獨立交易中預判總曝險並產生 `held`；但真正開始一筆時會在寫入鎖內重新計算曝險。專案已有測試固定這個合法競態：鎖外預判需要總曝險核可，鎖內額度剛好被釋放，最後核可沒有使用紀錄。file: `tests/executor/test_approval.py:808`

新 `_too_late` 卻在鎖內重算曝險之前，只要 `held` 中任一核可尚未過期就免除新鮮度。file: `src/rtb/executor/execution.py:455` file: `src/rtb/executor/execution.py:661`

因此可形成：

1. 過時提案的鎖外預判超額，`held` 留下總曝險核可。
2. 取得寫入鎖前，另一筆曝險被釋放。
3. `_too_late` 因該核可尚未過期而放過新鮮度。
4. `begin` 鎖內重算後發現未超額，`over_limit` 為空，因此不記核可使用，卻仍呼叫 DSP。file: `src/rtb/executor/attempt_store.py:350` file: `src/rtb/executor/execution.py:733`

反方向也會誤擋：若 `held` 中的舊核可在取鎖前到期、同時已有更新且有效的核可，`_too_late` 只看舊快照便先擋成「決策已過時」；檢查最新核可是否已取代舊核可的 `_superseded` 排在它之後，根本沒有機會放掉收據、下一輪重讀。file: `src/rtb/executor/execution.py:661` file: `src/rtb/executor/execution.py:664`

新增測試只涵蓋「一開始就不需要總曝險核可」及「交易內確實超額」兩端，沒有覆蓋既有測試已證明可達的預判／落鎖競態。file: `tests/executor/test_stale_decision.py:230`

其餘追查中，F1、F2、F3、F4 的冪等、確認與版本衝突路徑，以及 [S511] 兩條重跑保留新原因的分流，未發現本輪修正新增的合併阻擋問題。
