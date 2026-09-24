severity: minor

## 發現 1:收尾時,已經通過「收尾中」檢查、還沒設好 current 的觸發,仍會開出一次不會被取消的展示
severity: minor
blocking: 否
引句:「if self.closing:  # 收尾開始之後才處理到的 POST /run(代碼審 r2 v2)」
引句:「run, thread = self.current, self.thread」

file: `src/rtb/demo/server.py:147-160`、`src/rtb/demo/server.py:198-207`、`src/rtb/demo/server.py:519-520`

第 2 輪 (c) 只修掉一半。`start()` 拿第一次鎖時查 `closing`,查過就設 `running=True`,然後放開鎖。接下來在鎖外產生金鑰、開 StateWriter、`record_demo`、建驅動程式,最後才拿第二次鎖設 `current`。

問題出在這一段鎖外的時間:
- `stop()` 如果剛好在這時拿到鎖,讀到的 `current` 和 `thread` 都是舊的,或者是 None。
- 所以它不會 cancel,也等不到新的這一次。
- 新的驅動執行緒照常起跑。主行程結束時,這條 daemon 執行緒跟著被砍,它起的子行程(`start_new_session`)就變成孤兒。
- 第二次拿鎖設 `current` 時沒有再查一次 `closing`。

例子:
- 輸入:按下「全部跑一次」,約 2 毫秒內按 Ctrl-C。
- 預期:這次展示不啟動,或者被取消並等到收完。
- 實際:`stop()` 立刻返回,新的一次沒被取消,驅動執行緒還活著。

重現(`/tmp/secopus-p12r3` 複本,假驅動程式跑到被取消為止):
- 做法:monkeypatch `StateWriter.record_demo`,在裡面另開執行緒呼叫 `svc.stop(timeout=1)`,再睡 0.3 秒。
- 輸出:`closing= True stop returned after 0.0 s` / `new run cancelled? False drive thread alive? True running= True`。
- 用真的 `_real_driver` 量這段時間,約 1.6–2.1 毫秒。

列為 minor 的理由:要觸發,操作者得在按鈕送出後 2 毫秒內按 Ctrl-C。外人拿不到表單隨機值,送不出觸發,所以不能拿來攻擊。

建議:第二次拿鎖設 `current` 時再查一次 `closing`。已在收尾就 `driver.cancel()` 並放掉 `running`。或者讓 `stop()` 看到 `running and current is None` 時等一下再讀。

## 發現 2:兩段式 HTTP/0.9 請求行(`GET /`)讓 `single_header` 撞 AttributeError,回 500 並把 traceback 印到 stderr
severity: minor
blocking: 否
引句:「default_request_version = "HTTP/1.0"」
引句:「請求行壞掉時基底類別把版本當成 HTTP/0.9」

file: `src/rtb/httpkit.py:258-262`、`src/rtb/httpkit.py:215-218`、`tests/demo/test_server.py:910`

請求行只有兩個字時,CPython 3.14 的 `parse_request` 設 `is_http_0_9` 並把 `self.headers = {}`,這跟 `default_request_version` 無關。接著 `check_host` 呼叫 `single_header`,用到 `self.headers.get_all`,dict 沒有這個方法,丟 AttributeError。這個例外掉進 `_reply_unexpected`,回 500,並印出完整 traceback。

這個問題在改版本之前就有。改成 HTTP/1.0 之後,回應開始帶狀態行和四個安全標頭,才看得出是 500 而不是 400。

例子:
- 輸入:原始 socket 送 `GET /\r\n\r\n`,或者 `GET /\r\nHost: 127.0.0.1:<port>\r\n\r\n`。
- 預期:400 invalid_host 或 400 壞請求。
- 實際:`HTTP/1.0 500 Internal Server Error`,stderr 印出 `AttributeError: 'dict' object has no attribute 'get_all'`。

影響:
- 主機檢查沒被繞過,處理器根本沒跑到。
- 瀏覽器不會送這種請求,所以跟 DNS rebinding 無關。
- 剩下的只有本機任一行程能刷 traceback 洗版終端。
- 測試只參數化了 `GARBAGE` 和 `HTTP/9.9`,沒涵蓋兩段式請求行。

重現:
- 用真的 `python -m rtb.demo.server --work-dir <暫存> --reports <暫存>`,以 raw socket 送上面兩種請求。
- 結果兩者都是 500,帶齊標頭,stderr 有 traceback。

建議:在 `single_header` 對非 `Message` 的 headers 當成沒有標頭處理;或在 `parse_request` 之後遇到 HTTP/0.9 直接 `send_error(400)`。另外補一條 `b"GET /\r\n\r\n"` 的參數化測試。

---

**第 2 輪驗收**

| r2 條目 | 結果 | 依據 |
|---|---|---|
| 1(a) 收尾中第二次 Ctrl-C/SIGTERM 留孤兒 | 已修好 | 真伺服器跑 F7,等到 6 支子行程,再依序送 SIGINT、0.3 秒後 SIGINT、0.3 秒後 SIGTERM。0.61 秒結束,stderr 印「正在收尾…」,3 秒後殘留 `[]` |
| 1(a) SIGHUP 沒接 | 已修好 | `main` 對 SIGTERM、SIGHUP 掛 `_terminate`,收尾前三種訊號都換成 `_cleaning_up`;另有測試 `test_hanging_up_or_signalling_twice_still_leaves_no_child_processes` |
| 1(b) 取消落在情境開跑前 | 已修好 | `run_one` 先設 `_current` 再查 `self.stop`,`cancel` 先設 stop 再讀 `_current`,一來一回,兩邊至少有一邊看得到對方 |
| 1(c) 處理中的 POST /run | 部分修好 | 收尾後才進 `start()` 的一律 503;已經過了檢查、還沒設 current 的那 2 毫秒仍漏,見發現 1 |
| 2 HTTP/0.9 錯誤回應沒標頭 | 已修好 | `GARBAGE`、`HTTP/9.9`、`HTTP/2.0` 都回帶齊 CSP、no-store、nosniff、Referrer-Policy 的 400 或 505;兩段式請求行的旁支見發現 2 |
| 3 報告目錄權限跟著符號連結、改使用者目錄 | 已修好 | 四種情形實測,見下方 |
| 3 另存失敗頁面看不到 | 已修好 | 唯讀目錄時 `report_note` 記下 PermissionError 全文;頁面用 `escape_text` 輸出 |

