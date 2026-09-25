severity: minor

## 發現 1:設定來源實測的監聽埠，只要有任何連線就判「毒值讀到」,本機別的行程可以偽造這一項通過
severity: minor
blocking: 否

引句:「            hit.set()」
引句:「self.notes["settings_control"] = "connected"」

**問題在哪裡**
- `_poison_listener` 的做法是：收到連線就立刻設旗標，然後關掉連線。它不讀任何一個位元組，也不核對對方是不是 claude 送來的 API 請求(file: `src/rtb/modelverify.py:94`)。
- 對照組跑完後，只要旗標有被設起來，這項就直接判過(file: `src/rtb/modelverify.py:353`)。
- 監聽埠綁在 127.0.0.1,這部分沒問題。但本機的任何行程(包括其他使用者的)都能連 127.0.0.1。macOS 的 `netstat -an` 對所有使用者列出 LISTEN 的埠，要找到這個埠不難。
- 時間窗是對照組執行的那段(最長 `CONTROL_TIMEOUT_SECONDS`,30 秒)。這段時間裡連一次，這一項就被判成「毒值真的被讀到」。

**能不能被利用**
- 後果：即時模式的啟用紀錄會在「壓制參數有沒有效，其實沒被證明」的狀態下寫出。例如某版 claude 根本不讀使用者設定的 env,這項本來該判「驗不出」而不過，結果判成過。
- 反方向的偽造只會讓結果變差，方向安全:
  - 主呼叫期間有人連進來，會記成 `poison_read`、判沒過。
  - 攻擊者只能讓實測失敗，擋不到其他東西。
- 前提是攻擊者已經在本機跑行程，而且要剛好在協調者手動跑實測的那 30 秒內出手。所以列 minor。

**權杖洩漏面(看過，沒問題)**
- 對照組讀到毒值時,claude 會帶權杖用明文 http 打這個埠。監聽端不讀就關掉連線，資料只在核心緩衝區，隨連線重置丟掉，沒有任何記錄。
- 埠是先綁好才寫進設定，綁的是指定位址、沒開 SO_REUSEPORT,別的行程搶不走。
- 逾時時 `_finish` 會殺掉整個行程群組，不會有殘留的 claude 在監聽端關掉後繼續重試，讓別人撿到那個埠。

**例子**
- 輸入:
  - 假 claude 設成 `poison_readable=False, slow_control=True`:從不讀使用者設定，對照組只是回得慢。
  - 對照期間，另一個執行緒(模擬本機另一個行程)對監聽埠 `create_connection` 一次。
- 預期:`setting_sources_suppress_user_settings` 沒過(判不出來)。
- 實際：這項「過」,而且整份報告「全部通過」,寫出 live-verification.json。

**重現**
1. 在 /tmp/sec-opus-p13i4r3 的 pytest 裡(conftest 隔離家目錄),把 `CONTROL_TIMEOUT_SECONDS` monkeypatch 成 3.0。
2. 把 `modelverify._poison_listener` 包一層：拿到埠後起一條執行緒，睡 1.5 秒再連一次。
3. 用 `tests/model/test_verification.py` 的 `_claude(tmp_path, poison_readable=False, slow_control=True)` 跑 `_run`。
4. 結果 1 passed,斷言「這項過」成立。

**修法**
- 收到連線後讀第一行(有上限，例如 64 位元組、0.5 秒逾時),要是 `POST /v1/messages` 這類 API 請求行才設旗標。
- 只讀請求行，不讀標頭(權杖在 Authorization 標頭裡),讀到的內容一律不記錄。
- 同時補一支「陌生連線不算讀到」的測試。

k1 已驗(r2 s1):`_continued` 現在限在 1 < 輪數 ≤ 1+3,而且要看得到撞頂(停止原因 max_tokens,或總輸出 ≥ (輪數-1)×上限)。`num_turns=9` 照舊標工具使用;工具那一輪短、輸出沒到上限的,也標工具使用。
- 變異測試：拿掉輪數上限，或拿掉撞頂證據，都被 tests/model/test_claude_backend.py 抓紅。
- 殘餘風險：前提是工具真的被打開。這時注入可以讓模型在用完工具後寫滿上限，就會被歸成 `output_continued`。錯誤路徑上「超過輸出上限」本來就不看輪數(使用者裁定)。
- 這個殘餘靠兩層擋住：一是 `--tools ""`;二是 modelverify 新加的正面證據，要求初始事件的工具清單在、而且是空的。廣告名稱注入沒有工具可用，所以不列發現。

