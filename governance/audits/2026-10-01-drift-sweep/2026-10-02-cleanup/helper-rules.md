# 清漂移的共同規則(每個助手都要照做)

背景:rtb 知識圖譜(/Users/enzo/rtb-lumos-update/docs/rtb-production-agent-demo-knowledge/)裡有些句子跟程式碼現況或同篇其他段落對不上(「漂移」)。工具鏈會談做了逐筆對照報告,你負責清其中分給你的列。程式碼是現況的依據(/Users/enzo/rtb-lumos-update/src、tests),先讀程式碼再改筆記。

## 絕對禁止
- 不准執行任何會寫入 git 的指令:git add / commit / stash / reset / checkout / restore / rebase / merge / push / tag / branch -D / worktree。只准 git log、git show、git grep、git diff 這類唯讀指令。
- 只能用 Edit 工具改筆記;不准改 src/、tests/、scripts/、.lumos/、CLAUDE.md。
- 不准改分給別人的筆記(下面會列你的筆記範圍)。
- 開頭欄位(frontmatter 的 status、updated、revalidate_when 等)不要手改;需要改的列成「要人裁」回報。
- 不准用 --no-verify,不准跑全套測試。

## 每一列怎麼處理
1. 打開筆記那一行,再看程式碼,確認報告說的還成立(報告行號可能偏移幾行,用內容找)。
2. 決定處置:
   - **已完成的計劃或驗證紀錄裡的歷史敘述**(當時為真,後來改了):不要改寫歷史。在那句後面加一個簡短更正括號,格式:`(YYYY-MM-DD 更正:現況是…,見 [[Systems/某篇]] 或 `檔案路徑`)`,日期用 2026-10-02。一句話講清楚現況,別抄一大段程式。
   - **現況句(Systems 筆記、或明說「目前/現在/只有」的句子)**:直接把句子改成現況;數量句可加 `[count:路徑::名稱=N]`(只有程式裡真有那個常數、而且 N 是它的元素數時才加)。
   - **回頭條件、撤除條件、待辦寫成散文且條件已成立**:在旁邊註明已成立與結果,例如 `(2026-10-02:條件已成立——…;本條不再適用)`。條件還沒成立但寫成散文:改寫成獨立一行 `REVISIT:[when-file:路徑]` / `[when-symbol:路徑::名稱]` / `[when-test:測試名]` / `[when-status:Projects/名稱=done]` 加 `[by:YYYY-MM-DD]` 加一句待辦;觀測類只能寫 `REVISIT:YYYY-MM-DD 待辦`。寫之前確認條件現在**沒有**成立(成立了推送會被擋)。
   - **裁定被翻案(D1)**:在舊裁定句後加 `(已被取代:誰 何時 改裁成…,見 [[節點]])`。
   - **合約行(★INVARIANT★ 或 [S###] 條款)**:句子本身只能加更正括號,不要改條款文字,也不要動 [test:] 綁定(除非綁定的測試名確實不存在,那就改成 `[test-gone:名稱@提交]`,提交用 `git log --all --format=%h -S"def 名稱(" -- tests | head -1`)。
3. 改不了的不要硬改,記下原因類別,只能從這五種挑:
   - 要人裁(要使用者決定,例如狀態要不要改、偏離算不算違規)
   - 判不了(程式碼答不了,例如 CI 行為、人工紀錄)
   - 清了會再犯(要工具加機制才守得住;說明要什麼機制)
   - 工具有錯(lumos 的 lint/檢查擋錯或漏抓;附錯誤原文)
   - 原判有誤(報告判錯,句子其實沒漂移;附證據)
4. 每改完一篇筆記跑:`cd /Users/enzo/rtb-lumos-update && python3 scripts/lumos lint <節點,例如 Projects/RTB_Phase9可觀測與SLO_計劃>`,要 0 error。warning 是你新寫的才修。

## 交回什麼
寫一份結果檔到 /private/tmp/claude-501/-Users-enzo-rtb-production-agent-demo/f81e262d-8877-4d56-8771-2146d9df2e96/scratchpad/drift-round/<你的代號>.md,每列一行:
`筆記:行 | 形狀 | 處置(已修/沒修) | 怎麼修的一句話,或沒修的原因類別+一句理由 | 證據(檔案:行或指令)`
最後附:新發現的漂移形狀(報告沒列的)、lumos 檢查的誤報或漏報例子、各篇 lint 結果。
最後的回覆只要給:已修幾列、沒修幾列(按原因類別)、結果檔路徑。用繁體中文。
