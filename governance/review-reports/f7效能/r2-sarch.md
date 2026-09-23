severity: major

### F1 處理待核可要拿簽發器的快取,但簽發器只以 `Signer` Protocol(僅 `sign`/`grant`)注入執行迴圈,設計沒說怎麼跨過去拿

severity: major
blocking: 是
引句:「處理待核可改用它那個簽發器的快取(同一個執行迴圈、同一份設定檔)」

說明:執行迴圈(`Executor`)對簽發器的型別依賴是 `execution.py` 裡定義的 `Signer` Protocol,只宣告 `sign` 與 `grant` 兩支方法(`file: src/rtb/executor/execution.py:161-170`),不含任何跟快取有關的介面。`process_awaiting`(`file: src/rtb/executor/execution.py:622-635`)現在是直接呼叫模組層級函式 `load_tenants(self.config_path)`,完全不經過 `self.signer`。設計要它改成「用它那個簽發器的快取」,但快取照設計是掛在 `CapabilitySigner` 物件自己身上(`file: src/rtb/executor/capability_signer.py:125-130`,目前只有 `_key` 一個私有欄位),而 `Executor.signer` 在型別系統裡只是 `Signer` Protocol,沒有方法可以要到那個快取或那個快取物件。

具體例:實作時要嘛(a)把 `Signer` Protocol 加一支新方法(例如回傳快取過的租戶清單,或直接把快取物件露出去),這是這份設計完全沒提到、也沒經過架構對齊審視的介面變更;要嘛 (b) `process_awaiting` 用 `self.signer._cache` 這種方式伸手進 `CapabilitySigner` 的私有欄位——這正是 Protocol 存在的目的所要擋的事(呼叫端只認抽象介面,不認具體類別的內部狀態),等於跨層直呼。兩條路design都沒有選,而是留給實作者臨場決定,結果極可能落在 (b)。

建議改法:設計應該明確裁定要不要修改 `Signer` Protocol(例如新增一支 `cached_tenants(config_path) -> tuple[Tenant, ...]` 之類的公開方法,由 `CapabilitySigner` 實作、`Executor.process_awaiting` 呼叫它),把這個決定跟其他核心裁定放在同一節,而不是留白讓實作時各自發揮。

---

### F2 翻案 Phase 6 決策的寫法沒有走專案記錄決策的機制(decisions 欄位/決策編號/回頭標被取代)

severity: major
blocking: 是
引句:「Phase 6 增量 1 設計審第 2 輪裁定」

說明:CLAUDE.md 的鐵則明講決策要用指令改(`lumos set`/`append`/`decision-add`),`RULE:` 一類的規則行要有 `[since:]`/`[retire:]` 等欄位,而翻案的敘述本身沒有指向任何決策編號或 `decisions:` 欄位——它只是在計劃筆記正文裡用一段散文說「這是翻案,照實寫」,引用 `[[Projects/RTB_Phase6權限護欄與總曝險_計劃]]` 跟 `Systems/外部寫入嘗試紀錄` 的同一句話當出處。查了這兩處(`file: docs/rtb-production-agent-demo-knowledge/Systems/外部寫入嘗試紀錄.md:60`、`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md:78`),原始決策本身也只是計劃正文裡的一條列點(`加總在程式裡用整數累加,不在 SQL 裡加總(第 2 輪外家席:...)`),不是走 `decisions:` 欄位或有決策編號的紀錄(全圖譜只有 `RTB_Agent_Phase0架構.md` 一篇真的有 `decisions:` 欄位)。

具體例:設計沒有交代——實作完成後,Phase 6 那篇 `[[Projects/RTB_Phase6權限護欄與總曝險_計劃]]`(目前 `status: done`)裡那條決策要不要回頭標成 `[status:superseded]`,`Systems/外部寫入嘗試紀錄.md:60` 那一句現況描述("加總在程式裡用整數累加")要不要同步改成"多數情況資料庫端加總、退回情況才在程式裡累加",或者兩篇都保留原句只在旁邊加註「已被 F7 效能計劃部分翻案」。沒有這個裁定,下一個 session 讀到 Phase 6 那篇時仍會誤以為"加總一律在程式裡做"是唯一現況。

建議改法:在「核心裁定」或「落點」節明講——實作完成時用 `lumos decision-add` 對這個翻案記一筆決策(帶決策編號),並回頭在 Phase 6 那篇與 `Systems/外部寫入嘗試紀錄.md` 現有那句加 `[status:superseded]` 或等效標記,指向這篇 F7 效能計劃。

共 2 條,blocking 2 條。
