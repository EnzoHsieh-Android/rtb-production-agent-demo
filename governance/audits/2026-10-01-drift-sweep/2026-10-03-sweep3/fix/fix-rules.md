# 修漂移規則(2026-10-03 第三次全讀之後)

使用者已裁定照全圖譜比對結果全部修。工作目錄 /Users/enzo/rtb-lumos-update,筆記在 docs/rtb-production-agent-demo-knowledge/。

## 絕對禁止
- 不准執行任何寫入 git 的指令(add/commit/stash/reset/checkout/restore/rebase/merge/push/tag/branch/worktree)。只准 git log/show/grep/diff/blame。
- 不准改 src/、tests/、tools/、claims/、scripts/、.lumos/、CLAUDE.md。程式註解或測試名的問題記下來交回,不准改。
- 只准改分給你的那幾篇筆記(清單在你的任務說明)。發現別篇也要改:不准動,寫進結果檔的「轉給別篇」。
- 開頭欄位一律用 lumos 指令改:`python3 scripts/lumos set <節點> <欄位> <值>`(updated、status、responsibility、valid_under、revalidate_when 等)、`append`/`remove`(清單欄位)、`decision-refs` 等。正文用 Edit 工具改,不准用 sed -i 或寫腳本改筆記。
- 不准跑 lumos doctor、drift fix/ack(會寫治理帳,多人同時跑會打架);lumos lint 可以跑。不准跑全套測試。

## 上兩輪的修補出錯過,這次一定要做到
1. **每個要寫進去的事實,先自己對程式碼驗過**(讀程式、跑單一支測試、git log)。不准拿稽核員的描述直接照抄;稽核員也可能錯——驗下來不成立就不改,記成「原判有誤」並附證據。
2. **不准用位置指稱**:不准寫「開頭第 N 個重驗事件」「最後一項」「上面那條」這種會因清單改動而指錯的字樣;要寫就寫那件事本身的內容。
3. **寫了「某事已改/已裁定/已撤除/已結案」之後,搜全圖譜找講同一件事的句子**:`grep -rn "<關鍵詞>" docs/rtb-production-agent-demo-knowledge`。在你的筆記裡的一起改;在別篇的列進「轉給別篇」。
4. **一句話只講你驗過的範圍**:例如搬一支測試當守衛,要確認它真的執行到那段檢查(看條件分支走不走得到),不准一次對一批東西寫同一句結論。
5. 已完成計劃與驗收紀錄的歷史句不改寫,在句後加「(2026-10-03 更正:現況…,見…)」;講現況的系統筆記直接改句子。若同一句已有一個寫錯的更正括號,直接改正那個括號,不要再疊一個新的。
6. 回頭條件(REVISIT)條件已成立而事情沒做:不要只加註「已成立」讓它繼續掛著提醒;改成寫明現況與還沒做的事,並把 REVISIT 改綁到一個還沒發生、做得到的事件或日期(機器式條件優先:[when-file:]/[when-symbol:]/[when-test:]/[when-status:];觀測類只能用日期);若要不要做是使用者的決定,記成「要人裁」不要改。已結案筆記上的 REVISIT:事情已處理就拿掉並留一句說明,沒處理就記要人裁。
7. updated 欄位:你改過的每一篇,最後用 `lumos set <節點> updated 2026-10-03` 更新;清單列了 updated 落後的篇,也照最後一次實質改動日期(或今天,若今天有改)更新。

## 每一列的處置
- 已修:寫一句怎麼修。
- 沒修,原因只能是:要人裁 / 判不了 / 原判有誤(附證據)/ 清了會再犯(要工具機制,說明要什麼)/ 程式要改(程式註解或測試名,交協調者)。
- 已修(先前):協調者已經修好的列(任務說明會講)。

每改完一篇跑 `cd /Users/enzo/rtb-lumos-update && python3 scripts/lumos lint <節點>`,要 0 error;warning 是你新寫的才修。

## 交回
結果檔寫到 /private/tmp/claude-501/-Users-enzo-rtb-production-agent-demo/f81e262d-8877-4d56-8771-2146d9df2e96/scratchpad/sweep3/fix/<你的代號>-result.md:
- 每列一行:`筆記:行 | 形狀 | 處置 | 怎麼修或為什麼沒修 | 驗證證據`
- 「轉給別篇」:別篇哪一行要怎麼改、為什麼。
- 「程式要改」:哪支檔哪一行、建議改法。
- 各篇 lint 結果。
最後回覆只要:已修幾列、沒修幾列(按原因)、轉給別篇幾項、結果檔路徑。用繁體中文。