報告目錄權限實測的四種情形:
- (a) 預設目錄 0755:收緊成 0700。
- (b) 預設路徑是符號連結、指到 0755 目錄:目標不動,只警告。
- (c) `--reports` 指定的 0755 目錄:不動,只警告。
- (d) 唯讀目錄:沒存成,`report_note` 有記錄。
- 四種情形的報告檔都是 0600。

**這輪指定要查的項目**
- **Referrer-Policy 改 same-origin 之後:**
  - **同源檢查沒有變鬆。** `_require_same_site` 只看 Origin 和 Sec-Fetch-Site,完全不讀 Referer。實測以下請求一律回 403 cross_site_form:
    - `Origin: null`
    - `Origin: null` 加上 `Sec-Fetch-Site: same-origin`
    - 只帶同源 Referer
    - `Sec-Fetch-Site` 是 same-site 或 none
    - 別的埠的 Origin
  - **表單隨機值不會經由來源網址外洩。** 隨機值只在 POST 本文的隱藏欄位。頁面所有 href、action、src 都不含它;查詢字串只有 `scenario=`。另存的報告不帶隨機值;CSP 是 `default-src 'none'`,頁面也沒有外連。
- **HTTP/1.0 預設版本:** 除了發現 2 之外沒有新問題。
  - 主機檢查照擋:1.0 不帶 Host 回 400。
  - POST 用 0.9 形式回 400。
  - 版本 2.0 以上回 505。
- **收尾中拒絕新觸發的 503:** 不能濫用。
  - 它排在隨機值和同源檢查之後,外人送不到這一步。
  - 被拒時不改任何狀態。
  - `closing` 只在伺服器結束時設,只會讓本來就要結束的伺服器提早拒。
- **簽發三步的競態:**
  - 8 條執行緒同時確認,結果 `sign` 只被呼叫 1 次,其餘 7 條都回 `confirmation_in_progress`。
  - 簽發中驅動程式關窗,讀到的是「已確認」。
  - 簽失敗時退回簽發中標記,窗維持關閉,之後的狀態是 `confirmation_timed_out`。
- **不列為發現的觀察:**
  - 表單不符的確認如果剛好碰上關窗,驅動程式會把「簽發中」當成已確認,多等 60 秒(`APPROVED_WRITE_SECONDS`)才判成沒寫入。這只是延遲,結論不會錯,而且需要隨機值。
  - 收尾中 `approve()` 沒被擋。但那一次展示已經取消,行程也會被收掉,核可只寫進暫存的收件口。

**實驗與清理**
- 複本在 `/tmp/secopus-p12r3`。報告目錄一律用 `--reports` 或參數導到暫存目錄;HOME 也導到暫存目錄。
- 在複本跑 `tests/demo/test_server.py` 和 `test_state_store.py`:81 passed。
- 做完已刪掉 `/tmp/secopus-p12r3`。
- `ls -la ~/.rtb`:前後一樣,都是空目錄 `drwxr-xr-x 64 Sep 24 17:29`,沒有 demo-reports。
- ps:`pgrep -f secopus-p12r3` 沒有結果。現存的 rtb 行程只有 9/22 起的四支 `rtb.dsp.server`(/tmp/audit-h-copy*),父行程是 1,不是我起的。

**看過的檔**(涵蓋 r3-snapshot.patch 全部 37 支改動檔)

r3-delta 內的檔,本輪逐段讀過:
- src/rtb/demo/server.py、state_store.py、state.py、driver.py、page.py、present.py、flow_svg.py、static/demo.css
- tests/demo/test_server.py、test_state_store.py、test_driver.py、test_page.py、test_present.py、sample_data.py
- docs/…/Projects/RTB_Phase12一鍵展示與HTML報告_計劃.md、Systems/一鍵展示.md、Systems/展示頁面.md

r3 沒有改動、沿用第 2 輪審過的內容,本輪確認過不在 delta:
- src/rtb/httpkit.py(本輪重讀 check_host、single_header、_require_same_site、read_form)
- src/rtb/demo/basis.py、flow.py、observe.py
- src/rtb/analyzer/policy.py、task_store.py
- src/rtb/executor/inbox_store.py(本輪重讀 add_approval)
- tests/demo/test_basis.py、test_flow.py、test_observe.py
- tests/executor/test_read_only.py
- claims/aggregate-blast-radius.json、concurrency.json、idempotency-unknown-outcome.json、permission-guardrail.json、prompt-injection.json
- docs/…/Issues/執行端寫入前再確認沒記讀到的平台版本.md、Systems/共用行程基礎.md、分析行程流程與檢查點.md、提案收件口.md

審材以外參照的檔:
- src/rtb/modelledger_view.py(account_home 不看 HOME)
- src/rtb/sqlitekit.py(忙碌逾時 5 秒)
- CPython 3.14 的 http/server.py parse_request

2 條,blocking 0。
