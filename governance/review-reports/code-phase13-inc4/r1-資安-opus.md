severity: major

## 發現 1:列在即時清單、即時開關有開，但閘道退回錄製時，分析端與說明、假說命令列的花費帳會寫進帳號家目錄那一本真帳
severity: major
blocking: 是

引句:「即時開關有開才不帶帳檔:即時模式的帳寫死在帳號家目錄」
引句:「if not (self.ai.live and self.user_env.get(LIVE_ENV) == "1"):」

驅動在啟動**之前**就決定帶不帶 `--ledger`，依據是「列在清單」加上「RTB_MODEL_LIVE=1」。但真正的模式要等分析端的閘道判。閘道的判準比這個嚴:要 PATH 找得到 claude、價目表沒過期、沒有管理政策、實測啟用紀錄有效，缺一樣就退回錄製。
- 退回錄製時沒有帳檔參數，閘道就用 `live_ledger_path()`,見 file: `src/rtb/analyzer/modelgate.py:125`。
- 這一本的位置來自帳號資料庫、不看 HOME,見 file: `src/rtb/modelledger_view.py:30`。
- 退回錄製的兩處判斷在 file: `src/rtb/modelclient.py:107` 與 file: `src/rtb/modelclient.py:115`。
- 說明、假說兩支命令列的 `_entry_args` 是同一個判法(引句:「if not (ai.live and world.user_env.get(LIVE_ENV) == "1"):」),同樣會中。

結果:這種情況下錄製模式每次呼叫(多半記成「沒有錄製」)都寫進 `~/.rtb/model-ledger.sqlite`。這違反計劃「錄製模式把花費帳記在這個情境的暫存目錄」。就算操作者把 HOME 指到暫存目錄也擋不住。協調者 10:46 看到的那筆真帳紀錄(demo-1、analyzer_investigation、recorded)就是這個形狀。

- 例子:`RTB_MODEL_LIVE=1 python -m rtb.demo.server --work-dir /tmp/w --live F1`,機器上沒跑過即時實測(或 claude 不在 PATH)。
  - 預期:分析端退回錄製，帳記在 `/tmp/w/demos/<編號>/F1/model-ledger.db`。
  - 實際:分析端參數沒有 `--ledger`,閘道判成錄製，帳路徑是真帳。
- 重現(只算路徑、不呼叫模型):用 `SimpleNamespace` 假一個 World,帶 `AiSetup("demo-1", True, …, "demo-live-demo-1")` 和 `user_env={"RTB_MODEL_LIVE":"1","PATH":"/usr/bin:/bin"}`,呼叫 `driver.World.analyzer_args(w)`。
  - 結果是 `has --ledger: False`。
  - 再用同一組環境呼叫 `modelgate.open_gate(..., ledger=None, ...)`,得到 `mode: recorded notices: () ledger: /Users/enzo/.rtb/model-ledger.sqlite`。
  - 另外 notices 是空的，頁面會寫「分析端沒有說原因」。
- 現有測試沒蓋到「列在清單、開關有開、閘道拒絕」這個組合。而且啟動器起的子行程不吃 conftest 的 child_prelude,這條路徑在 pytest 裡同樣會寫到真帳。
- 修法方向:runner 加一個「只在錄製時用」的帳檔參數，即時時忽略、不拒絕;或由閘道判完之後再決定帳檔。

## 發現 2:入庫前的展示批次檢查放行假後端、手寫答案的批次
severity: minor
blocking: 否

引句:「assert result.passed and [v[1] for v in result.verdicts] == [DONE] * 6, result.verdicts」

檢查只做三件事:看結果類別與無法分類、看批次一致和佔位檔、數「沒有錄製」的筆數。它不看錄製檔的 `backend` 欄，也不驗批次編號是否為計劃規定的 `phase13-demo-YYYYMMDD` 格式。
- 假錄製產生器寫的是 `backend="fake"`、理由「照收據判斷」,見 file: `tests/demo/fake_recordings.py:145`。
- 例子:`python -m tests.demo.fake_recordings rec phase13-demo-20260925 spec.json`,再跑 `python -m rtb.demo.recordings --dir rec --batch-id phase13-demo-20260925 --work-dir w`。
  - 預期:假後端的批次不准入庫。
  - 實際:通過，結束代碼 0。
- 重現:上面這支測試本身就是用假批次斷言 `passed`。
- 入庫之後，頁面會把人寫的文字標成「AI 產生、僅供參考・錄製回應」。有人工把關，所以列 minor。

