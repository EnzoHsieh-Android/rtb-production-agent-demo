severity: clean

### 已看:parse_proposal 每一種錯誤字串,經過 `_log_invalid_proposal` 換算後是否都不含攻擊者可控內容

逐一列出 `src/rtb/domain/proposal.py` 裡 `parse_proposal`/`_parse` 可能回的每一種 `errors` 元素:

- `not_an_object`(固定字串,`_parse` L282)
- `too_deep`、`not_json_serializable`(固定字串,`_size_error` L198/L200/L205)
- `f"too_large:{total}"`(L211,尾巴是位元組數,非攻擊者字面內容但仍是計算值)
- `f"unknown_field:{str(key)[:64]}"`(L234,尾巴是攻擊者送的鍵名,最直接可控)
- `f"{field}:missing"` / `f"{field}:invalid"`(L237/L239,`field` 只會是 `CHECKS` 的固定鍵名之一,不是攻擊者輸入)
- `decision_expires_at:not_after_creation` / `decision_expires_at:lifetime_too_long`(固定字串,`_expiry_errors` L249/L251)
- `f"unexpected_failure:{type(exc).__name__}"`(L277,尾巴是例外類別名稱,來源是 `_parse` 內建/自訂例外的型別,不是攻擊者能自訂字串內容的欄位)

引句:「_TAILED_ERRORS = frozenset({"unknown_field", "too_large", "unexpected_failure"})」(r1-snapshot.patch)。核對後,唯一在格式上「前綴:攻擊者可控尾巴」的三類就是 `unknown_field`(鍵名)、`too_large`(位元組數)、`unexpected_failure`(例外類別名);其餘所有錯誤字串要嘛完全固定、要嘛尾巴只會是 `CHECKS` 裡的固定欄位名,不含請求內容。`_log_invalid_proposal` 用 `error.split(":", 1)[0]` 取「冒號前」判斷是否屬於這三類,由於這三類的格式固定是 `f"{類別}:{尾巴}"`,即使攻擊者刻意把鍵名取成含冒號的字串(例如鍵名本身叫 `too_large:leak` 或 `decision_expires_at:not_after_creation`),送進去也只會被組成 `unknown_field:<key>`,`split(":", 1)[0]` 永遠先切到 `unknown_field`,不會被鍵名內容誤導成別的類別、也不會意外保留尾巴。實測 `tests/executor/test_inbox_reject_log.py` 的 `unknown_field_and_missing`、`bad_value_and_unknown_field` 兩案例都驗過鍵名 `ATTACKER_KEY` 與注入字串不出現在記錄行。沒發現第四種帶攻擊者內容卻未被歸類的錯誤字串。

### 已看:httpkit.py 的 read_json / read_exactly,每一種 RequestRejected 是否都被接住記一行;有沒有非 RequestRejected 的例外從讀本文冒出繞過紀錄

`read_json`(`src/rtb/httpkit.py` L202-220)與其呼叫的 `read_exactly`(L222-229)、`single_header`(L185-189)會丟的 `RequestRejected` 共 7 種固定代碼:`chunked_not_supported`(411)、`invalid_content_length`(400)、`body_too_large`(413)、`duplicate_header`(400,讀 Content-Length 時)、`request_timeout`(408)、`incomplete_body`(400)、`invalid_json`(400,兩處:JSON 語法錯與非 dict)。`InboxHandler._parse_or_reject` 的 `try: body = self.read_json() except RequestRejected as rejection: _log_invalid_proposal((rejection.code,)); raise` 把 `self.read_json()` 整段包住,上述 7 種都會先被記一行才重新丟出。`tests/executor/test_inbox_reject_log.py::test_body_rejections_before_parsing_are_logged_with_their_fixed_code` 已對 `body_too_large`、`chunked_not_supported` 兩種實測。

非 `RequestRejected` 的例外:`read_json` 內 `json.loads` 只會丟 `ValueError`/`RecursionError`,兩者都已被 `except (ValueError, RecursionError)` 轉成 `RequestRejected(400, "invalid_json")`,不會漏。`read_exactly` 的 `self.rfile.read(length)` 只特別接住 `TimeoutError`;若客戶端在宣告 Content-Length 後中途斷線(例如 `ConnectionResetError`,是 `OSError` 子類、不是 `TimeoutError`),會以原始例外形式從 `read_exactly`→`read_json`→`_parse_or_reject` 冒出,不會經過 `except RequestRejected`,因此不會寫 `invalid_proposal` 這行。但這條路徑是 `httpkit.py` 既有行為,這次 diff 沒有改動 `read_exactly`;而且冒出後仍會落到 `JsonHandler._dispatch` 的 `except Exception as exc: self._reply_unexpected(exc)`,該處本來就會把完整 traceback 印到標準錯誤(`httpkit.py` L172-174),不是靜默漏記,只是格式不是這次新增的 `invalid_proposal errors=...` 一行。這屬於既有邊界情況,非本次 diff 引入的迴歸,列為 ⚠ 而非 finding。

