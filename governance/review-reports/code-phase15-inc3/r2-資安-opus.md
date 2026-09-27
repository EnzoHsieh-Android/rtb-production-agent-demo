severity: major

# 代碼審 Phase 15 增量 3 r2:資安-opus

席位:資安-opus(站攻擊者那邊,只報能被利用的)。

驗證方式:
- PoC 寫在 scratchpad(`p15i3-r2-sec/test_r2_sec_poc.py`),沿用 `tests/eval/test_rule_mining_cli.py` 的假即時閘道 `Live`,並用 `-p tests.conftest` 載入隔離夾具。
- 沒有呼叫真 claude、沒有設行程的 RTB_MODEL_LIVE、沒有碰 ~/.rtb。
- 13 支 PoC 全部照攻擊預期重現(`13 passed`):r1 原攻擊 4 支、新攻擊 7 支、量時間窗 2 支。

## 驗收(r1 原攻擊重跑)

- F1:部分修好 — 原攻擊(`--recordings-dir` 給空目錄)現在拒絕、清單不動;但把參數換成「指向同一個目錄的符號連結」,照樣能把好錄製記成驗收沒過再重抽(見 N1)
- F2:修好 — 同一個入庫根下,行程 B 在 A 送出途中錄同一個展示編號,被鎖檔擋下、沒送出;鎖以外的繞法另列 N3
- F3:修好 — 暫存目錄改回覆文字後判定,被雜湊比對拒絕、清單沒動;入庫後再改,`--verify` 抓得到
- F4:修好 — outcome、model、finished_at 塞 Markdown 或 HTML,整份清單讀不懂、拒絕產報告;報告儲存格另有 `cell()` 逸出

## 沒有發現洞的面向(簡述)

- **check_gate 回傳的閘道**:只存在同一個行程的記憶體裡,命令列拿不到、也存不下來;`suggest` 會擋掉別的呼叫者開的閘道(ForeignGate)。從命令列找不到路子重送或冒充。
- **預先放置鎖檔或清單**:外部投稿放一個 `.recording.lock` 或亂寫的清單,會讓 `--verify` 顯示「不該有的 .recording.lock」或「讀不懂」,CI 會紅燈。只能讓人錄不了,沒辦法偷偷改變判定結果。
- **格式驗證的漏網**:`\d` 會吃全形數字,不過展示編號要跟 `demo_id_of` 逐字相符,清單機檢抓得到。會進報告的欄位都驗過格式也逸出過。`_line` 只擋 `<`、`>` 和控制字元,但能帶外來字串進驗收行的路徑(檔名、評估版本),本身就會讓驗收沒過,沒辦法偽裝成通過。
- **判定後、手動搬檔前換檔**:入庫目錄的雜湊一定要等於清單記的,`--verify` 抓得到。

---

## N1 判定時把 --recordings-dir 換成指向同一目錄的符號連結,好錄製就被記成驗收沒過,可以重抽(F1 換個參數就重現)

severity: major
blocking: 是
引句:「if staging.resolve() != Path(attempt.staging_dir):」
查證 file: `src/rtb/eval/rule_mining_eval.py:504`(另見 `:549-552`、`:565-567`、`src/rtb/modelrecording.py:216-217`、`src/rtb/eval/rule_mining_recordings.py:288-289`)

**問題**
- `_bound` 先把路徑 `resolve()` 再比。符號連結解開後就是錄製當下記的目錄,雜湊也一樣,所以會放行。
- 接著 `batch_problems` 走共用的 `check_one_batch`,它用 `lstat` 看到最後一段是符號連結,判「是符號連結,不收」。
- 這個原因被當成「錄製內容沒過」,寫成驗收沒過。
- 另一條路也有同樣的結果:`_bound` 之後才開始讀目錄做判定,這段時間裡目錄被換掉。量到 `_bound` 之後第一步 `prepare()` 就要 0.45 秒,時間窗很寬。
- `_seed_problems` 的「第一次成功」會跳過驗收沒過的那幾次,所以清單機檢全過。

**攻擊重現**(PoC `test_n1_symlink_forces_rejected_then_reroll`、`test_n1b_toctou_after_bound`)
1. 即時錄 `phase15-seed-15001-1`,狀態是待驗收。攻擊者慢慢看暫存目錄裡的回覆,覺得不好看。
2. 執行 `ln -s <記下的暫存目錄> /tmp/link`,再跑 `--check-in --demo-id phase15-seed-15001-1 --recordings-dir /tmp/link`。
3. 第 2 步的結果:
   - 預期:目錄參數不對,應該拒絕(結束代碼 2),清單不動。
   - 實際:結束代碼 1,第 1 次被記成驗收沒過,原因是「…link-to-first 是符號連結,不收」。錄製檔的雜湊跟清單記的完全一樣,內容本身沒有問題。
