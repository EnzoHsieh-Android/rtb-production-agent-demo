severity: major

**Finding 1:驅動程式帶著稽核金鑰讀平台時,請求會照 HTTP_PROXY 走代理:金鑰送到代理,代理回的內容也被當成「平台真實狀態」**
severity: major
blocking: 是
引句:「writes = read_dsp_window(str(self.dsp.url), *_EVER, DSP_READ_SECONDS,」
file: `src/rtb/demo/driver.py:264-275`、`src/rtb/httpclient.py:45`、`src/rtb/ops/side_effects.py:215-223`

- **問題**:子行程的環境走白名單(S1003),不帶代理變數,這一點沒問題。驅動程式本身不一樣:
  - 它跑在使用者的完整環境裡,本輪開始在行程內用共用用戶端讀 DSP,每次都帶 `X-Dsp-Audit-Key`。
  - `build_opener(_NoRedirect)` 保留了 urllib 預設的 ProxyHandler。
  - 使用者只要設了 `HTTP_PROXY`、沒設 `NO_PROXY`(開發機很常見,例如本機的翻牆代理、除錯用的攔截代理),請求就不會直接連 `127.0.0.1:<port>`,會改送到代理。
- **後果**:
  - 稽核金鑰外洩到代理,攔截工具會記錄標頭。
  - 代理回的內容會被逐廣告核對與總額核對當成平台真相,可能造成假綠,也可能造成假失敗。
- **跟既有程式的關係**:正式的維運指令(ops)也用同一支 `get_json`,同樣有這個問題。所以根本修法在共用用戶端。

重現(在 /tmp 複本):
- 起一個假代理,設 `HTTP_PROXY=http://127.0.0.1:<代理埠>`,然後呼叫 `get_json("http://127.0.0.1:9", "/operations/after/0", 3, b"secret-audit-key-…")`。
- 回傳是代理給的 `(200, {})`。
- 代理收到 `GET http://127.0.0.1:9/operations/after/0 … X-Dsp-Audit-Key: c2VjcmV0LWF1ZGl0…`,金鑰整段送到了代理。

建議:
- 在 `httpclient` 用 `build_opener(ProxyHandler({}), _NoRedirect)`:回送位址的平台一律直連,不讀代理設定。
- 補一條測試:設了 HTTP_PROXY,代理收不到任何請求。

**Finding 2:驗證器逾時只殺直接子行程,驗證器另開行程群組的 pytest 會變成孤兒;取消展示也停不了驗證器**
severity: major
blocking: 是
引句:「done = subprocess.run(list(command), cwd=PROJECT_ROOT, env=env, capture_output=True」
file: `src/rtb/demo/driver.py:921-936`、`src/rtb/demo/driver.py:979-987`、`tools/verify_claims.py:1198-1205`、`tools/verify_claims.py:52`

- **問題**:三個條件疊在一起:
  - `run_verifier` 的時限是 600 秒,逾時會由 `subprocess.run` 只對 `verify_claims.py` 送 SIGKILL。
  - `verify_claims.py` 起 pytest 時用了 `start_new_session=True`,pytest 在自己的行程群組裡。
  - 驗證器自己的總時限是 900 秒,比驅動程式的 600 秒長,所以它自己的 `killpg` 來不及執行就被殺了。
- **後果**:pytest(以及它起的證據測試行程)留成孤兒,父行程變成 1。這符合判準的「讓子行程失控、留孤兒」。
- **另一個相關的點**:`run_all` 跑驗證器時不看 `stop`。展示取消後,仍要等驗證器最多 600 秒。

重現(在 /tmp 複本):
- 執行 `run_verifier(default_verifier_command(), "d-x", 8)`,回傳 `False ('驗證器逾時(8 秒)',)`。
- 2 秒後 `ps` 看到 `Python -E -s -c import json, sys / import pytest …`:PPID=1、PGID 是它自己,cwd 是我的複本,5 秒後還在。
- 已用 `pkill -9 -g` 收掉。

建議:
- 驅動程式起驗證器時用 `start_new_session=True` 加 Popen,逾時用 `killpg` 殺整組。
- 或讓驅動程式的時限大於驗證器自己的時限,並給驗證器傳 SIGTERM,讓它有機會自己 killpg。
- 驗證器執行中也要輪詢 `self.stop`,收到就殺整組。

**Finding 3:httpkit 轉址的 Location 不擋換行也不擋非 latin-1:可以注入標頭,非 ASCII 會讓回應亂掉**
severity: minor
blocking: 否
引句:「return cls(303, "text/plain; charset=utf-8", b"", (("Location", location),))」
file: `src/rtb/httpkit.py:72`、`src/rtb/httpkit.py:313-314`

- **問題**:Python 3.14 的 `send_header` 不檢查 CR/LF。
  - 位址帶 `\r\n` 就能注入任意標頭。
  - 位址帶中文會在 `send_header` 丟 UnicodeEncodeError。這時 `send` 只接 OSError,例外會落到 `_reply_unexpected`,再送一次狀態列。結果是同一條連線上先有半截的 303 標頭,接著又是一個 500。
- **為什麼只列 minor**:現在還沒有正式呼叫端,2b 的轉址目標應該是寫死的,所以是潛伏問題。

重現(在 /tmp 複本):
- 處理器把查詢參數原樣當成轉址目標。
- 請求 `?next=/ok%0d%0aSet-Cookie:%20pwn=1`,回應裡出現獨立的一行 `Set-Cookie: pwn=1`。
- 請求 `?next=/%E7%A2%BA%E8%AA%8D`,回應是 `303 … Content-Length: 0` 後面直接接 `HTTP/1.0 500 …`。

建議:
- `Response.redirect` 只收以 `/` 開頭、不含 `//` 開頭、不含控制字元、只有 ASCII 的相對位址,不合就丟 ValueError。
- `send` 裡所有標頭值先檢查有沒有 CR/LF。
- 另外建議:`read_form` 預設就檢查 `Origin`/`Sec-Fetch-Site` 是同源。Host 檢查擋得住 DNS rebinding,擋不住跨站的表單 POST。F7 的確認表單會簽出一張超額核可,不該只靠 2b 的處理器記得自己檢查。

**第 2 輪 s1–s4 繞法複查**
- **s1(超時後情境本體還在起行程、狀態被改回執行中)**:已修對。
  - 用原來的重現方式在複本重跑:情境時限 1 秒,本體先等 1.5 秒,再 `start_platform`、`start_inbox`。
  - 結果是 `incomplete / 超過情境總時限 1 秒`,3 秒後 `pgrep` 沒有殘留行程,狀態庫停在 incomplete。
  - 修法本身:`World.start` 把「檢查」與「登記」放在同一把鎖裡,`close` 也拿這把鎖;`_wait_for_confirmation` 開頭會檢查 `stop`,finally 也只在情境還沒被收掉時才寫回執行中。
- **s2(確認快照的關卡文字)**:已修對。關卡文字改由 `STAGE_TEXT[stage]` 產生,不在表裡的關卡直接失敗;查不到廣告改成 ScenarioFailed,不再補 0。
- **s3(上層目錄核對會跟著符號連結走)**:已修對。改用 `lstat`,是連結就拒。上層的上層沒有檢查,屬於跨使用者刻意攻擊,不另列。
- **s4(總上限明確給 0 會變成寬值)**:已修對,寫法是 `LOOSE_AGGREGATE_LIMIT if aggregate_limit is None else aggregate_limit`。

**其他查過、沒發現問題的部分**
- **稽核金鑰**:
  - 只用在 `_dsp_get(audit=True)` 與 `platform_writes`,不進狀態庫、確認請求或例外訊息。`DspUnreadable` 與 `_refused` 只帶狀態碼與本文。
  - `DemoKeys` 的 repr 有遮蔽。
  - 不會被導到別處:`url` 來自子行程第一行的 `PORT=`,子行程是固定的正式模組;共用用戶端也不跟隨 3xx。唯一的外流路徑是 Finding 1 的代理。
- **驗證器輸出**:
  - 驗證器的環境只帶 PATH/HOME/LANG,沒有金鑰;輸出以參數化 SQL 存成 JSON 文字。
  - 2b 顯示時要記得 `html.escape`,因為 `Response.html` 不會自動跳脫。現在還沒有頁面,不列。
- **httpkit 錯誤頁與表單**:
  - HTML 錯誤頁只顯示跳脫過的錯誤代碼,堆疊只寫到 stderr,不會洩漏內部細節。
  - HTML 回應一律帶 CSP;CSS 與 303 不帶,這沒問題。
  - 表單沿用 64KB 上限、每個請求 30 秒總期限、重複欄位拒收,沒看到資源耗盡的路徑。
- **F7 核可證據(c2 修法)**:
  - 用這次展示的核可金鑰驗章,並比對任務、修訂、內容雜湊、關卡四項。
  - 還查了該操作鍵用過的核可關卡。
  - 別的展示的核可或別的金鑰都驗不過章,執行端也用同一把金鑰驗。在「防忘記」的威脅模型下沒看到繞法。

**清理**:實驗目錄 /tmp/p12i1-r3-sec 已刪除。我起的孤兒 pytest 已用 `pkill -9 -g` 收掉,之後 `pgrep -fl p12i1-r3-sec` 沒有輸出(exit=1)。
- ps 裡還有 `/tmp/p12r3-rd/repo` 與 `/tmp/p12i1r3-drv/repo` 的 pytest,不是我起的,是其他席位的。

**看過的檔**(涵蓋 r3-snapshot.patch 的全部改動檔):
- **細讀**:
  - `src/rtb/httpkit.py`
  - `src/rtb/demo/driver.py`
  - `src/rtb/demo/keys.py`
  - `src/rtb/demo/state_store.py`
  - `src/rtb/demo/launcher/__init__.py`
  - `src/rtb/demo/faults/delivery.py`
  - `src/rtb/ops/side_effects.py`(get_json / read_dsp_window)
  - `src/rtb/httpclient.py`
  - `tools/verify_claims.py`(執行與逾時段)
  - `tests/kit/test_httpkit_responses.py`
  - `tests/demo/test_driver.py`(逾時與確認段)
- **依 patch 範圍掃過**:
  - 展示套件:`src/rtb/demo/__init__.py`、`flow.py`、`observe.py`、`state.py`
  - 故障與啟動器:`src/rtb/demo/faults/__init__.py`、`dsp.py`、`executor.py`,`src/rtb/demo/launcher/child.py`
  - ruff 設定:demo、launcher、faults、analyzer、domain、dsp、eval、executor、ops 的 `ruff.toml`
  - 分析端:`src/rtb/analyzer/flow.py`、`runner.py`、`task_store.py`
  - 執行端:`src/rtb/executor/approve.py`、`attempt_store.py`、`inbox_server.py`、`inbox_store.py`、`replay.py`、`runner.py`
  - 其他正式程式:`src/rtb/dsp/server.py`、`src/rtb/eval/record.py`、`src/rtb/ops/cli.py`、`metrics.py`、`slo.py`、`trace.py`
  - `pyproject.toml`
  - 測試:`tests/analyzer/test_no_action_reason.py`、`tests/analyzer/test_runner.py`、`tests/demo/__init__.py`、`test_boundaries.py`、`test_faults.py`、`test_flow.py`、`test_launcher.py`、`test_observe.py`、`test_state_store.py`、`tests/executor/test_read_only.py`、`tests/ops/test_cursor_readers.py`、`tests/ops/test_ops_boundaries.py`
  - claims 五檔、圖譜 Systems 與 Issues 筆記(只看標題與相關段)
- **參考**:`governance/review-reports/code-phase12-inc1/r2-資安-opus.md`

3 條,blocking 2。
