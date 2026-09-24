severity: major

**Finding 1:情境超過時限後,情境本體的執行緒沒有被停住,會在收尾之後再起子行程(孤兒行程帶著金鑰),還會把已經結束的情境改回「執行中」**
severity: major
blocking: 是
引句:「world.stop.set()  # 只停這個情境的觀察迴圈,不影響整次展示」
file: `src/rtb/demo/driver.py:696`、`src/rtb/demo/driver.py:118`、`src/rtb/demo/driver.py:624-625`
- **問題**:時限到了,`_attempt` 只做兩件事:設停止旗標,然後在 finally 裡 `world.close()`。情境本體跑在另一條 daemon 執行緒,它只在 `watch` 裡看旗標,其他步驟照樣往下走:
  - `launcher.start` 最多等 20 秒
  - `restart_shifted`、F7 連起 8 個執行迴圈
  - `_race_two_analyzers`
  - `_wait_for_confirmation` 的 finally
- **後果一(孤兒行程)**:`close()` 已經清完行程清單之後,本體才 `self.processes.append(process)`,之後沒有人會再收這些行程。它們開了新的行程群組,展示行程結束之後還活著,環境裡帶著能力金鑰與稽核金鑰。這符合判準的「子行程失控、留孤兒」。
- **後果二(展示狀態被改錯)**:時限剛好在 F7 自動查核跑完的時候到,情況會這樣走:
  1. 本體仍會進 `_wait_for_confirmation`:先 `set_confirmation`,這時一個已經收掉的情境短暫出現確認請求;
  2. finally 裡 `mark_status(world.code, "running")`,把 `run_one` 剛寫好的「沒跑完:超過時限」蓋掉;
  3. 狀態庫最後停在 `running`,原因被清成空值,`finished_at` 卻有值。頁面(2b)會把一個失敗的情境顯示成還在跑。
- **跟第 1 輪的關係**:第 1 輪各席看的是啟動器;驅動程式是這一輪才進來的,這個缺口是新的。

重現(在 /tmp 複本):
- **孤兒**:自訂情境,時限 1 秒;本體先 `sleep(1.5)`,再 `world.start_platform()`、`world.start_inbox()`。
  - `Driver.run` 回傳 `incomplete / 超過情境總時限 1 秒`;
  - 腳本結束 4 秒後,`pgrep` 還看得到 `rtb.dsp.server --db …/d1/X/dsp.db` 與 `rtb.executor.inbox_server`;
  - `ps eww` 看得到 DSP 環境裡有 `RTB_CAPABILITY_KEY`、`RTB_DSP_AUDIT_KEY`(值已遮蔽);
  - 最後用 pkill 收掉。
- **狀態回寫**:本體 `sleep(1.5)` 後呼叫真的 `driver._wait_for_confirmation`(只把 `_earliest_waiting` 換成固定請求)。
  - 剛結束時讀狀態庫:`status='incomplete'`;
  - 1.5 秒後再讀:`status='running', reason=None`,`finished_at` 有值。
- **旁證**:ps 裡另有 `pytest-539/test_the_driver_runs_every_sce0/…/F1、F2、F3/inbox.db` 三支收件口還掛著,不是我起的,可能是同一類漏收。

建議:
- 時限到了之後要 join 情境執行緒(給上限)再 `close()`;
- 或讓 `World.start` 在 `stop` 已設或 World 已關時直接丟例外、不起行程(檢查和 append 放在同一把鎖裡);
- `_wait_for_confirmation` 開頭檢查 `world.stop`;finally 不寫 `mark_status("running")`,改成只有情境還沒結束才恢復;
- 補一條測試:「時限在起行程途中到期,Driver.run 回來後沒有殘留行程,狀態維持沒跑完」。

**Finding 2:確認快照裡給人看的「關卡」是寫死的文字,跟實際簽章用的 stage 沒有綁在一起**
severity: minor
blocking: 否
引句:「("關卡", "全部廣告加起來會超過總上限"), ("租戶", TENANT)))」
file: `src/rtb/demo/driver.py:542-548`
- **問題**:`stage=str(rows[0][1])` 取自收件口,伺服器也拿它簽章。給人逐項勾的 `numbers` 卻固定寫「全部廣告加起來會超過總上限」。
- **會出錯的情況**:APPROVABLE 另有 `budget_increase_too_large`。最早停下的那一筆要是停在比例關,人會以為自己確認的是總上限超額,實際簽下的是比例超額。
- **目前影響**:F7 的加額都是一成,只會停在總上限關,所以是潛伏問題。
- 另一個小點:`current = world.budget(...) or 0` 把「查不到廣告」當成預算 0,快照會顯示「0 → 110」。

重現:讀程式比對 `stage` 與 `numbers` 的來源,沒有實跑。

建議:
- 「關卡」的文字由 `stage` 對照表產生;
- 查不到廣告就 `ScenarioFailed`,不要補 0。

**Finding 3:s3 的上層目錄核對用 `stat()`,會跟著符號連結走,核對的是連結指向的目錄,不是那個路徑本身**
severity: minor
blocking: 否
引句:「info = base.stat()」
file: `src/rtb/demo/faults/delivery.py:61-63`
- **問題**:共用目錄(例如 `/tmp/x`)下放一個別人擁有的符號連結,指向我自己的 0700 目錄,核對會通過。之後連結的擁有者可以改指向,把後續寫的故障設定檔、資料庫、租戶設定全部導到他的目錄。
- **上一層也沒查**:base 的上一層是別人可寫的,一樣能把整個 base 換掉。
- **定級**:這屬於刻意的跨使用者攻擊,在「防忘記」的威脅模型邊緣,所以列 minor。

