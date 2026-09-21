severity: major

# r3 架構對齊席(arch3)

總評:拆行程本身站得住。handoff 第 5 節允許「因 write permission boundary 而獨立」的執行隔離,d9 也寫了具體風險(分析端處理不可信文字、持有模型金鑰)與同行程為何不夠(令牌與記憶體同處)。決策四欄齊全,Phase 1 沒有多開節點。問題出在家規接電和論證的精確度。

## Finding 1:d9 新增的限制,REVISIT 沒有獨立成行,doctor 不會唸

severity: major
blocking: 是 判準:家規第 4 條要求帶日期的回頭條件寫成獨立一行,寫成行內散文等於沒接電,新增的承認限制附不上有效的回頭條件。

- 問題:「作業系統帳號隔離」這條限制,REVISIT 接在同一行句尾,不是獨立一行。lumos 的回訪掃描只認「去掉行首空白與列表前綴後以 `REVISIT:` 開頭」的行,行內出現的不算,所以這條 2026-11-21 的回頭永遠不會到期提醒。同段 `REVISIT:2026-11-21 Phase 6 開審前…` 那條核准者身分的限制也是同樣寫法。SQLite 單寫入者那條則寫對了(獨立一行)。
- 引句:「金鑰隔離只靠「只傳給該持有的行程」。REVISIT:2026-11-21 Phase 3 設計審時決定要不要引入不同帳號或容器。」
- 佐證:file: `/Users/enzo/rtb-production-agent-demo/scripts/lumos:1889-1906`(行判準:以 REVISIT: 開頭才算);file: `/Users/enzo/rtb-production-agent-demo/CLAUDE.md:56`(「獨立一行…緊鄰原句」)。
- 建議:把兩條 REVISIT 各拆成緊鄰原句下一行。

## Finding 2:d9 沒進「決策」WHY 清單,也沒有出處

severity: minor
blocking: 否 判準:四欄與內容都在,只是摘要層與判斷紀錄漏登,不影響設計正確性。

- 問題:「決策」節的 WHY 行只列到 d8。d9 是本輪最大的架構變更,卻沒有帶出處的 WHY 行。「使用者判斷紀錄」節也沒記下 r3 使用者裁定拆行程這件事(審計紀錄只說「使用者裁定」)。
- 引句:「WHY: 程式要乾淨、不寫成義大利麵,並接 ruff。出處:對話 2026-09-21,見 d7、d8。」
- 佐證:file: `/Users/enzo/rtb-production-agent-demo/CLAUDE.md:44`(WHY 需附出處);審材第 269 行只有「使用者裁定拆成兩個行程(決策 d9)」。

## Finding 3:d9 的「可測的事實」比實際保證強,且沒對應 handoff 第 5 節的準則

severity: minor
blocking: 否 判準:限制已在正文坦承並列入已知限制,只是決策理由的措辭過頭,屬論證精確度。

- 問題一:d9 說拆行程能讓「假設分析端已被攻破,它手上仍沒有寫入能力」成為可測事實。但兩個行程同一個作業系統使用者,分析行程照樣能以讀寫模式打開執行端的 SQLite 檔、也讀得到環境。「對方只讀」是約定,不是強制;d2 否決行程內類別時用的正是「隔離靠自律」這個理由。實際可測的只有「分析行程啟動環境不含簽章金鑰」,建議 d9 的 why_chosen 收窄成這句。
- 問題二:d9 沒有點名符合 handoff 第 5 節哪一項準則(credential boundary、trust boundary)。正文第 142 行有引第 5 節,決策本身沒有。
- 問題三:d9 的風險前提是分析端持有模型金鑰,但正文自己說 LLM 到較後段才引入,所以 Phase 2 到 5 分析端沒有這個風險,邊界要早付成本(兩行程、兩段佇列、跨檔讀提案、事件依 trace_id 串接)。這不算與 18.5 牴觸(使用者已裁定、第 5 節也允許),但決策該寫一句「為什麼現在就拆而不是等 LLM 進來」。
- 引句:「讓寫入相關金鑰不在分析端記憶體裡成為可測的事實(假設分析端已被攻破,它手上仍沒有寫入能力)」
- 佐證:審材「已知限制」節自承同一作業系統使用者;file: `/Users/enzo/Downloads/RTB_PRODUCTION_AGENT_DEMO_HANDOFF.md:159-168`(第 5 節四項準則與「記錄具體風險、為何不用新 Agent 不夠」);同檔 `:794-803` 的 18.5「能用單一 runtime 清楚隔離就不多拆 Agent」。

## 已讀,無 finding 的項目

- 決策 d9 四欄(context、why_chosen、decided、valid)齊全,另有替代方案與取捨。
- lands_in 仍是 Systems/Mock-DSP 與 Systems/確定性指標計算,與 Phase 1 範圍一致;拆行程只影響 Phase 2 起的 agent 端,Phase 1 不必多開節點。
- 新增的事件入口(Phase 3 設計審、Phase 7 注入測試)有明確指向。

最嚴重等級為 major,blocking 共 1 條。