4. 第 1 到 3 步的另一種做法(N1b):照常用原目錄跑 `--check-in`,在它跑 `prepare()` 的 0.45 秒內往暫存目錄丟一個檔。一樣結束代碼 1,記成驗收沒過。
5. 錄 `phase15-seed-15001-2`,判定、搬檔,結果是可入庫。
6. 第 5 步的結果:`manifest_problems == []`。報告的嘗試表只多一列看起來很正常的「驗收沒過」。
7. 第 2 到 5 步可以一直重複,每次都花一次真模型的錢,最後挑最好的一次入庫。

**建議修法**
- `_bound` 不接受符號連結,也不先 `resolve` 再比:
  - 對 `--recordings-dir` 本身做 `lstat`,是符號連結就拒絕;
  - 或改比 `(st_dev, st_ino)`,並要求字面路徑就等於清單記的那一個。
- 判定不要在使用者可寫的目錄上做:
  - 先把目錄內容讀進記憶體,或複製到行程自己建的 0700 暫存目錄;
  - 核對雜湊等於清單記的,之後的 `batch_problems` 和重播都只看這份快照。
- 在快照上判出來的問題,如果屬於目錄層級(符號連結、多出來的檔、子目錄),一律算參數錯,拒絕、清單不動。只有錄製檔本身的欄位沒過,才准記驗收沒過。

---

## N2 收到回覆後、收尾算雜湊之前動手:成功的呼叫會被記成驗收沒過,或被 --abandon 記成呼叫失敗

severity: minor
blocking: 否
引句:「attempt, finished_at=_now(), status=r.FAILED, files=files,」
查證 file: `src/rtb/eval/rule_mining_eval.py:388-390`(另見 `:383`、`:438-448`、`src/rtb/eval/rule_mining_recordings.py:288-289`)

**問題**
- 回覆的錄製檔寫進暫存目錄後,還要經過解析和核對,之後 `_finished` 才算雜湊。攻擊者在這段時間裡可以讀到內容再決定怎麼做:
  - **做法 a**:在暫存目錄建一個子目錄。雜湊算不出來,狀態記成驗收沒過,結果類別照樣是 ok。
  - **做法 b**:直接 `kill -9` 錄製行程,清單停在呼叫中。接著跑 `--abandon --demo-id`,狀態記成呼叫失敗。
- 做法 b 裡,`--abandon` 其實算了暫存目錄的雜湊,清單裡看得到預期鍵的錄製檔確實在;但它還是記成呼叫失敗。
- 呼叫失敗那一次的 `outcome` 是 None,所以 `_seed_problems` 不把它算成第一次成功,機檢照樣全過。

**攻擊重現**(PoC `test_n2a_extra_file_before_finish`、`test_n2b_kill_then_abandon`)
1. 即時錄 `phase15-seed-15003-1`。錄製檔一出現在暫存目錄,攻擊者就用 fswatch 加 `kill -STOP` 停住行程,讀內容。
2. 覺得不好,就 `kill -9` 行程,再跑 `--abandon --demo-id phase15-seed-15003-1`。
3. 第 2 步的結果:
   - 預期:暫存目錄裡已經有 ok 的預期鍵錄製,應該記成待驗收,或者拒絕。
   - 實際:記成呼叫失敗,而且 `files` 裡就是那一份錄製檔。
4. 錄 `-2`,判定,結果是可入庫。第 4 步的結果:`manifest_problems == []`。
5. 做法 a(建子目錄)得到的是驗收沒過、`outcome=ok`,後續一樣可以重抽。

**影響與限制**
- 從錄製檔寫完到算雜湊,量到解析加核對只要大約 1 毫秒,攻擊者要先用 SIGSTOP 之類的方法停住行程,門檻比 N1 高。所以定為 minor。

**建議修法**
- `--abandon` 發現暫存目錄恰好有一份預期鍵、結果 ok 的錄製檔時,不要記成呼叫失敗,改記成待驗收(照常進判定)。
- `_seed_problems` 認第一次成功時,把「呼叫失敗但 `files` 就是預期鍵錄製」的嘗試也算進去。
- `_finished` 看到目錄層級的雜音(子目錄、多出來的檔)時,只要預期鍵那一份在而且是 ok,就不要記驗收沒過,交給判定處理(配合 N1 的快照判定)。

---

## N3 鎖只在單一 checkout 裡有效:另一個 worktree,或手動刪鎖檔,就能同一個展示編號多抽

severity: minor
blocking: 否
引句:「_save(io.root, _with(manifest, started))  # 先記再送:行程中斷也不會重用這個序號」
查證 file: `src/rtb/eval/rule_mining_eval.py:477`(另見 `:466`、`:480`、`:336-351`、`src/rtb/modelrecording.py:152-161`)