k2 已驗(r2 s2):入庫根不存在時，改成丟 OSError,再轉成 `MixedRecordingsDir` 拒開錄。
- 實測：入庫根搬走後，入庫子目錄、別處的新目錄、大小寫不同的寫法三種全部拒絕。入庫根在的時候,`RECORDINGS/Model/new`、`…/model/../model/z` 也都拒絕。
- 把舊的 `return False` 塞回去的變異，被 tests/model 抓紅。
- 看過的繞法:
  - 懸空的符號連結當入庫根：會拒絕。
  - 入庫根換成指向別處的連結:stat 跟隨連結，比對的是同一個 inode,照樣擋。
  - 裝成套件後找不到 pyproject:退到 `here.parents[2]`,多半不存在，結果是一律拒絕，方向安全。
  - 會開錄的入口(模型閘道兩處、`recordings.py --record`、假說命令列)都經過這支。
  - `rtb.eval.record` 的候選評估直接寫預設目錄，不經這支。那是 Phase 11B 既有設計，不在增量 4 的改動內，不列。

k3 已驗(r2 s3):另存報告的 meta CSP 是 `default-src 'none'`,腳本、樣式各綁自己的 sha256,沒有 `unsafe-inline`,沒有 nonce,也沒有 `unsafe-hashes`。
- 我實際渲染報告並掃過:
  - 0 個 `style=""` 屬性、0 個 on* 事件屬性，沒有 `javascript:`。
  - 沒有 img、link、iframe,CSS 裡沒有 `url(`。
  - `<script>` 與 `<style>` 各只有 1 個。
  - 所以 `img-src 'none'` 和雜湊式的 `style-src` 不會弄壞頁面，也不會逼人加 unsafe。
- 雜湊一致性:
  - 雜湊是在 `_public_page_html` 替換之後、從實際輸出算的，測試會比對，把 meta 拿掉的變異被抓紅。
  - 另存檔用 utf-8 寫，換行已正規化成 LF,和瀏覽器算雜湊時看到的文字一致。
  - 腳本用 CSSOM 改 `box.style`,不受 `style-src` 管，不會被擋。
- 浮出框的鍵盤版 JS:
  - 新增的只有焦點移動(`focus`)、`restoringFocus` 旗標、捲 `.scenario-index` 的 `scrollLeft`。
  - 沒有 innerHTML、insertAdjacentHTML,也不讀 location 或網址參數。`getElementById` 的參數來自白名單情境代碼，加上固定的節點 id。
  - 新加的 `_occurrence_label` 對 `task_id` 做了 `escape_text`。
  - 沒有 DOM XSS。

看過的改動檔:claims/aggregate-blast-radius.json、claims/concurrency.json、claims/idempotency-unknown-outcome.json、claims/permission-guardrail.json、claims/prompt-injection.json、docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11B大模型接入_計劃.md、docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase13AI參與決策_計劃.md、docs/rtb-production-agent-demo-knowledge/Systems/一鍵展示.md、docs/rtb-production-agent-demo-knowledge/Systems/展示頁面.md、docs/rtb-production-agent-demo-knowledge/Systems/模型用戶端.md、docs/rtb-production-agent-demo-knowledge/Verification/Phase13增量4驗收紀錄.md、src/rtb/demo/flow_svg.py、src/rtb/demo/page.py、src/rtb/demo/recordings.py、src/rtb/eval/investigation_eval.py、src/rtb/modelclaude.py、src/rtb/modelrecording.py、src/rtb/modelverify.py、tests/demo/test_ai_demo.py、tests/demo/test_ai_page.py、tests/demo/test_flow_popover_browser.py、tests/demo/test_page.py、tests/demo/test_record_demo_batch.py、tests/eval/test_investigation_eval.py、tests/eval/test_model_candidate.py、tests/model/test_claude_backend.py、tests/model/test_recording_integrity.py、tests/model/test_shared_entry.py、tests/model/test_verification.py、tests/ops/test_hypothesis.py。

本席沒有呼叫模型，沒有在 pytest 以外起 rtb 行程，也沒有碰 ~/.rtb。
- 實驗都在 /tmp/sec-opus-p13i4r3 做：跑純函式，以及 pytest 裡的假 claude,做完已刪。
- 目前看得到的殘留行程屬於 pytest-18268、pytest-18286,是別席的，我沒動。

1 條,blocking 0。
