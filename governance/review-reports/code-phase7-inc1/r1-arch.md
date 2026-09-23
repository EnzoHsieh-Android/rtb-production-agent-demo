severity: major

# 架構對齊審查:code-phase7-inc1 r1-snapshot

## 一、分層與依賴方向

`dsp_client.py` 新匯入 `rtb.domain._checks` 的 `is_id`/`is_plain_int`/`is_plain_number`,以及
`rtb.domain.evidence` 的 `MAX_UNTRUSTED_TEXT_LENGTH`,方向是分析端(`rtb.analyzer`)依賴領域層
(`rtb.domain`)——跟同層鄰居 `src/rtb/analyzer/policy.py:25` 的既有寫法
`from rtb.domain.proposal import MAX_INT, ActionType, Proposal` 一致,也跟
`src/rtb/domain/_checks.py:5` 的檔頭說明「DSP(rtb.dsp)是外部系統的模擬器,刻意不依賴這裡,
所以它自己另有一份」吻合:DSP 模擬器(`src/rtb/dsp/store.py:129` 自己的 `is_plain_int`)本來就該
自成一份,分析端的 DSP 用戶端則理所當然共用領域層那份。這一問沒有跨層直呼,方向對。

## 二、命名與錯誤處理

- 白名單容器：`STATE_FIELDS: dict[str, Check]`、`METRICS_FIELDS: dict[str, Check]`(`Check = Callable[[Any, TaskRow], bool]`)在「欄位 → 檢查函式」這個大結構上跟
  `src/rtb/domain/proposal.py` 的 `CHECKS: dict[str, Callable[[dict[str, Any]], bool]]` 相同,
  但函式簽章不同:提案那份檢查函式吃整包 `raw` 字典自己挑欄位讀,這裡改成吃「單一欄位值 + TaskRow」。
  差異是為了讓 `_is_campaign_of` 能比對 `task.campaign_id`,結構性理由站得住,判定 minor。
- 可信欄位壞掉沿用既有的 `DspRequestFailed`(檔頭已把 docstring 改成「或回應裡的可信欄位壞掉」),
  沒有另開新例外類別,跟鄰居「用既有例外分類、不亂開新類別」的慣例(`inbox_client.py` 的
  `SubmitRejectedPermanently` 等既有分類)一致。
- `MAX_DB_INT = 2 ** 63 - 1` 是**重複定義**專案已有的常數:`src/rtb/domain/proposal.py:29`
  已經有同值的 `MAX_INT = 2**63 - 1`,而且同層鄰居 `policy.py` 就是直接 `import MAX_INT` 重用,
  沒有自己另開一份。`dsp_client.py` 這裡的註解甚至寫「模擬 DSP 與提案共用的資料庫整數上限」,
  等於承認跟提案那份是同一件事,卻沒有 import 既有符號,而是重新宣告了一個同值不同名的常數——
  之後兩處各改各的,上限就會不知不覺分岔。
- 證據建構錯誤訊息:既有 `_get()` 的錯誤字串是 `f"{path} 回 {status}:{body.get('error', ...)}"`
  這種「主體 回/的 狀態:細節」句式;新的 `f"{endpoint} 的可信欄位不合格:{', '.join(bad)}"`
  沿用同一種「主體 的 X 不合格:細節」語氣,風格一致,不列為不對齊。

## 三、第二種做法

沒有發現引入專案原本沒有的整體做法(白名單、TrustClass 綁 EvidenceKind、逐欄檢查都是延伸既有
模式),但 `MAX_DB_INT` 是重複定義已有的整數上限常數(見上),符合「之後兩份各改各的」的 major 判準。

---

## F1

severity: major

blocking: 是 — 重複定義專案已有的常數(`proposal.py` 的 `MAX_INT`),註解自承跟提案共用同一個
上限卻沒有 import 既有符號,之後兩處會各改各的。

引句:「MAX_DB_INT = 2 ** 63 - 1  # 模擬 DSP 與提案共用的資料庫整數上限」

## F2

severity: minor

blocking: 否 — 結構(欄位→檢查函式的白名單字典)跟 `proposal.py` 的 `CHECKS` 一致,只是函式
簽章因為需要 `TaskRow` 做欄位比對(如廣告編號要等於任務的 `campaign_id`)而多一個參數,是有
理由的變化,不是另立一套做法。

引句:「Check = Callable[[Any, TaskRow], bool]」

---

不對齊共 2 條,其中 major 1 條。