**問題**
- 入庫根、鎖檔和批次清單,都是從「這一份 checkout 的專案根」往下算的。開兩個 worktree,就有兩把鎖、兩份清單。帳號家目錄只有一本花費帳,它記了兩次呼叫,但 CI 看不到那本帳。
- 在同一個 checkout 裡,鎖是唯一的防線:
  - 第 477 行寫「呼叫中」用的是登入預檢(最多 10 秒)之前讀的舊清單,沒有重新讀;
  - 第 480 行收尾時,也不比對同一個展示編號是不是已經有別的開始時間。
  - 所以只要把鎖檔刪掉,F2 就原樣重現。

**攻擊重現**(PoC `test_n3_two_checkouts_draw_twice`、`test_n3b_rm_lock_reopens_f2`)
1. 在 worktree A 和 worktree B 各跑一次即時錄 `phase15-seed-15001-1`。
2. 第 1 步的結果:
   - 預期:第二次因為序號已經用掉而拒絕。
   - 實際:兩邊都 ok,後端各被呼叫 1 次。任選一邊判定、入庫,再提交那一邊的清單:只有 1 次嘗試,`manifest_problems == []`。
3. 同一個 checkout 的做法:行程 A 在登入預檢時,攻擊者 `rm .recording.lock`,再跑行程 B 錄同一個編號。
4. 第 3 步的結果:A 和 B 都 ok,也都送出了;清單只剩 A 那一筆待驗收,B 的呼叫完全沒留紀錄。

**影響與限制**
- 要操作者刻意開多個 checkout 或手動刪鎖檔,所以跟 r1 F2 一樣定為 minor。

**建議修法**
- 開錄前先查帳號家目錄那本花費帳(它是跨 checkout 唯一的紀錄):如果已經有 `caller=rule_mining`、`demo_id` 是 `phase15-seed-<種子>-*` 但不在這份清單裡的列,就拒絕。
- 判定時再比一次:花費帳裡這個種子的呼叫數,要等於清單裡的嘗試數。
- 同一個 checkout 裡:
  - 寫入「呼叫中」之前,在鎖內重新讀清單;
  - 收尾時如果同一個展示編號的 `started_at` 或 `staging_dir` 已經不是自己的,就拒絕覆蓋,改記衝突。

---

## N4 暫存目錄在錄製途中多一個一般檔(例如 Finder 產生的 .DS_Store),清單就寫進自己讀不懂的值,三個種子全部卡死

severity: minor
blocking: 否
引句:「("files", all(isinstance(k, str) and RECORDING_NAME.fullmatch(k) and isinstance(x, str)」
查證 file: `src/rtb/eval/rule_mining_recordings.py:190-191`(另見 `:320-329`、`src/rtb/eval/rule_mining_eval.py:438`、`:383`)

**問題**
- 寫入和讀取的規則不一致:
  - 寫入端(`file_hashes`)收下任何一般檔的檔名,`_finished` 和 `--abandon` 都會把這些檔名寫進清單;
  - 讀取端(`_values_problem`)只接受 64 碼十六進位加 `.json` 的檔名,其他一律判整份清單讀不懂。
- 錄製期間(最長 60 秒以上)暫存目錄只要多出一個一般檔,清單就壞了。清單壞掉之後,即時錄製、`--check-in`、`--verify`、`--abandon` 全部拒絕。
- 工具本身沒有復原的路,只能手改清單,而手改清單正是這套稽核要避免的事。

**攻擊重現**(PoC `test_n4_stray_file_bricks_manifest`)
1. 即時錄 `phase15-seed-15001-1`。錄製途中,暫存目錄出現 `.DS_Store`:在 Finder 裡打開這個目錄就會產生,也可以是有人刻意放的。
2. 第 1 步的結果:
   - 預期:這次嘗試記成驗收沒過或拒絕,清單照樣讀得懂。
   - 實際:清單寫進 `files: {".DS_Store": …}`。
3. 之後的 `--verify`、另一個種子的即時錄製、`--abandon`,結束代碼都是 2,訊息是「批次清單讀不懂:嘗試的 files 值不合格式」。

**建議修法**
- `file_hashes` 遇到不符合錄製檔名的一般檔,跟遇到子目錄一樣,丟 `Unhashable`。
- `_finished` 和 `_abandon` 只把合格的檔名寫進清單;把不合格的原因寫進 `problems`(只記原因類別,不記原始檔名)。
- 另外加一條測試:寫入端產生的每一份清單,都一定要能被 `loads` 讀回。

---

新發現:N1 到 N4 共 4 條(1 major blocking、3 minor)。