重現:在 /tmp 複本做了 `ln -s realbase linkbase`,`prepare_root(linkbase, 'd9')` 通過,回傳 `linkbase/d9`。沒有另一個使用者可以示範改指向。

建議:
- 改用 `base.lstat()`,是符號連結就拒;
- 或先 `resolve()`,之後一律用解析後的路徑。

**Finding 4:租戶總上限用 `or` 補寬值,明確傳 0 會變成十億**
severity: minor
blocking: 否
引句:「"aggregate_limit": aggregate_limit or LOOSE_AGGREGATE_LIMIT}」
file: `src/rtb/demo/driver.py:109`
- **問題**:`make_f7(limit=0)`(想展示「一筆都放不出去」)寫進設定檔的會是 1,000,000,000。
- **為什麼只列 minor**:逐廣告核對會發現放行 300 個、不等於 0,情境判成沒跑完,不會假綠。
- 設定檔本身以 O_EXCL、0600 寫在 0700 的情境子目錄,權限沒問題。

重現:讀程式,沒有實跑。

建議:改成 `LOOSE_AGGREGATE_LIMIT if aggregate_limit is None else aggregate_limit`。

**第 1 輪 s1–s6 繞法複查**(在 /tmp 複本實跑 child)
- **s1**:下面五種全部以代碼 3 拒絕,根目錄外的目錄只留下我預先放的 tenants.json:
  - `--db=OUT`
  - 縮寫 `--d OUT`
  - 重複 `--db IN --db=OUT`
  - 縮寫 `--tenant OUT`
  - 根目錄內指向外面的符號連結
- **s2**:最上層 pyproject 已禁 `rtb.demo`;`_imports` 能還原相對匯入;另有 `sys.modules` 實測,沒看到繞法。
- **s3**:符號連結的情況見 Finding 3;`-P` 已加上。
- **s4**:核對完會 pop 掉隨機值,設定檔改名為 `.used`,並行兩次只有一次能改名成功。`ps eww` 仍看得到啟動時的環境,但設定檔已作廢,重放不了。
- **s5**:TypeError 與 ValueError 已轉成拒絕;`Path.resolve()` 碰上連結迴圈丟的 OSError 沒有被接住,需要同一個使用者竄改才會發生,不另列。
- **s6**:沒看到繞法。

**其他查過、沒發現問題的部分**
- **F6 行程內重放**:
  - 呼叫的是 `replay.run` 正式入口同一支,只寫進情境自己的 inbox.db;
  - 操作人一向是命令列參數,正式工具本來就不驗是誰;
  - 權限沒有被繞過,也不會記到正式資料庫或別人名下。
- **F5 樣本字串**:只在 `rtb.demo.driver` 當 DSP 種子資料;不會被執行,也不會進觀察器或狀態庫的原因欄。展示套件不被正式程式匯入(已有 sys.modules 實測)。
- **金鑰流向**:
  - 狀態庫的結構與寫入點都不帶金鑰,確認請求也沒有;
  - `DemoKeys` 的 repr 已遮蔽;
  - 例外訊息進狀態庫的只有子行程 stdout 第一行和例外字串,看不到金鑰來源;
  - httpkit 不記請求;子行程日誌在 0700 根目錄內。
  - S1004 後半(金鑰只在伺服器記憶體)沒有提前寫進狀態庫。

**清理**:我的實驗目錄 /tmp/p12i1-r2-sec 已刪,我起的行程已用 pkill 收掉,最後 `pgrep -fl p12i1-r2-sec` 沒有輸出。ps 裡還有下列行程,都不是我起的,請相關席位確認:
- `/tmp/audit-h-copy*` 的 DSP
- `pytest-539` 的三支收件口
- `/tmp/p12i1r2-drv` 的 F3 執行迴圈與分析端

**看過的檔**(涵蓋 r2-snapshot.patch 的全部程式改動檔):
- 展示套件:
  - `src/rtb/demo/driver.py`
  - `src/rtb/demo/state_store.py`
  - `src/rtb/demo/observe.py`
  - `src/rtb/demo/keys.py`
  - `src/rtb/demo/state.py`
  - `src/rtb/demo/flow.py`(從 observe 的引用看)
  - `src/rtb/demo/launcher/__init__.py`
  - `src/rtb/demo/launcher/child.py`
  - `src/rtb/demo/faults/delivery.py`
  - `src/rtb/demo/faults/dsp.py`
  - `src/rtb/demo/faults/executor.py`
  - demo、launcher、faults 三個 `ruff.toml`
- 分析端:`src/rtb/analyzer/runner.py`、`flow.py`、`task_store.py`、`ruff.toml`
- 執行端:`src/rtb/executor/replay.py`、`approve.py`、`inbox_server.py`、`runner.py`、`ruff.toml`
- 其他正式程式:
  - `src/rtb/dsp/server.py`
  - `src/rtb/eval/record.py`
  - `src/rtb/ops/metrics.py`、`slo.py`、`trace.py`、`ruff.toml`
  - domain、dsp、eval 三個 `ruff.toml`
  - `pyproject.toml`
- 測試:`tests/demo/test_boundaries.py`、`tests/demo/test_driver.py`(確認流程)
- 圖譜筆記:`Systems/一鍵展示.md`、計劃第 5 版的相關段落
- 其餘文件類改動與 claims 只掃過標題

4 條,blocking 1。