## 發現 3:批次檢查的「通過」不看各情境結果，沒建出花費帳的情境算 0 筆缺錄製
severity: minor
blocking: 否

引句:「return self.missing == 0 and not self.problems」

`missing_recordings` 遇到不存在的帳檔直接 `continue`。`passed` 也不看 verdicts。
- 例子:F2 的分析端起不來(StartFailed)或情境在第一次問 AI 之前就失敗。
  - 預期:第 4 條沒有被證明，不通過。
  - 實際:那個情境的帳不存在、算 0 筆，只要其他情境都有錄製，整批照樣 `passed: true`,判定只以 JSON 附在 verdicts 裡。
- 重現:讀 `check_demo_batch` / `BatchCheck.passed` 的程式碼可見;實際跑要起整組展示行程，這一席沒跑。

## 看過、沒有發現的面向(不列為發現)
- **XSS、屬性與 CSS 注入**:新加的渲染全部經 `escape_text`,即 `html.escape(quote=True)` 再加上控制字元可見化。沒有使用者資料進 style 或 class。模型理由上限 200 字，假說最多 3 條、每條 500 字，頁面用 `overflow-wrap:anywhere`。
- **--live 能否從外部打開**:只能從命令列給，沒有新的 HTTP 路由。/run 系列照舊要表單權杖。F7 和不認得的代碼，在命令列解析和驅動建構時各擋一次。
- **模型環境變數與金鑰**:
  - 模型變數只給「列在清單而且帶 --ai-judge」的分析端;不在清單的情境，說明與假說命令列的變數由 `_entry_env` 拿掉;批次檢查整份拿掉。
  - 分析端和模型入口本來就拿不到任何角色金鑰(`_ROLE_KEYS` 只有 DSP、EXECUTOR)。
  - 狀態檔、錯誤 JSON(只記例外類別)、MODEL 行都沒有金鑰。
- **錄製目錄與路徑穿越**:即時錄製目錄是 `root/live-recordings/<代碼>`,代碼經 ALL_CODES 白名單。展示編號由伺服器產生(時間加 hex),批次 `demo-live-<編號>`,沒有外部輸入。入庫目錄只讀。
- **runner 的模式行**:只有分析端自己在 READY 之後立刻印一行，內容經 `json.dumps`,換行已跳脫。模型輸出不走標準輸出，子行程無從偽造;讀不懂就不採用、照錄製顯示。

看過的改動檔:.gitignore、claims/prompt-injection.json、docs/assets/phase13-inc4/exam-failed-f5.jpg、fallback-f1.jpg、full-F1.jpg、full-F2.jpg、full-F3.jpg、full-F4.jpg、full-F5.jpg、full-F6.jpg、full-F7.jpg、full-summary.jpg、no-propose-f2.jpg、docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase12一鍵展示與HTML報告_計劃.md、Projects/RTB_Phase13AI參與決策_計劃.md、Systems/一鍵展示.md、Systems/分析行程流程與檢查點.md、Systems/展示頁面.md、Verification/Phase13增量4驗收紀錄.md、src/rtb/analyzer/runner.py、src/rtb/demo/basis.py、src/rtb/demo/driver.py、src/rtb/demo/flow_svg.py、src/rtb/demo/launcher/__init__.py、src/rtb/demo/observe.py、src/rtb/demo/page.py、src/rtb/demo/present.py、src/rtb/demo/recordings.py、src/rtb/demo/server.py、src/rtb/demo/state.py、src/rtb/demo/state_store.py、src/rtb/demo/static/demo.css、tests/analyzer/test_runner_model_line.py、tests/demo/fake_recordings.py、tests/demo/test_ai_demo.py、tests/demo/test_ai_launcher.py、tests/demo/test_ai_page.py、tests/demo/test_driver.py、tests/demo/test_page.py、tests/demo/test_present.py(圖檔只看了檔名)。

**關於協調者的提醒**:10:46 寫真帳的不是這一席。我沒起任何 rtb 行程，也沒跑 pytest。不過，發現 1 的重現是在 pytest 之外、也沒有先換掉 `modelledger_view.account_home`,就在 /tmp/sec-opus-p13i4 的複本裡呼叫了一次 `modelgate.open_gate`(即時開關設 1、PATH 沒有 claude)。這違反第二則提醒(在 pytest 之外不要碰會開模型閘道的東西)。這次呼叫只算出帳的路徑，不寫檔，也沒有任何模型呼叫。事後 `ls ~/.rtb` 是空的;`~/.rtb` 的修改時間是 10:52:52,早於這次呼叫。臨時目錄已刪除，沒有留下行程。

3 條，blocking 1。
