# 增量 2 代碼審第 1 輪收貨與重現紀錄(2026-09-22)

審材:df900ba..a3c307b 的 diff 共 2038 行,超過單席建議上限,拆成程式(r1-snapshot-src.patch,886 行)與測試(r1-snapshot-tests.patch,1152 行)兩份分席審;r1-snapshot.patch 是兩份合在一起的原檔。七份報告已正規化;外家席第 3 條引句出自測試那份,對程式那份錨不到,對合併原檔可錨,內容成立。

## 重現表

| id | 發現 | 重現方式 | 結果 |
|---|---|---|---|
| s1-1 | 政策版本驗完就丟,只記不驗沒落實 | 讀 src/rtb/dsp/store.py 的 operations 表沒有政策版本欄位;新增 test_the_policy_version_of_an_applied_write_is_recorded,修正前紅 | HIT |
| s2-1 | 設定檔是具名管道時簽發器卡住 | 新增 test_a_config_that_is_a_named_pipe_is_refused_without_hanging,修正前卡住判紅;拿掉不等待旗標的變異會紅 | HIT |
| s3-1 | 讀標頭搶在金鑰檢查之前 | 手動重現:沒金鑰加兩個憑證標頭回 400 duplicate_header;新增 test_without_a_key_duplicate_capability_headers_still_answer_not_configured,舊程式碼(git stash)下紅、修正後綠 | HIT |
| s4-1 | 聲明型別測試走不到 DSP 自己的檢查 | 改寫 _claim_cases 用共用層放行的錯誤值;拿掉 DSP 聲明型別檢查的變異現在會紅 | HIT |
| sarch-1 | DSP 內整數檢查重複 | 讀 src/rtb/dsp/store.py 既有 _is_plain_int;改成共用一份並加 test_the_dsp_capability_module_keeps_no_second_integer_check | HIT |
| x1-1 | 同 s1-1 | 同上 | HIT |
| x1-2 | 設定檔超長整數丟出沒分類的例外 | 新增 test_a_config_with_a_huge_integer_is_refused_not_crashed,修正前紅 | HIT |
| x1-3 | 金鑰不可用的合約測試沒跑到 DSP 啟動程式 | 綁定測試補上子行程啟動 DSP(沒金鑰、短金鑰)寫入回 503;把啟動程式改成寫死金鑰的變異現在會紅 | HIT |

資安席 clean。修正後變異檢查:六道新防護逐一拆掉全部變紅;全套 868 支綠。
