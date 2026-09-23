preflight-4: ran

# Phase 7 計劃第 1 輪前掃紀錄(2026-09-23)

派了一個 sonnet 代理,掃整份計劃的四項:
- ① 未定義的詞:1 條。狀態清單寫成中文,程式裡是英文字串。
- ② 壞引用:1 條。「見要使用者決定的事」指向一節不存在的段落。其餘連結、合約編號、既有測試名稱、規格章節都核對過,都存在。
- ③ 範圍自相矛盾:無。
- ④ 機械宣稱驗語意:15 句逐句開檔核對,都屬實。另有 1 句「由 Phase 3 既有合約守著」代理沒查透,編排者自己核對後發現措辭不準。另有 1 條提醒(對抗性素材只能放測試目錄)補成明文。

沒有動到使用者裁定的情境題答案,也沒有動增量排法。

| 項 | 修改前 | 修改後 |
|---|---|---|
| ① 狀態白名單 | 狀態(已知清單:投放中、已暫停) | 狀態(已知清單:DSP 實際回傳的英文字串 active、paused)。依據:src/rtb/dsp/store.py 的建檔預設值,以及暫停後寫回的值 |
| ② 死連結 | 增量 2、增量 3 的「不做的事」寫「見要使用者決定的事」,但全篇沒有這一節 | 新增「## 要使用者決定的事」一節,列出七項:接模型、文字欄位、截斷上限、F5 不搬家、拒收紀錄三選一、單筆比例上限、兩筆改三筆 |
| ④ F5 轉正的範圍限制 | 執行期範圍限制那一半由 Phase 3 既有合約守著 | 改成由 Phase 3 既有的規則與測試守著:寫入能力憑證筆記裡是規則加防回歸測試,沒有登記成正式合約。編排者讀了 Systems/寫入能力憑證,確認那裡沒有 ★INVARIANT★ 行 |
| ④ 素材位置 | 放在測試共用的一支素材模組 | 補明素材只能放在測試目錄。tests/analyzer/test_boundaries.py 的原始碼掃描只掃 src/rtb/analyzer,素材搬進正式程式目錄會撞上它 |

代理沒查透、留給設計審的點:
- 沒有實跑測試。「會紅」的結論都是讀程式碼推出來的。
- 「歷史表那支檔是 3b 正在改的」是從 Phase 4 計劃的增量描述推出來的,沒有逐字對到檔名。

# 第 1 輪設計審收貨與重現紀錄(2026-09-23)

## 收貨
- 七席收齊才動計劃:s1 信任邊界、s2 差異測試、s3 遷移相容、s4 F5 轉正、s5 可測性、sarch 架構對齊、x1 外家 Codex(codex exec --sandbox read-only,沒有撞到用量限額,不用頂替)。
- 存檔時編排者動過的格式(不改證據、不改等級):sarch 的引句原本換行寫在「引句:」下一行,改成同一行;s4、s5、s3、s2、s1 的非 finding 段落標題拿掉編號、敘述條目刪掉重複的說明句,引句與佐證行號沒動;s3 報告裡一個簡體字「处理」改成「處理」;各席報告尾端的總結句保留。
- report-normalize:七份都已是正規化格式。quote-check:七份全數錨定。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| s1-1 | PYTHONPATH=src python -c 建一筆可信證據,內容 budget=10**400 → 丟出 OverflowError int too large to convert to float | HIT,採信,折入 |
| s3-1 | 讀 src/rtb/analyzer/task_store.py:196-207,讀回時用 Evidence(...) 重建 | HIT,採信,折入 |
| s4-2 | 讀 docs/.../Systems/寫入能力憑證.md 沒有 ★INVARIANT★ 行;tests/executor/test_capability_signer.py:68 與 tests/executor/test_execution.py:163 存在 | HIT,採信,折入 |
| s4-3 | 讀 /Users/enzo/harness/lumos-toolchain/scripts/lumos:3721 PLANNED_RE 只認 ★INVARIANT-PLANNED★,:10962 _guard_planned_line 用它找行,:11147 cmd_guard_abandon 呼叫同一支 | HIT,採信,折入 |
| s5-1 | 讀 src/rtb/domain/proposal.py:41-56 為沒有覆寫字串化的 frozen dataclass | HIT,採信;判準另想:使用者裁定管的是「拿來判斷」,字串化進日誌不是判斷,所以折入方式是把 S212 措辭縮到它真正守得住的範圍、日誌風險寫進實務隱患,不是加掃描 |
| s5-3 | 讀 src/rtb/analyzer/policy.py:84 證據引用列入全部證據編號、src/rtb/domain/proposal.py:294-306 雜湊含證據引用 | HIT,採信,折入(與 s2-2 同一件事) |
| x1-1 | 讀 src/rtb/httpclient.py:106 超過 64 KB 丟 ValueError、src/rtb/analyzer/flow.py:157 蒐證例外一律留原地重試 | HIT,採信;判準另想:不放寬回應上限,改由模擬 DSP 設名稱上限 4096 字元(真實 DSP 都有),超過歸「DSP 壞了」 |

## 處置
- 折入 16:s1-1、s1-2、s1-3、s2-1、s2-2、s3-1、s3-2、s4-2、s4-3、s4-4、s5-1、s5-2、s5-3、s5-5、s5-6、x1-1。
- 放行 3(都是 minor):
  - s4-1「3b 正在改」用錯時態:編排者告知 Phase 4 增量 3b 正由另一個會談進行中,「正在改」屬實。
  - s5-4:席位自註「此條只為跟 finding 1 區隔,不另計問題」,不是缺陷。
  - sarch-1「已截斷」旗標是新做法:截斷的是之後要給人或模型讀的不可信文字,讀者必須知道它不完整;既有的靜默截斷(錯誤細節、解析錯誤鍵名)是內部診斷字串,情境不同,不構成第二套做法。
- 重現不到而列入駁回的:無。
