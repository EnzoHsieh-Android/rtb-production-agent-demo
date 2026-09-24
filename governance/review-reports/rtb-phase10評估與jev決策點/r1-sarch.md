severity: major

### F1 評估模組/評估集要放在哪個既有分層、要不要新的匯入禁令,設計完全沒交代
severity: major
blocking: 是
引句:「評估模組與評估集是新東西,新開一篇 Systems 家」

說明:設計只交代要幫增量 2 新增的程式開一篇 Systems 筆記(`docs/rtb-production-agent-demo-knowledge/Systems/`),但完全沒說這些新檔(生成器、評分、報告)要落在 `src/rtb/` 底下哪個既有分層,或要不要開一個新的頂層套件。這件事不是文件瑣事,是專案既有每加一道行程/分層邊界就要在該目錄放一份 `ruff.toml` 宣告匯入方向的慣例:`analyzer`/`executor`/`dsp` 三個行程互相禁止匯入,`ops` 則明寫「依賴方向只有維運套件 → 分析端與執行端」且被其他三層禁止反向匯入(`src/rtb/analyzer/ruff.toml:6`、`src/rtb/executor/ruff.toml:6`、`src/rtb/dsp/ruff.toml:6`、`src/rtb/ops/ruff.toml:1-3`)。

評估模組要呼叫增量 1 抽出的候選介面與路由閘(在 `rtb.analyzer.policy`),等於一定要匯入 `analyzer`;它也極可能要用 `rtb.domain.metrics.pacing()` 之類的純計算來造情境。這跟 `ops` 目前「只讀,匯入 analyzer/executor 的讀取介面」的方向表面相似,但用途完全不同(`ops` 是讀正式資料庫,評估是離線造合成資料、不接觸正式資料庫),硬塞進 `ops` 會讓那份 `ruff.toml` 的既有注解(維運只讀的角色定義)失真;另開一個新頂層套件(類似 `ops` 的地位、只是方向改成「評估 → analyzer/domain」)則需要新增一份 `ruff.toml` 宣告「不得被 analyzer/executor/dsp 反向匯入」,否則規則作者(`policy.py`)理論上可以直接 `import` 評估集內容,直接違背文件自己寫的「規則作者不得看評估集的逐筆答案後回頭調規則」那條外洩防線——這條防線目前完全沒有機械邊界撐著,只是一句自律宣示。

建議在〈設計〉補一句:評估模組落在哪個目錄(例如新開 `src/rtb/eval/`)、比照 `ops` 的模式新增一份 `ruff.toml`(禁止 `analyzer`/`domain`/`executor`/`dsp` 反向匯入評估模組,防止規則作者存取評估集),並明說評估模組允許匯入 `analyzer`(候選介面)與 `domain`(純計算)。
file: `src/rtb/analyzer/ruff.toml:1-6`
file: `src/rtb/ops/ruff.toml:1-3`

### F2 候選判斷介面用「一支函式」帶過,沒有比照既有同形狀介面用 Protocol
severity: minor
blocking: 否
引句:「候選判斷的介面:一支函式,輸入同上、輸出三類之一或」

說明:`src/rtb/analyzer/flow.py` 開頭明文寫了本專案挑選 `Protocol` 還是 `Callable` 的判準:三個既有可替換介面(`EvidenceSource`、`Decide`、`Submit`)因為「各自有具名的多個參數與語意(不是單純『一個函式』)」而刻意選 `Protocol`,只有像 `OnDspCall`(單一動作的簡單通知回呼,無回傳語意)這種才維持 `Callable`(`src/rtb/analyzer/flow.py:20-23`、`src/rtb/analyzer/dsp_client.py:29-31`)。`Decide` 本身就是同一個決策函式家族目前的介面範本:`task, evidence, now -> Decision`,三向枚舉結果(`src/rtb/analyzer/flow.py:73-79`)。

計劃裡的候選介面收「這次決策用的可信現況與指標」這種具名多欄位輸入,輸出是「值得加/不值得加/證據不足/不知道」四路語意化結果,且未來設想有多種實作(現行規則、LLM、Jev)——形狀跟 `Decide` 幾乎一模一樣,照專案自己寫的判準應該一樣用 `Protocol`,但文件只寫「一支函式」,沒表態要不要走 `Protocol`。如果實作時圖方便用裸的 `Callable[[...], ...]`,就是在同一個「決策函式家族」裡另立一套跟 `Decide` 不一致的介面風格,型別檢查器也核對不到候選實作要遵守的簽章文件;但這不影響任何合約([S700]–[S710])的可測性或正確性,屬於實作時順手就能對齊、不需要現在卡住整份設計的細節。

建議在〈設計〉的候選介面段落補一句「候選判斷比照 `Decide` 用 `typing.Protocol` 定義,不用裸 `Callable`」,並讓 [S702] 的測試連著介面的型別契約一起釘。
file: `src/rtb/analyzer/flow.py:20-23`
file: `src/rtb/analyzer/flow.py:73-79`

### F3 評估集「版本化入庫」沒說清楚是進版本控制的資料檔還是寫進正式行程共用的 SQLite,專案沒有這類先例可循
severity: major
blocking: 是
引句:「標準答案與生成的情境一起版本化入庫,用雜湊釘住」

說明:「入庫」在本計劃是全圖譜第一次出現的詞(`docs/rtb-production-agent-demo-knowledge/` 底下其餘筆記查無此詞),沒有既有慣例可直接套用,語意含糊到可以讀成兩種完全不同的架構:(a) 一份提交進版本控制的資料檔(JSON/JSONL 之類);(b) 寫進分析端既有的 SQLite(`TaskStore`)。這兩種選法在既有分層決策下後果差很多:Phase 0 架構明文分析端有自己獨立的 SQLite 檔案只放執行期任務狀態,是為了「分析行程處理不可信文字、最可能被劫持」而刻意拆出來的攻擊面隔離(`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Agent_Phase0架構.md:60-66`,決策 d9/d10);如果評估集是離線工具產生的合成資料還被寫進同一個 SQLite,等於把「跟正式行程隔離的評估製品」混進「正式行程信任邊界內」的儲存,需要另外交代為什麼這樣做不破壞 d9/d10 的隔離理由。

反之,本專案目前完全沒有「版本化、雜湊釘住的資料集當檔案進 repo」這種先例(`find` 全專案查無 fixture/golden/dataset 類檔案,`.jsonl` 只有 lumos 治理帳本用途),所以選 (a) 也是要新建一種尚無範例可循的檔案類別,需要決定放在哪個目錄(`tests/` 底下當測試固定資料?還是新開的評估模組目錄底下當程式產物?)才能滿足「每支檔案有家」與 CLAUDE.md 對資料檔案位置的一致性。這個含糊直接牽動 Phase 0 已裁定的行程隔離理由,不是實作細節,應在進入增量 2 前先裁清楚。

建議在〈設計〉明講「入庫」指的是哪一種:如果是資料檔案,寫清楚存放路徑與格式;如果考慮寫進 SQLite,要額外交代跟 d9/d10 隔離理由的相容性。
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Agent_Phase0架構.md:60-66`

---

共 3 條,blocking 2 條。
