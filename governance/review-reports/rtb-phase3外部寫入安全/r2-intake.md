preflight-4: ran

# 第 2 輪收貨與重現紀錄(2026-09-22)

機械三道:七份報告 report-normalize 皆已正規化;quote-check 七份全數錨定;refcheck 引用的檔案行號全部存在;seat-check 只有「未提凍結快照檔名」的觀測提醒(不擋)。

## 重現表

| id | 發現 | 重現方式 | 結果 |
|---|---|---|---|
| s1-1 | 查到被拒打不到 | 讀 src/rtb/dsp/store.py 的 _execute_in_transaction 與 _recorded_result:只有成功套用才寫 operations 與 idempotency_keys,拒絕在寫入前丟例外 | HIT |
| s1-2 | 已提交待驗證缺驗證查詢逾時去處 | 讀 r2-snapshot.md 轉換表,已提交待驗證只有兩條出路 | HIT |
| s1-3 | 對帳逾時無上限耗光列數 | 讀 r2-snapshot.md:逾時上限處置留給增量 4,50 列上限無例外 | HIT |
| s2-1 | 同 s1-3 | 同上 | HIT |
| s2-2 | 廣告編號只在第 1 列 | 讀 r2-snapshot.md 儲存段;對照 src/rtb/analyzer/task_store.py 每列帶 campaign_id | HIT |
| s3-1 | 查不到就判沒發生會誤判 | 讀 src/rtb/dsp/server.py 的 _apply_fault_before_commit:delayed_response 先睡後提交 | HIT |
| s3-2 | 代碼沒涵蓋忙碌等回應 | 讀 src/rtb/dsp/server.py 的 ERROR_TABLE 與 error_entry | HIT |
| s3-3 | 租戶納入鍵會逼改增量 1 | 讀 r2-snapshot.md 無租戶交代;DSP campaigns 表 id 是主鍵 | HIT |
| s4-1 | 代碼類別未綁狀態 | 讀 r2-snapshot.md 的 S16 只驗認不認得 | HIT |
| s4-2 | 一般轉換可離開轉人工 | 讀 r2-snapshot.md 轉換表含轉人工出路;對照 src/rtb/domain/task_state.py transition 只看表 | HIT |
| s4-3 | 自轉換可被一般轉換動用 | 讀 r2-snapshot.md 轉換表含結果不明自轉換 | HIT |
| s4-4 | 同 s1-3 | 同上 | HIT |
| s4-5 | 快照讀回要能建回提案 | 讀 src/rtb/analyzer/task_store.py 讀回時包回 MappingProxyType;src/rtb/domain/proposal.py 的 __post_init__ 驗型別 | HIT |
| s4-6 | 增量 2 編號撞名 | 讀 r2-snapshot.md 冪等鍵段 | HIT |
| sec-1 | 大量鎖死 | 讀 r2-snapshot.md 開始一筆只看單一廣告,無全表上限 | HIT |
| sec-2 | 租戶碰撞 | DSP campaigns 表 id 是主鍵,碰撞前提不成立,但計劃未交代假設,當文件缺陷折入 | HIT |
| sec-3 | 鍵正規化不足 | 執行 PYTHONPATH=src .venv/bin/python -c 呼叫 rtb.domain.proposal._valid_change:new_budget=10.0 回 False、10 回 True;ID_PATTERN 限 ASCII;變更只有 new_budget 正整數或空白,無陣列 | MISS(不成立) |
| sec-4 | 細節文字可偽裝 | 讀 r2-snapshot.md 只限可列印字元;全形冒號屬可列印 | HIT |
| sec-5 | 快照與索引欄位一致性 | 讀 r2-snapshot.md 未寫明 | HIT |
| sarch-1 | 另開連線做不到同交易 | 讀 src/rtb/sqlitekit.py 每連線各自交易;三個行程的資料庫模組都是一支一個 connect | HIT |
| sarch-2 | 同 s2-2 | 同上 | HIT |
| x1-1 | 冪等衝突兩處歸類矛盾 | 讀 r2-snapshot.md 結果代碼段:失敗類列冪等衝突,後文寫冪等衝突→轉人工 | HIT |
| x1-2 | 同 s1-1 | 同上 | HIT |
| x1-3 | 同 s1-3 | 同上 | HIT |
| x1-4 | 取件與開始一筆的競態 | 讀 src/rtb/executor/inbox_store.py 的 _accept_in_transaction:新修訂把舊待處理標 superseded | HIT |

## 相依回歸閘的已知誤報

spec-gate 的相依回歸對 4 支參數化或名稱互為子字串的既有測試報「篩選匹配到多支」;這是已回報給 lumos 會談的已知誤報(改用 collect-only 節點編號),照指示不為了閘改測試。
