Now I have everything needed. Compiling the final report.

---

**掃描結果:RTB_Phase10評估與Jev決策點_計劃.md**

**① 未定義的詞**

- 位置:第44、68、71行。原文引句:「只有『有候選、而且這個切片在已驗證清單上』才交給候選判斷」「正式行程的已驗證清單是空的、也沒有候選」。
  實際狀況:全文件與 `Systems/分析行程流程與檢查點.md`、`src/rtb/analyzer/`、`src/rtb/domain/` 都查不到「已驗證清單」這個名詞對應的任何既有型別、設定檔或資料結構——它是本計劃要新造的東西,但文件沒說它會是什麼形式(常數集合?設定檔?環境變數?誰在什麼時候寫入它),只說「正式行程是空的」。
  建議改法:在增量 1 或〈設計〉補一句最小定義,例如「已驗證清單是一個 `frozenset[SliceKey]`,由增量 2 的評估報告產出後手動寫入某個常數/設定;正式行程的呼叫端永遠傳空集合」,讓 [S701]/[S704] 的測試知道要測什麼型別。

**② 壞引用**

- 位置:frontmatter 第9–11行(`lands_in`)。原文引句:`lands_in: - Systems/分析行程流程與檢查點 - Systems/評估與Jev決策點`。
  實際狀況:`docs/rtb-production-agent-demo-knowledge/Systems/` 底下只有 `分析行程流程與檢查點.md`,沒有 `評估與Jev決策點.md`(`ls Systems/` 確認)。這是要新開的家,但正文完全沒提到要新開這篇、由誰負責、管哪些檔(增量 2 會新增評估集/評分/報告等新檔,依 CLAUDE.md 鐵則 5「每支檔案有家」,這些新檔要先有家才能落地)。
  建議改法:在〈設計〉或計劃結尾補一句「本計劃完成時用 `lumos new system 評估與Jev決策點 --code <評估模組路徑> --responsibility ...` 新開這篇,管增量2新增的評估集/評分/報告程式」。

**③ 範圍自相矛盾 / 與圖譜決策打架(動到核心裁定)**

- 位置:第28行(「評估對象」)對照 `Projects/RTB_Agent_Phase0架構.md` 第243行的路由表。
  原文引句:「評估對象:不評整支決策函式…只評『配速偏低時,這個廣告值不值得加預算』這一個判斷。」
  實際狀況:Phase0 架構的路由表(`RTB_Agent_Phase0架構.md` 239–246 行)把決策點分兩組——「要不要提案調整(示範用配速規則)」這一列(就是 `decide()` 裡「值不值得加」這段)標的「未來優化候選」是 **LLM**,「優化前需要的證據」是「真實業務規則與證據類型確定之後再議」;真正標「未來優化候選:Jev」、需要「Production Trace → 人工標註 → Rubric → Eval Set → 分片…」這條證據鏈的,是另外三列(證據夠不夠、下一步查什麼、繼續或停止——這些是規劃/investigation 層的窄決策,不是 `decide()` 的配速判斷)。
  Phase10 計劃選了 Phase0 路由表明確劃給 LLM 的那一列去評估 Jev,且引用的證據鏈(PRIOR-ART 那段、〈使用者裁定〉的完成條件)其實是 Phase0 表裡屬於「另外三列」的證據要求,兩邊對不上。這不是純粹的現況描述打架,是動到 Phase0 已經裁定的「哪個決策點路由到 Jev」這個核心取捨,而文件裡完全沒有提到、也沒有重新裁定或更新那張路由表。
  建議改法:要嘛在〈使用者裁定〉裡明寫「這次刻意選了 Phase0 表原本標給 LLM 的那個決策點來練 Jev 評估機制,是本次新裁定,原表那一列連帶更新」;要嘛把 Phase0 路由表也一併更新(加一欄或改『未來優化候選』),否則兩份文件對同一個決策點的路由分派互相矛盾。

**④ 機械宣稱驗語意**

- 現況四條對 `policy.py decide()` 的描述(第33–37行):
  逐句核對 `src/rtb/analyzer/policy.py:55-92`——三種結果(NeedsFreshEvidence/NoAction/ProposalDecision)、條件(新鮮度 `_all_fresh`、配速 `pacing().below(0.5)`、曝光點擊都 >0)、"不做"不帶原因(`NoAction` 是空 dataclass,`flow.py:47`)、沒有正式行程呼叫(`grep decide= src/rtb` 無結果,只有 `tests/` 底下接線)、只在測試接線、不看廣告狀態(`decide()` 從未讀 `state["status"]`)——**存在命中+語意命中,全部一致**。

