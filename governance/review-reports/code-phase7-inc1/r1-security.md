severity: clean

# 審查對象

`/Users/enzo/rtb-3b/governance/review-reports/code-phase7-inc1/r1-snapshot.patch`(repo `/Users/enzo/rtb-3b`),對照設計 `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase7提示注入與信任邊界_計劃.md` 增量 1 節與合約 S200–S207、S217、S218。

站在攻擊者角度看:攻擊者能控制的是「DSP 回應本文」(含廣告名稱、以及回應裡任何欄位),目標是讓不可信文字取得可信身分、外流到決策/日誌/工具呼叫紀錄,或讓塞字變成 DoS(讓某廣告分析永遠失敗)。逐項檢查如下,沒有找到可被利用的洞。

## 1 不可信輸入流到危險操作

已看,無。具體驗證:

- 逐欄白名單在 `dsp_client._trusted()`(`src/rtb/analyzer/dsp_client.py:107-114`)用固定 dict-comprehension `{name: body[name] for name in fields}` 組出可信 payload,鍵只可能是 `STATE_FIELDS`/`METRICS_FIELDS` 這兩個常數字典的鍵——名單外欄位的**名稱**與**值**都不會進到回傳的 dict 裡,對應 S204,測試 `test_fields_outside_the_allowlist_never_reach_the_evidence` 有埋 `MARKER` 驗證。
- 例外訊息 `f"{endpoint} 的可信欄位不合格:{', '.join(bad)}"`(`dsp_client.py:112`)裡的 `bad` 只會是白名單常數鍵名,不含攻擊者送來的欄位名或值,不會把攻擊者塞的字串帶進例外/日誌(對應 python-idioms R16)。
- 廣告名稱(唯一原樣保留的不可信文字)只寫進 `CAMPAIGN_TEXT` 這一種證據、標成 `UNTRUSTED_TEXT`;`domain/evidence.py` 新增的 `_trust_matches_kind`(成對綁定)與 `_strings_fit_trust`(可信證據字串只能是 `is_id` 格式的短代號、不可信文字才准放到 512 字元的自由字串)在 dataclass 建構時強制檢查,繞不過建構式(對應 S200/S201/S202)。
- 決策端 `src/rtb/analyzer/policy.py`(本次未改動)只用 `EvidenceKind.CAMPAIGN_STATE`/`METRICS` 兩種取 payload(`_payload()` 精確比對 `kind`),`CAMPAIGN_TEXT` 從未被讀進 `budget`/`spend`/`impressions` 等決策輸入;唯一碰到全部證據(含文字)的地方是 `evidence_refs=tuple(item.evidence_id for item in evidence)`,只帶固定格式的證據編號(如 `t1-2-text`),不帶名稱內容,符合設計「仍可引用但不取權」。
- 廣告名稱塞字不會讓分析失敗:`_campaign_text()` 只做「非字串→記空值」「超長→截斷+標記」,不丟例外;送進 `_content_hash()` 的 `json.dumps(..., ensure_ascii=True, allow_nan=False)` 會把控制字元、孤立代理字元、非 ASCII 全部轉成 `\uXXXX` 逃脫序列(合法 ASCII),不會因為名稱內容讓雜湊或後續 `TaskStore` 寫入(同樣用 `ensure_ascii=True` 的 `json.dumps`,見 `task_store.py:285`)拋出例外——用推論驗證了情境題選項 d(整筆拒收)、也驗證了「控制字元/代理字元/超長字串讓分析整個失敗」這條攻擊路徑在本次改動下走不通。
- 欄位名稱本身不外流:白名單機制本身就是只認鍵名、不回傳鍵名列表(S204 已附測試)。

## 2 權限與範圍

已看,無。`_is_campaign_of()`(`dsp_client.py:38-39`)在 `STATE_FIELDS["id"]` 與 `METRICS_FIELDS["campaign_id"]` 都要求等於 `task.campaign_id`,對不上就整讀失敗(S205 涵蓋 `("state","id","c2")`、`("metrics","campaign_id","c2")`)。攻擊路徑推論:即便 DSP 被竄改回應別的廣告資料,只要編號對不上任務,`fetch()` 就丟 `DspRequestFailed`、不寫入任何證據(增量 3 的 S26 保證的「丟例外不寫半套」),攻擊者拿不到「用別的廣告資料驅動這個任務的決策」。

## 3 密鑰與個資

已看,無。本次改動沒有新增任何讀取/傳遞金鑰或憑證的程式碼;`on_call` 鉤子仍只記 `(task, endpoint, outcome, latency_ms)`,不含回應本文,廣告名稱等文字不會流進 `tool_calls` 紀錄。

## 4 加密與隨機數

已看,無。沒有新增雜湊、簽章、隨機數相關程式碼;`_content_hash()` 沿用既有 SHA-256 規則,只是套用對象換成過濾後的白名單 dict / 文字 dict,算法本身未變。

## 5 執行邊界

已看,無。沒有新增 `subprocess`、`eval`、`pickle`、動態 import 等執行面呼叫;新 import 只有標準庫 `math`(python-idioms R18 不適用,本次沒有反序列化或 shell 相關改動)。

## 6 行動端

已看,無。此改動為 Python 後端分析行程,不涉及任何行動端程式碼。

## 新依賴

已看,無。未新增任何第三方套件依賴。

# 圖譜/設計對照補記

- 本次讀了 `RTB_Phase7提示注入與信任邊界_計劃.md`「增量 1 設計」與「使用者情境題(2026-09-23)」段落,確認情境題答案 b(分開存放、可引用但不取權)與本 patch 行為一致;決策規則「只讀可信證據並用測試證明邊界」屬增量 3 範圍,本 patch(增量 1)不含,審查時已按此劃界,沒有把增量 3 才要做的事當成本次缺陷。
- 沒有發現筆記與程式碼互相矛盾之處,合約 S200–S207、S217、S218 對應的測試都在 patch 的測試檔裡實際存在(非只掛名),抽查沒有發現「宣稱測了但斷言空洞」的情況。
- `py-memory satisfied — src/rtb/analyzer/task_store.py:263` 這則表態:該行落在 `commit_step()` 內、非本次改動的既有邏輯區段,本次未新增未釋放資源或無界記憶體成長點,無反駁理由,予以接受。

# 結論

六類與新依賴皆已看過,均無可被利用的洞;patch 的白名單、成對信任標記、截斷不拒收三個機制彼此獨立生效,涵蓋了威脅模型列出的「不可信文字取得可信身分」「跨廣告決策」「塞字致分析永久失敗」三條攻擊路徑,均推導為不可行。無 finding,blocking: 否。
