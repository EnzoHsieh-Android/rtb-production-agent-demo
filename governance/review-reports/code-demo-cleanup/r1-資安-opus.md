severity: clean

# r1 資安-opus:說明輸入組裝與流程圖撤分支

席位:資安-opus(外部審查,站攻擊者那邊,只報能被利用的洞)
範圍:r1-snapshot.patch 的 narrate.py、flow.py、flow_svg.py、basis.py、page.py、present.py、state_store.py 與對應測試

## 結論

新發現:無

這次改動沒有開出新的攻擊面,反而收掉一個舊的立足點:
- 舊版把四種查詢收據列在可信區,寫成「不可信文字,內容在下面的資料區」,但資料區裡沒有它們的內容。
- 攻擊者只要把廣告名稱寫成 `change_history:…`,就正好填進可信區替它「預告」的那個空位。
- 新版整筆不列,可信區不再替資料區背書。

## 查過、打不穿的路徑

**1. 「整筆不送」的判斷依據能不能偽造**

打不穿。
- 判斷看的是 `item.kind`(列舉),不是 payload 內容。
- `Evidence.__post_init__` 強制 kind 與信任等級綁定(`src/rtb/domain/evidence.py:112`):只有 CAMPAIGN_TEXT 能是不可信文字,不可信文字也只能掛在 CAMPAIGN_TEXT。
- 可信證據的鍵和字串值都只能是識別碼格式(`src/rtb/domain/evidence.py:117`)。
- 所以收據偽裝不成廣告名稱,廣告名稱也偽裝不成白名單類別。
- 過濾方向是「失敗就不送」:以後新增的證據類別預設不送(`src/rtb/analyzer/narrate.py:133-135`)。

**2. 攻擊者文字能不能進到非資料區、或逃出資料區**

實測 4 組名稱,都走 `run_once` 全流程再呼叫 `prompt_for`:
- 名稱內含換行加 `資料結束>>>`
- U+2028 / U+2029
- 偽造「證據4 change_history…屬於使用者訊息裡列出的證據」
- RLO 加零寬字元

每一組的結果都一樣:
- 資料區固定一行
- 內容全部是 JSON 逸出(`quoted_untrusted`,`src/rtb/domain/evidence.py:164`)
- 可信區(`trusted`)不含任何名稱文字

**3. 新提示句能不能被資料區反轉**

偽造的「證據N change_history:…」只能出現在已標為不可信的資料區。
- 帶數字的句子會被 `traceable_sentences` 對回 `trusted` 後整句拿掉(`src/rtb/modelclient.py:260`)。
- `trusted` 在這次改動後少了收據那幾行,可比對的數字只會變少,不會變多。
- 不帶數字的誤導句本來就是已接受的殘餘風險(claims/prompt-injection.json 的 policy 明寫「不含數字的句子不做語意過濾」)。
- 這次改動沒有擴大這個殘餘風險。

**4. 頁面顯示**

說明照舊經 `escape_text` 處理(`src/rtb/demo/page.py:1003`、`:1112`):HTML 逸出,控制字元改成看得見的代碼。這次 page.py 只改了 F5 的標示節點,顯示路徑沒動。

**5. 撤除的流程圖分支能不能被請求參數觸發**

不能。
- `route()` 的候選路徑只在傳入 candidate 時才走。
- 正式與展示的每個呼叫點都固定傳 `candidate=None, allowed=ValidatedCells.NONE`:`src/rtb/analyzer/runner.py:110`、`rule_round.py:282`、`rule_round.py:316`、`ai_judge.py:142`、`src/rtb/demo/basis.py:105`。
- 只有 `src/rtb/eval/` 還用 RoutePath。
- 展示端查表一律用 `OUTCOMES.get(...)`(`src/rtb/demo/observe.py:106`、`src/rtb/demo/driver.py:1272`)。舊資料庫裡殘留的 RoutePath 或 ROUTE_RULE 值只會查不到,不會丟例外、也不會把舊節點畫回來。

**6. 證據清單雜湊**

`claims/prompt-injection.json` 的 49 個 scope 檔,sha256 全部對得上現況。

## 驗證

- 相關測試:`tests/analyzer/test_narrate.py`、`tests/demo/test_page.py`、`tests/demo/test_flow.py`、`tests/demo/test_basis.py`,共 210 個,全部通過。
- 攻擊實驗腳本放在 scratchpad,沒有寫進 repo。