- 決策當下事實清單(第36行)對 `dsp_client.py` 白名單與 `store.py`/`server.py`:
  「有」的九項(預算、狀態、版本、1小時曝光/點擊/轉換/花費/營收、證據年齡、有無不可信文字)分別對到 `STATE_FIELDS`(budget/status/version)、`METRICS_FIELDS`(impressions/clicks/conversions/spend/revenue,`dsp_client.py:67-81`)、`_all_fresh`/`check_freshness`(年齡)、`EvidenceKind.CAMPAIGN_TEXT`(不可信文字,`dsp_client.py:96-102,164-170`)。「沒有」的三項(上線天數、租戶、動作種類)——`store.py` 的 `campaigns` 表確實有 `tenant` 欄位(第37行、`tenant_of()`),但 `dsp_client.py` 的白名單完全不讀它,分析端拿不到——**語意命中,一致**;上線天數整個系統都沒有這個概念;動作種類 `ActionType` 有 `UPDATE_BUDGET`/`PAUSE_CAMPAIGN` 兩種,但 `decide()` 只會產出 `UPDATE_BUDGET`(`policy.py:84`),跟文件說法(『只有調預算一種』指的是 decide() 產出,不是 DSP 支援的動作種類)一致,但措辭容易讓人誤會 DSP 只支援一種動作——輕微語意風險,建議改成「decide() 目前只會提案調預算,DSP 另支援暫停但決策函式不會產出」。

- 信任邊界 [S210](第45行,「候選不准拿到不可信的廣告文字(信任邊界,沿用 [S210])」):
  [S210] 實際定義在 `Projects/RTB_Phase7提示注入與信任邊界_計劃.md:198`——「同一批可信證據配上沒有名稱、正常名稱或任何一份對抗性素材的廣告文字…決策規則的結果應完全相同」,綁 `tests/analyzer/test_trust_boundary.py:60 test_no_adversarial_campaign_name_changes_the_decision`。這支測試守的是**現有 `decide()`** 對廣告名稱不敏感,不是「候選函式不會收到不可信文字」這件事(候選函式現在根本不存在)。**語意命中,「沿用」用詞過寬**:S210 保證的是既有 `decide()` 的輸出穩定性,不是候選介面的輸入邊界;本計劃自己另開了 [S703] 並綁新測試 `test_the_candidate_never_sees_untrusted_campaign_text`,這才是真正守住候選不收不可信文字的合約。不算動到核心裁定,是描述不精確。
  建議改法:把「沿用 [S210]」改成「比照 [S210] 的信任邊界原則,新增 [S703] 另外守候選介面」。

- 切片鍵「小額會觸發保底 +1、逼近整數上限」(第50行)對 `decide()` 金額算式(`policy.py:80-81`):
  `new_budget = min(max(round(budget*1.1), int(budget)+1), MAX_INT)`——budget 很小時 `round(budget*1.1)` 可能等於原值,`max(...,budget+1)` 保底 +1;`min(..., MAX_INT)` 對應「逼近整數上限」——**語意命中,一致**。

- 評分標準「不值得加:廣告暫停中」與現行規則「不看狀態」(第55、63行):
  文件自己在第63行已明講「預期它在…『暫停中』這幾片的精確率很低——那是規則的缺陷,記進缺陷清單,不改標準答案遷就規則」——這是文件自己承認的落差,不是未察覺的矛盾,**核對過、一致**(文件對自己的矛盾做了正確處理)。

- `lands_in` 的 `Systems/評估與Jev決策點`:已併入②壞引用一併處理。

- Phase0 架構決策裡跟 Jev 有關那條(路由表三欄):已併入③一併處理,是本次掃描的主要命中項。

**沒命中的項目**

- 增量1「行為不變」的 [S700]、[S702] 描述與程式現況比對:一致(目前程式規則邏輯與描述相符,尚未實作不算矛盾)。
- 〈回退〉段落與〈實務隱患〉的「已排除」三項核對程式現況(不改寫入行為、不接外部 API、不可逆風險):一致。
- REVISIT 日期與格式:符合 CLAUDE.md 要求的獨立行、帶日期格式。

**命中 4 條**(①1條、②1條、③1條/含動到核心裁定、④2條,其中①②③各記1條、④記2條語意命中,合計4類命中共5個具體發現點,取類別計數為4條主要命中類別)。
