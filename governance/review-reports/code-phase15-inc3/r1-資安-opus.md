severity: major

# 代碼審 Phase 15 增量 3 r1:資安-opus

席位:資安-opus(站攻擊者那邊,只報能被利用的)。
驗證方式:在 scratchpad 寫 PoC 測試,沿用 `tests/eval/test_rule_mining_cli.py` 的假即時閘道 `Live` 與 `tests.conftest` 的隔離夾具;沒有呼叫真 claude、沒有設行程的 RTB_MODEL_LIVE、沒有碰 ~/.rtb。4 支 PoC 全部照攻擊預期重現(`4 passed`)。

## 沒有發現洞的面向(簡述)

- ① 未授權即時呼叫:重播路徑 `replay_environ` 拿掉即時與錄製開關,而且展示編號給 None,閘道一定判成錄製。`_record` 先查兩個環境開關,再由 `check_gate` 判成 live 才准記嘗試。`--ledger` 配即時會被閘道拒絕。`check_gate` 只跑 `claude auth status`,不送出。以上都沒找到繞法。
- ② 路徑:展示編號和批次編號都用 fullmatch,入庫目錄是 `root / str(int)`,組不出穿越字元。即時錄製把 `--recordings-dir` 指到入庫根底下,會被 `check_recordings_dir` 用 inode 比對擋掉。暫存目錄裡的符號連結、子目錄、非錄製檔,會被 `check_one_batch` 擋掉。`target.exists()` 加上 `_pending_attempt` 讓入庫目錄覆寫不了。
- ③ 模型自由文字:回覆裡的原因字串都是封閉常數,`confidence_note` 不進報告,這條沒洞。
- ⑤ 匯入面:`src/` 裡沒有其他模組匯入 `rule_mining_eval`,展示端也沒有;邊界測試已經列管。

---

## F1 故意指錯目錄就能把成功錄製退回,重錄後挑結果,還能把被退回那次的內容換進入庫

