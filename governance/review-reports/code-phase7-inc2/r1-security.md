severity: minor

# 審查對象

`/Users/enzo/rtb-3b/governance/review-reports/code-phase7-inc2/r1-snapshot.patch`(repo `/Users/enzo/rtb-3b`)。
本輪只改 `src/rtb/dsp/store.py`(模擬 DSP 加 `campaigns.name` 欄位、`_check_campaign_name` 驗證、`seed_campaign`/`get_campaign` 帶名稱)與對應測試(`tests/dsp/test_campaign_name.py`、`tests/kit/test_shared_base.py`)。分析端的白名單/截斷(`src/rtb/analyzer/dsp_client.py`)是上一個提交做的,本輪沒有再碰。

## 1 不可信輸入流到危險操作

已看,無。

- **SQL 注入**:`seed_campaign` 的 INSERT 與 `get_campaign` 的 SELECT 都是參數化(`?` 佔位),名稱值只透過 `sqlite3` 的 bind 參數帶入,沒有任何字串拼接把 `name` 塞進 SQL 文字。新增的 `ALTER TABLE campaigns ADD COLUMN name TEXT NOT NULL DEFAULT ''` 是常數字面值,不含使用者輸入(既有的 `tenant` 欄位那行才用 f-string,但插的是常數 `DEFAULT_TENANT`,不是本次改動、也非使用者輸入)。
- **錯誤訊息/日誌洩漏名稱內容**:`_check_campaign_name` 的兩個 `ValidationRejected` 訊息都是固定字串(「廣告名稱必須是最多 N 字元的字串」「廣告名稱含寫不進資料庫的字元」),不回顯 `name` 原文。這支專案的 handler(`httpkit.py`)也不印每個請求(`log_message` 是空實作),沒有把名稱寫進伺服端日誌的路徑。
- **控制字元/假 JSON 破壞回應格式**:回應一律經 `json.dumps(payload).encode()`(`httpkit.py: reply`),預設 `ensure_ascii=True`,任何控制字元、引號、反斜線都會被逃脫成合法 JSON 逃脫序列,名稱塞不出「看起來像多一個欄位」的假 JSON。`_check_campaign_name` 另外用 `name.encode("utf-8")` 擋孤立代理字元(`"ok\ud800"` 這種),SQLite 只收合法 UTF-8,擋在寫入前,不會讓儲存層例外洩漏堆疊或壞資料。

## 2 權限與範圍

已看,無。

- 這次 diff 沒有新增或修改任何 HTTP 路由;`seed_campaign` 只在測試與內部 `main()` 的建庫流程呼叫,`ROUTES`(`server.py`)裡的三個 POST 端點(`update_budget`、`pause_campaign`、`void_operation`)都沒有碰 `name` 欄位。`_next_state` 改狀態時原樣沿用 `campaign.name`,不會被 `update_budget`/`pause_campaign` 的請求體覆寫(`check_body_fields` 會拒絕請求體帶未預期欄位,`name` 不在 `update_budget`/`pause_campaign` 允許欄位裡,見 `dsp/capability.py`——本次沒改,行為沿用)。
- `tenant_of`、`check_scope` 的租戶/範圍判斷都讀 `tenant` 欄位,新加的 `name` 沒有進任何授權判斷路徑,不會被拿來混淆租戶或版本比對。
- 執行側(analyzer)讀 DSP 現況走 `_trusted()` 白名單(`STATE_FIELDS` 只有 `id/budget/status/version`),名稱不在白名單裡,不會被當成可信欄位混進去;名稱另外走 `_campaign_text()` 標成 `TrustClass.UNTRUSTED_TEXT`——這是上一個提交做的,本輪只是把 `name` 從 DSP 端真的存起來、讓上一輪的白名單有東西可測,設計與程式碼一致。

## 3 密鑰與個資

已看,無。名稱只是一個一般文字欄位,沒有密鑰、也沒有新增任何寫入 log/audit 的路徑會把它跟使用者個資關聯起來。

## 4 加密與隨機數

已看,無。本次改動不涉及任何加密或亂數生成。

## 5 執行邊界

已看,無。沒有新的 subprocess、反序列化、動態載入,`name` 全程只是一個 `str`,不會被 eval/exec 或當成路徑/指令片段。

## 6 行動端

已看,無。這是純後端(mock DSP + 儲存層)改動,不涉及行動端。

## 新依賴

