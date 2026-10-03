# 全圖譜比對規則(2026-10-03,第三次全讀)

你是唯讀稽核員。工作目錄 /Users/enzo/rtb-lumos-update(主線最新版)。筆記在 docs/rtb-production-agent-demo-knowledge/,程式在 src/、tests/、tools/、claims/、recordings/。

## 絕對禁止
- 不准修改任何檔案(不准用 Edit/Write,不准用 sed -i、重新導向寫進 repo)。只准讀。結果只寫到你自己的結果檔。
- 不准執行任何寫入 git 的指令(add/commit/stash/reset/checkout/restore/rebase/merge/push/tag/branch/worktree)。只准 git log/show/grep/diff/blame。
- 不准跑 lumos doctor、lumos drift fix/ack、lumos set/append 等會寫檔或寫治理帳的指令。`python3 scripts/lumos search/show/context/decisions` 唯讀可以用。
- 不准跑全套測試;需要確認行為時讀程式碼,最多跑單一支測試(用 ../rtb-production-agent-demo/.venv/bin/python -m pytest -q <檔>::<名>)。

## 要做什麼
逐篇、逐句讀分給你的筆記(開頭欄位與正文都讀),每一句講「現況」「數量」「名稱」「誰呼叫誰」「有沒有某東西」「條件成立沒」的,都去程式碼或同篇/他篇對照。找跟現況對不上的句子(漂移)。

特別要驗:
1. **這兩天(2026-10-02、10-03)補上的更正括號與註記本身對不對**——格式像「(2026-10-02 更正:…)」「(2026-10-03 …)」「已被取代:…」「撤除後條款原掛的綁定移到這裡…」。這些是上兩輪清理加的,可能寫錯(說錯現況、指錯檔、數錯數量、自相矛盾)。
2. 已完成計劃與驗收紀錄裡的歷史句:有日期或寫明當時的,不算漂移;沒標時間、用現在式講已經不成立的事,才算。
3. 回頭條件(REVISIT、RETIRE-IF、revalidate_when、valid_under):條件是否已成立卻沒處理、是否永遠不會成立。
4. 開頭欄位:status、lands_in、about_code、responsibility、verified_by、updated 跟內文與程式對不對得上。
5. [test:] 綁定:測試存在,而且真的在測句子講的事(不是只名字對得上)。
6. 程式碼裡的註解或測試名若跟現況不符,也記下(形狀 C1)。

不算漂移(不要報):
- 句子後面已有正確的更正括號或撤除橫幅,而且更正內容是對的。
- 刻意保留的歷史紀錄,且明確標了日期或「當時」。
- 純粹措辭可以更好,但沒有說錯事實。

## 形狀代碼(沿用上一輪,見 governance/audits/2026-10-01-drift-sweep/findings.md 第 9–37 行)
K1 R1 D1 H1 H2 H3 T1 S1 S3 E1 P1 P2 V1 V2 V3 U1 A1 A2 W1 M1 M2 M3 F1 G1 C1;新形狀自己取名並說明。另加:
- X9:這兩天補的更正/註記本身寫錯。

## 誤導程度
- 高:照著做會改錯程式、做錯決定或以為有守衛其實沒有。
- 中:會讓人誤解現況,但查一下就會發現。
- 低:小數字、措辭、欄位落後。

## 交回
結果檔寫到 /private/tmp/claude-501/-Users-enzo-rtb-production-agent-demo/f81e262d-8877-4d56-8771-2146d9df2e96/scratchpad/sweep3/<你的代號>.md,格式:

每筆一行:`筆記:行 | 形狀 | 誤導(高/中/低) | 一句現象 | 證據(檔案:行、指令、或同篇第幾行) | 建議改法(一句)`

最後附:
- 每篇讀完的確認(篇名:讀完/沒讀完,沒讀完說原因)。
- 判不了的(程式碼答不了的)另列,說明為什麼。
- 你覺得是工具該抓卻沒抓的(lint/doctor/drift scan 應該能機械發現的)另列。

最後回覆只要:找到幾筆(高/中/低各幾)、其中 X9(更正本身寫錯)幾筆、判不了幾筆、結果檔路徑。用繁體中文。寧可多查少報:沒有證據的不要報。