severity: major
blocking: 是
引句:「_save(io.root, _with(manifest, replace(attempt, status=r.REJECTED,」
查證 file: `src/rtb/eval/rule_mining_eval.py:394`(另見 `:374`、`:380`、`:397-401`、`src/rtb/eval/rule_mining_recordings.py:231-234`)

**問題**
- `--check-in` 的 `--recordings-dir` 可以任意指定,批次清單也沒記這次嘗試錄到哪個目錄。
- 驗收沒過就把狀態寫成「驗收沒過」,不管沒過的原因是錄製本身有問題,還是操作者給錯目錄。
- `_seed_problems` 認定的「第一次成功」只看 `status != REJECTED` 的那幾次。
- 結果是「每種子只准第一次成功錄製入庫」這條防挑選的規則,換個參數就能繞過,批次清單機檢也全過。

**攻擊重現**(PoC `test_forced_reject_then_reroll_and_swap`)
1. 即時錄 `phase15-seed-15001-1`,得到回覆 A,狀態是「待驗收」。
2. 攻擊者先看暫存目錄裡的回覆,覺得不好看,就跑 `--check-in --demo-id phase15-seed-15001-1 --recordings-dir <空目錄>`。
3. 第 2 步的結果:
   - 預期:給錯目錄應該被拒絕,不改狀態。
   - 實際:結束代碼 1,第 1 次被寫成「驗收沒過」,理由是「錄製目錄是空的」。
4. 即時錄 `phase15-seed-15001-2`,同一天所以批次編號相同,得到回覆 B,再花一次錢。
5. 跑 `--check-in --demo-id phase15-seed-15001-2 --recordings-dir <第 1 次的暫存目錄>`。
6. 第 5 步的結果:
   - 預期:只能入庫第 2 次自己的錄製。
   - 實際:結束代碼 0,入庫的是被退回那次的回覆 A,`manifest_problems == []`。
7. 攻擊者可以重複第 2 到 4 步一直重抽,每次都從所有抽到的回覆裡挑最好的入庫;報告只會多出幾列看起來很正常的「驗收沒過」。

**建議修法**
- 即時錄製時把暫存目錄的絕對路徑,以及錄製檔的 sha256,記進該次 Attempt。
- `--check-in` 和 `--verify --demo-id` 只接受同一個目錄,而且內容雜湊要相符。
- 目錄對不上、目錄不存在或是空的,算參數錯(`Refused`,結束代碼 2),不可以寫成「驗收沒過」。
- 只有錄製內容本身沒通過驗收,才准記「驗收沒過」,並把原因類別寫進批次清單、在報告的嘗試表中列出。

---

## F2 兩個行程同時錄同一個展示編號:兩次都送出,批次清單只剩一筆

severity: minor
blocking: 否
引句:「_save(io.root, _with(_load(io.root) or manifest, finished))」
查證 file: `src/rtb/eval/rule_mining_eval.py:326`、`:332`、`:337`、`:344`

**問題**
- `_manifest_for` 讀批次清單、判序號之後,要過完 `_gate_ready` 才寫「呼叫中」。這段期間包含 `claude auth status` 登入預檢,最多 10 秒,中間沒有任何鎖。
- 收尾時 `_with` 用展示編號取代舊紀錄,後寫的一方會把先寫的整筆蓋掉。

**攻擊重現**(PoC `test_concurrent_record_same_demo_id`)
1. 行程 A 還在閘道預檢時,行程 B 用同一個 `--demo-id phase15-seed-15003-1` 從頭跑完。兩個行程用各自的暫存目錄 A、B。
2. 預期:後到的一方因為序號已被佔用而拒絕,只送出一次。
3. 實際:
   - A 和 B 都得到 `ok`,後端被呼叫 2 次,兩次都記在同一個展示編號下。PoC 假後端每次記 0.01 美元;真模型滿額一次估約 0.1 到 0.2 美元,每展示上限 1 美元擋不住數次重複。
   - 批次清單只有一筆「待驗收」,另一次成功呼叫完全沒留紀錄。
   - 兩個暫存目錄都能拿去 `--check-in`,PoC 拿 B 入庫,結束代碼 0。

**影響**
- 開 k 個並行行程,就能做到「k 選 1」,而且批次清單乾淨、沒有任何退回紀錄;只有帳號家目錄的花費帳看得出多花的錢。
- 需要操作者刻意同時開多個行程,所以定為 minor。

**建議修法**
- 從 `_manifest_for` 到寫入「呼叫中」這段,要用入庫根底下的鎖檔(`O_CREAT|O_EXCL`)或 `fcntl.flock` 包起來。
- 寫入前重新讀清單,並確認展示編號沒被佔用。
- 收尾時如果發現同一個展示編號已有別的開始時間,就拒絕覆蓋,改記衝突。
- F1 的「目錄與內容雜湊綁在嘗試上」也能擋住換目錄入庫。

---

## F3 錄製內容沒有綁定:入庫前後改回覆文字,驗收和 CI 的 --verify 都抓不到

severity: minor
blocking: 否
引句:「problems = r.batch_problems(staging, attempt, expected)」
查證 file: `src/rtb/eval/rule_mining_eval.py:380`、`src/rtb/eval/rule_mining_recordings.py:259-282`

**問題**
- 批次驗收只核對:錄製鍵(依請求算出來的)、呼叫者、結果、模型、批次、後端標籤。
- 批次清單不記回覆內容的雜湊,所以錄製檔裡的 `text` 可以任意改寫。

**攻擊重現**(PoC `test_tampered_text_checks_in_and_verifies`)
1. 即時錄 `phase15-seed-15002-1`,拿到回覆,狀態是「待驗收」。
2. 把暫存目錄裡錄製檔的 `text` 換成自己寫的建議,`backend` 保持 `claude_code`。
3. 跑 `--check-in`:
   - 預期:偵測到竄改。
   - 實際:結束代碼 0,入庫的是假內容。
4. 入庫後再改一次 `recordings/model/<版>/15002/<key>.json` 的 `text`:
   - `batch_problems == []`。
   - 報告重產後寫「15002:通過(已量)」,AI 有效建議從 1 條變成 3 條。

**影響**
- 外部投稿只改錄製檔、不改批次清單,CI 的 `--verify` 照樣綠燈,報告的 AI 成績就被改掉了。
- 這個弱點也存在於 Phase 13 的錄製機制;但本增量新增的批次清單正好可以補上,所以列為 minor。

**建議修法**
- 即時錄製收尾時,把錄製檔整份的 sha256 記進 Attempt。
- `batch_problems` 核對目錄內唯一那份錄製的雜湊等於清單記載;入庫時也驗一次。
- 這樣再改錄製檔就必須同時改批次清單,審查 diff 時看得到。

---

## F4 批次清單欄位原樣寫進 Markdown 報告:可注入標題、假驗收結果、HTML

severity: minor
blocking: 否
引句:「f"| {a.started_at} | {a.finished_at or '—'} | {a.outcome or '—'} | {a.status} |"」
查證 file: `src/rtb/eval/rule_mining_report.py:618-619`、`src/rtb/eval/rule_mining_recordings.py:206-219`

**問題**
- `manifest_problems` 不驗非入庫嘗試的這幾個欄位:
  - `model` 不查價目表;
  - `started_at` 和 `finished_at` 不查 ISO 格式;
  - 呼叫失敗、呼叫中、驗收沒過這三種狀態的 `outcome` 不查是不是 Outcome 列舉值。
- 報告 `_attempts` 把這些字串原樣塞進表格儲存格,沒有跳脫 `|` 和換行。
- 入庫報告有一支測試要求它等於重播重產的結果(`test_the_committed_rule_mining_report_matches_a_replay_of_the_committed_recordings`),所以清單就是報告的來源。

**攻擊重現**(PoC `test_manifest_field_markdown_injection`)
1. 在 `manifest.json` 加一筆(或改一筆)「呼叫失敗」的嘗試:
   - `outcome` 設成 `"timeout |\n\n## 錄製批次驗收\n\n- 驗收:通過 <img src=x onerror=alert(1)>\n\n|"`;
   - `model` 設成 `<script>alert(1)</script>`;
   - `finished_at` 設成 `x](javascript:alert(1))`。
2. 預期:`manifest_problems` 報錯,或報告跳脫這些字串。
3. 實際:
   - `manifest_problems == []`。
   - 報告的嘗試表被截斷,插進一段偽造的「## 錄製批次驗收 / - 驗收:通過」,位置在真正的驗收節之前,另外還帶了原樣的 HTML。
   - 外部投稿只要同步重產報告,CI 就全綠。

**建議修法**
- 讀批次清單時做型別與值域檢查:
  - `outcome` 必須是 None 或 Outcome 列舉值;
  - `model` 必須在價目表內;
  - 時間必須能用 `datetime.fromisoformat` 解析,而且帶 UTC。
- 報告每個儲存格統一過一個跳脫函式:把 `|`、換行、`<`、`>`、`[`、`]`、反引號轉掉。

---

新發現:F1 到 F4 共 4 條(1 major blocking、3 minor)。