已看,無。`tests/dsp/test_campaign_name.py` 用的都是標準庫(`ast`、`inspect`、`json`、`sqlite3`、`threading`、`urllib.request`)與專案既有模組。

---

## F1 名稱上限與 64KB 回應上限之間只有約 16KB 緩衝,長期可能被壓縮

severity: minor
blocking: 否 — 縱深防禦:目前有測試證明「最壞情況」(全部字元都要代理對逃脫)仍在上限內且留有餘裕,不是可直接利用的洞,是設計裕度問題(屬提示裡點名要報的「塞超長名稱讓這個廣告的分析永遠失敗」這條可用性路徑)。
引句:「最壞情況(每字都要代理對、回應用 ASCII 逃脫時每字 12 位元組)4096 字約 48 KB,仍低於分析端 64 KB 的回應上限,名稱塞不爆回應」

四件:
- 誰:能寫入模擬 DSP 資料(目前僅測試/建庫用的 `seed_campaign`,production 尚未接出建檔端點——見下方「推論」註記)的一方。
- 哪個入口:`CampaignStore.seed_campaign(name=...)`,經 `_check_campaign_name` 檢查後寫入 `campaigns.name`;之後任何人打 `GET /campaigns/{id}` 都會把名稱整包吐回。
- 送什麼:4096 個需要代理對逃脫的字元(例如大量表情符號),即 `MAX_CAMPAIGN_NAME_LENGTH` 上限、每字元在 JSON `ensure_ascii=True` 逃脫後占滿 12 位元組。
- 拿到什麼:單一 `GET /campaigns/{id}` 回應本身確實仍 < 64 KB(`tests/dsp/test_campaign_name.py::test_the_dsp_caps_campaign_names_below_the_response_limit` 已經拿真實 HTTP 回應驗證,不是紙上算術),`src/rtb/httpclient.py` 的 `MAX_RESPONSE_BYTES` 不會被單一這個回應打穿;但這 48KB/64KB 只留約 16KB 給其他欄位或未來擴充。若之後同一批 diff 或後續變更在同一個回應裡再加別的不可信文字欄位(例如描述、備註),或把上限往上調,原本「不會塞爆」的前提會在沒人重算的情況下悄悄失效,屆時 `request_json` 會丟 `ValueError`(超過 `MAX_RESPONSE_BYTES`),連鎖到 `DspRequestFailed`,讓這筆廣告的分析持續失敗(可用性,不是資料外洩或越權)。

備註(推論,非本次 diff 直接證據):目前 repo 內只有測試與 `store.py` 自己的 `main()`(建庫,不接受外部名稱)呼叫 `seed_campaign`,沒有找到任何 HTTP 寫入端點讓外部呼叫者設定 `name`(`ROUTES` 的三個 POST 端點都不接受 `name` 欄位,`check_body_fields` 會擋)。所以「攻擊者建檔時寫入超長名稱」這條路徑,在本次 diff 範圍內還沒有實際對外入口——如果生產環境的建檔管線在別的、這次沒審到的程式碼裡,才需要重新確認那裡是否也套用了同一個 `MAX_CAMPAIGN_NAME_LENGTH` 上限。

---

## 筆記/圖譜對照

- `/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase7提示注入與信任邊界_計劃.md` 增量 2 節描述的設計(名稱只在建檔時設定、沒有寫入端點能改、原樣保存+截斷不拒收)與本次 diff 的程式行為一致,沒發現落差。
- 技術棧 skill(`~/.claude/skills/python-idioms/SKILL.md`)R15 邊界驗證:`_check_campaign_name` 型別檢查、長度上限、UTF-8 可編碼性檢查三項齊全,符合。R16 秘密不進 log:本次沒有新增任何 log 呼叫,不適用。R18 反序列化與 shell:本次無涉。
- LUMOS-IMPACT 給的三格(受影響測試/共改夥伴/呼叫者)皆空,machine反查沒找到 base 樹既有相依——與本次實際檢查結果一致:`seed_campaign` 在 base 樹之外沒有其他呼叫者,新增測試檔本身就是唯一使用方,沒有找到與此矛盾之處。

## 結論

沒有找到可直接利用的注入、越權、密鑰外洩或資料完整性漏洞;唯一值得留意的是名稱長度上限與回應上限之間的安全餘裕只有約 16KB,屬縱深防禦層級的觀察,不擋這次合併。