### 已看:「先寫紀錄、再送回應」的順序

`_parse_or_reject` 在兩個分支都是「先呼叫 `_log_invalid_proposal`(內含 `sys.stderr.write` 與明確 `sys.stderr.flush()`),再 `raise`」,例外才被 `JsonHandler._dispatch` 的 `except RequestRejected as exc: self.reply_error(...)` 接住並送出回應(`httpkit.py` L154-155)。由於 `flush()` 是同步呼叫且在同一個執行緒、在 `raise` 之前執行,寫入一定先於後續 `reply()` 對 socket 的 `send_response`/`wfile.write`。沒發現任何一條路徑是先送回應才寫紀錄。

### 已看:capfd 抓伺服器執行緒寫的標準錯誤是否可靠

`capfd` 是在檔案描述子層(`os.dup2`)攔截,寫入是否被抓到只取決於「寫入的 syscall 是否已經發生」,與是不是同一個 Python 執行緒無關。因為 `_log_invalid_proposal` 的 `flush()` 保證在同一執行緒內、回應送出之前完成,而測試是在 `inbox.post()`(等待並收到 HTTP 回應之後)才呼叫 `capfd.readouterr()`,「收到回應」隱含「寫紀錄那行已經執行完並 flush」,兩者之間有明確的 happens-before(客戶端收到回應位元組,代表伺服器端執行緒已經執行過送回應之前的所有程式碼,包含 flush)。所以不會有「時序偶發抓不到」的競態。實際執行測試(見下方重現)沒有出現任何 flake 現象。

### 已看:既有測試是否仍守住原本的東西

實際重現(複製 `src`/`tests` 到臨時目錄,不改 repo,用專案 venv 跑):

```
$ TMP=<mktemp -d 出來的臨時目錄>
$ PYTHONPATH="$TMP/src" /Users/enzo/rtb-production-agent-demo/.venv/bin/python \
    -m pytest -p no:cacheprovider tests/executor/test_inbox_reject_log.py tests/executor/test_inbox_server.py -q
.............................................F..........                 [100%]
1 failed, 55 passed in 2.57s
FAILED tests/executor/test_inbox_server.py::test_the_executor_and_dsp_packages_may_not_import_each_other
  AssertionError: ('executor', 'ruff: ... No such file or directory (os error 2)')
```

唯一失敗的 `test_the_executor_and_dsp_packages_may_not_import_each_other` 是因為臨時目錄裡沒有複製 `pyproject.toml`,導致子行程呼叫的 `ruff` 找不到設定檔,與這份 diff 無關(環境缺檔案,不是程式邏輯壞掉)。其餘 55 個測試全過,包含:
- `test_every_sample_the_domain_parser_rejects_is_also_rejected_by_the_inbox_and_the_inbox_defines_no_validators`(S16,「每個格式不合法的請求都經過同一個解析函式」,靠 monkeypatch `inbox_server.parse_proposal` 計數):`_parse_or_reject` 內仍是直接呼叫模組層級名稱 `parse_proposal(body)`,monkeypatch 換掉 `inbox_server.parse_proposal` 後照樣被攔到,通過。
- `test_an_invalid_proposal_is_a_400_even_when_the_database_is_locked_and_writes_no_event`:`_parse_or_reject` 仍在 `InboxStore(...)` 建立之前執行,格式不合法時完全不碰資料庫,鎖住時依然回 400、不進 `inbox_events`,通過。
- 這次新增的 `tests/executor/test_inbox_reject_log.py` 全數 8 個參數化案例(含 `too_large_inside`、`unknown_field_and_missing` 等)與另外兩支獨立測試全過。

沒有發現既有合約被破壞。

⚠ 交編排者:`src/rtb/httpkit.py` 的 `read_exactly`(L222-229)只特別接住 `TimeoutError`,若客戶端在宣告 `Content-Length` 後中途斷線導致 `ConnectionResetError`/`BrokenPipeError`(`OSError` 子類)冒出,會跳過這次新增的 `invalid_proposal errors=...` 紀錄,改由 `httpkit.py` 既有的「印完整 traceback 到 stderr、回 500」路徑處理。這不是本次 diff 造成的迴歸(`httpkit.py` 完全沒被這份 diff 改動),也不是「靜默漏記」(traceback 仍會印出來,只是格式不同),但如果之後 Phase 9 要依賴 `invalid_proposal errors=` 這個固定格式做可觀測性統計,這個連線中途斷線的邊界情況不會出現在那個格式裡,值得留意。

最高 severity 是 clean,沒有 blocking 的 finding。
