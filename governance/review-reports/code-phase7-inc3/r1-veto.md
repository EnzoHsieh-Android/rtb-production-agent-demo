severity: clean

# 外家否決席審查(Phase 7 增量 3,r1 快照)

席位:外家否決席(原定 Codex,由 opus 頂替)。只找 blocker/major。整份 diff(235 行,三支檔)逐 hunk 讀完;對照收斂 spec「## 增量 3 設計」(S210、S211、S212)、「使用者裁定」「實務隱患」節,以及兩篇 Systems 筆記的 ★INVARIANT★。repo 未動,實驗全在 mktemp -d 臨時目錄。

### 已看:S210 字面與差異測試的比較基準

- 決策規則改動只有一行:「if item.kind == kind and item.trust_class is TrustClass.TRUSTED:」。對照 `src/rtb/analyzer/policy.py` 全檔,新鮮度判斷(`_all_fresh`)仍對每一筆證據做、不看信任標記;提案的證據引用仍是 `tuple(item.evidence_id for item in evidence)`,含廣告文字那筆。與 spec「決策規則」三條一致。
- 比較基準:每個變體都是三筆(「沒有名稱」是名稱空值、不是少一筆),三筆的編號(t1-3-state/metrics/text)、任務、讀取時間 NOW、版本 7/None/7 在各變體相同,只有廣告文字內容不同——符合 spec「比較基準要釘死」那段。測試同時比 Decision 相等與提案內容雜湊,配速偏低與正常各跑一次,且先斷言基準分別是 ProposalDecision 與 NoAction,不會兩邊都落在 NeedsFreshEvidence 而假綠。
- 截斷規則:測試的 `name[:MAX_UNTRUSTED_TEXT_LENGTH]` 與 `len(name) > MAX_UNTRUSTED_TEXT_LENGTH`,與 file: `src/rtb/analyzer/dsp_client.py:100` 至 `:101` 的 `_campaign_text` 逐字同規則;缺值/非字串記空值也一致(`:98`-`:99`)。
- 差異:測試裡廣告文字證據的內容雜湊固定 "a"*64,真實流程是對名稱內容算的(file: `src/rtb/analyzer/dsp_client.py:166`)。今天決策規則與提案內容都不讀證據的內容雜湊(提案只列編號),所以不影響結論;屬 minor,不列。
- 牙齒實測(臨時目錄):在 `decide()` 算新預算前插入「廣告文字名稱含 500% 就把 budget 乘 5」→ `1 failed, 3 passed`,S210 翻紅。

### 已看:S211 素材類別

- CATEGORIES 八類與 spec 固定清單一一對上;測試斷言覆蓋集合等於類別集合(每類至少一份、無清單外類別);超長三份皆 4096 字元且都大於 512,第三份含代理對字元。spec 寫「中文、英文、表情符號各一份」,素材恰好是 _SCENARIO(中文)、_ENGLISH、U+1F600,相符。

### 已看:S212 結構測試是否真的走到正式程式

- 第 1 部分列舉 `CampaignView` 欄位,import 自正式模組 `rtb.executor.execution`。
- 第 2 部分:`Path(__file__).resolve().parents[2] / "src" / "rtb" / "executor"` 解析到 repo 根的正式套件(tests/analyzer → tests → repo),rglob 全部檔、斷言至少 7 支(目前 7 支 .py),沒有縮小成三支。實測在 `attempt_store.py` 尾端加一個讀 `p.risk_summary` 的函式 → `1 failed, 3 passed`。
- 第 3 部分:用正式的 `CapabilitySigner.sign` 與 `DspClient.write`,只把網路層 `request_json` 換成捕捉函式。實測用字串拼接繞過原始碼掃描(寫入本文加 `proposal.to_primitives()["risk"+"_summary"]`)→ `1 failed, 3 passed`,第 3 部分接住。
- spec 字面「以屬性存取讀」:掃描抓屬性存取與 getattr 常數,與字面相符;以索引讀字典鍵繞過屬 spec 已明寫的威脅模型外(「不防刻意用字串拼屬性名稱繞過」)。
- 沒走到的面:作廢簽發 `sign_void` 與請求標頭未直接抓內容;兩者都經同一個 `_encode`/同一段 write,且屬性讀取會被第 2 部分掃到,給不出會漏的具體場景,不列。

### 已看:既有合約(★INVARIANT★)

- 任務流程領域模型的「證據只有判為新鮮才可用」:`_all_fresh` 未改,仍對全部證據做,不受信任標記條件影響。
- 同篇「提案是不可信輸入」「狀態機終點」「領域層匯入白名單」:本 diff 未碰領域層與解析、狀態機。
- 分析行程流程與檢查點的「每一步落地成新歷史列」:本 diff 未碰流程驅動與歷史表;F5 那條是 ★INVARIANT-PLANNED★,屬增量 4。
- 舊列相容:`TrustClass` 在 base 與 HEAD 都只有 TRUSTED、UNTRUSTED_TEXT 兩個成員(另一個 `unverified` 是 Freshness 的成員);S218 的舊格式列是可信的現況與指標,新條件照樣讀得到。回歸子集 `tests/analyzer tests/domain` 實跑 `452 passed`。

### 已看:設計明寫的已知存活

- 拿掉「只讀可信」條件行為不變、變異檢查會存活:spec〈回退(增量 3)〉已寫明,這是設計取捨,不算假綠。

⚠ 交編排者:無。

總結:最高為 clean,blocking 0 條。
