severity: minor

# 結論一句話

這份 diff 做的事像是「錢包裡的錢不會被廣告名稱拿走」的證明:policy.py 讀證據時多加了一道信任標記過濾,再用差異測試證明換任何攻擊文字、決策(要不要出提案、出多少預算)都不變,執行端也用結構掃描證明理由摘要不會被讀出去送出。這條證明本身站得住腳,而且 policy.py 加的過濾其實是錦上添花——evidence.py 既有的「種類與信任標記成對」型別規則本身就已經讓廣告文字進不了 CAMPAIGN_STATE/METRICS 這兩種可信證據,新加的 `trust_class is TrustClass.TRUSTED` 只是把意圖寫在讀取端第二次確認,不是新的最後一道防線(這點 diff 自己的註解也承認)。真正沒被證明覆蓋到的,是「廣告文字離開決策層之後,還會不會以別的形狀(原始文字、不是決定)流到某個會被人或下游系統讀到的地方」——這條差異測試量的是「決策」,量不到「有沒有東西被存下來、以後被誰看到」。

# 逐類檢查(已看)

- 1 不可信輸入流到危險操作:已看,無。廣告名稱進不了 `_payload()` 讀到的可信欄位,預算金額、動作類型都是型別化的結構欄位,不是自由文字組出來的。
- 2 權限與範圍:已看,無。`CapabilitySigner._encode` 簽的 claims(`tenant`/`campaign_id`/`action`/`new_budget`/`policy_version`…)全部是結構化欄位,`policy_version` 固定是常數 `"demo-pacing-v1"`,沒有攔截點讓廣告名稱混進去。
- 3 密鑰與個資:已看,無,但有一條値得記:`tests/adversarial_samples.py` 的 `secret_request` 類別字面寫出真正的金鑰環境變數名稱 `RTB_CAPABILITY_KEY`(這個名字在正式程式裡「只出現一次」是 `src/rtb/capabilitykit.py` 的既有合約,見 `tests/analyzer/test_boundaries.py` 的原始碼掃描)。diff 自己的檔頭註解已經講清楚這個掃描只掃 `src/rtb/analyzer`,不掃 `tests/`,所以不會誤觸——是已經想過的風險,不是新洞。
- 4 加密與隨機數:已看,無。這份 diff 沒有碰簽章、隨機數或雜湊演算法本身。
- 5 執行邊界:已看,部分——見 F1、F2。
- 6 行動端:不適用,這個 repo 沒有行動端程式碼。

# F1 廣告名稱裡的控制字元/雙向覆寫字元原樣寫進永久證據表,沒人證明「以後不會被人看錯」

severity: minor
blocking: 否 — 推論(找不到現有的人類可讀渲染路徑可以直接證明,只能指出這條表以後被讀的時候沒有守衛)
引句:「"control_and_bidi",  # 控制字元與雙向覆寫」

四件:
- 攻擊者控制點:廣告名稱字串,可以塞入 `\x00`、雙向覆寫字元(RLO/PDF 這類),`adversarial_samples.py` 的 `control_and_bidi` 樣本就是這個類別的樣本。
- 路徑:`src/rtb/analyzer/dsp_client.py` 的 `_campaign_text()` 只做長度截斷,不做字元過濾,原樣包進 `Evidence.payload["name"]`;`task_store.py` 的 `evidence` 表把 `payload_json` 整包原樣寫進永久、只增不改的 SQLite 表(`CREATE TABLE ... evidence (... payload_json TEXT NOT NULL ...)`)。這條路徑跟這次的信任邊界過濾完全無關——過濾只擋「決策要不要讀它」,不擋「要不要存它」,而設計本來就是「仍在證據裡」。
- 影響:這批字元目前在這個 repo 裡沒有已知的人類可讀出口(沒有 CLI、沒有審查 UI),所以現在還打不出真正的欺騙;但一旦以後有人加一個「看任務歷史 / 看證據」的工具(這類工具通常是排查與代碼審查這種高信任場景下才會被加),雙向覆寫字元可以讓終端機或簡單的字串拼接顯示出跟實際內容不同的文字,這正是這次威脅模型關心的「不可信文字影響人的判斷」——只是換了一個尚未存在的出口。
- 為何差異測試/結構測試抓不到:S210 只比對「決策的指紋(型別+內容雜湊)」是否不變,S212 只掃執行端套件裡有沒有讀 `risk_summary` 屬性,兩者都不檢查「存進資料庫的位元組本身要不要先消毒」這件事,因為那本來就不是決策或執行端讀不讀的問題。

# F2 `_payload()` 新加的信任標記過濾是第二層而非新的最後防線,「決策規則只讀可信證據」這句話目前完全靠 `evidence.py` 既有型別不變量撐著

severity: minor
blocking: 否 — 推論(不是漏洞,是證明範圍的邊界問題:寫給下一個維護者看)
引句:「決策只讀可信證據(Phase 7 增量 3):按種類找之外再加信任標記。證據型別的成對規則已讓不可信」

四件:
- 攻擊者控制點:同樣是廣告名稱,但這裡談的是「這條防線本身有幾層」而不是某個具體輸入。
- 路徑:`evidence.py` 的 `_trust_matches_kind()` 規定 `(kind is CAMPAIGN_TEXT) == (trust is UNTRUSTED_TEXT)`——換句話說 `CAMPAIGN_STATE`/`METRICS` 這兩種 `_payload()` 會用到的種類,`Evidence.__post_init__` 已經強制它們的 `trust_class` 只能是 `TRUSTED`,不可能是別的。這次 diff 在 `_payload()` 加的 `and item.trust_class is TrustClass.TRUSTED` 因此在目前程式碼路徑下永遠是恆真式,真正擋住廣告文字混進決策的是型別建構式,不是這次改的讀取端。
- 影響:如果以後有人新增第三個 `EvidenceKind`(例如某種「半可信」的欄位),或是改掉 `_trust_matches_kind` 的成對規則卻沒注意到 `_payload()` 這行過濾其實一直沒被真正測到「擋下不可信資料」這件事(S210 的樣本全部走的是既有的 `CAMPAIGN_TEXT` 種類,從沒建構過一筆「`kind=CAMPAIGN_STATE` 但 `trust_class=UNTRUSTED_TEXT`」的證據去證明過濾真的會擋),這句「決策規則只讀可信證據」的證明強度會比讀者以為的低一層。
- 為何差異測試/結構測試抓不到:S210/S211/S212 都是在「型別不變量已經生效」的前提下取樣,沒有一條測試繞過 `Evidence` 建構式去直接組一筆型別不允許但如果哪天允許了會怎樣的證據;差異測試測的是輸出穩定,不是測「這道過濾條件本身有沒有被真正的反例打到過」(即測試沒有 mutation-test 這道過濾行)。

# 沒有找到 blocking 等級的發現

三格反查(受影響測試、共改夥伴、呼叫者)都是 0,跟程式碼讀到的一致:`policy.py`、`adversarial_samples.py`、`test_trust_boundary.py` 這次改動在 base 樹上确实没有既有測試或呼叫者掛著,是全新增量,machine reverse-lookup 沒有漏抓。
