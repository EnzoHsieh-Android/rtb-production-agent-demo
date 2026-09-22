# 增量 2 代碼審第 2 輪收貨與重現紀錄(2026-09-22)

審材:第 1 輪修正的 diff(r2-snapshot.patch,698 行)。三席報告已正規化。

## 重現表

| id | 發現 | 重現方式 | 結果 |
|---|---|---|---|
| s1-1 | 共用整數檢查的守衛只認名字 | 讀新測試只斷言舊名稱不存在;改成斷言同一個函式物件並掃原始碼,加一份本地整數檢查的變異現在會紅 | HIT |
| sec-1 | DSP 對政策版本沒有格式上限 | 讀 src/rtb/dsp/capability.py 的 _nonempty_str 只檢查非空;新增 test_a_policy_version_outside_the_dsp_format_is_invalid,修正前紅 | HIT |
| sec-2 | 同鍵重放只留第一次的政策版本 | 讀 src/rtb/dsp/store.py 指紋不含政策版本;判定這是正確語意(紀錄的是實際套用時的政策版本),寫進 Mock-DSP 節點說明,程式不改 | HIT |
| x1-1 | 時鐘仍比金鑰檢查先讀 | 新增 test_the_key_is_checked_before_the_clock_is_read(沒金鑰、時鐘丟例外),修正前紅;把時鐘提前讀的變異現在會紅 | HIT |
| x1-2 | 同 s1-1 | 同上 | HIT |

修正後:全套 874 支綠;三道新防護的變異全部變紅。
