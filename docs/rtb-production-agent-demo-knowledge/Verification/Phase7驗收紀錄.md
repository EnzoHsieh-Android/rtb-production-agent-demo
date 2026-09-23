---
type: verification
status: pass
date: 2026-09-23
valid_under: "Python 3.14.6 與 sqlite3 3.50.6、pytest 9.1.1、ruff 0.16.8,macOS 本機;程式版本為提交 0ef3ad1;分析行程沒有接語言模型,決策是示範規則;模擬 DSP 只有廣告名稱一種文字欄位,沒有素材文字、落地頁網址;不可信文字上限 512 字、模擬 DSP 名稱上限 4096 字是使用者裁定值"
revalidate_when: "改動 src/rtb/domain/evidence.py、src/rtb/analyzer/dsp_client.py、src/rtb/analyzer/policy.py、src/rtb/executor/inbox_server.py 的拒收路徑、或模擬 DSP 查廣告的回應時重跑全套;分析行程接上語言模型、或模擬 DSP 加素材文字等新的文字欄位時,重驗事故 F5 的差異測試與端到端並延伸到新欄位;換真的 DSP 時核對它的狀態、編號值域與名稱長度上限"
tags:
  - type/verification
  - status/pass
plan_refs:
  - "[[Projects/RTB_Phase7提示注入與信任邊界_計劃]]"
---
# Phase7驗收紀錄

驗證對象:[[Systems/分析行程流程與檢查點]]、[[Systems/任務流程領域模型]]、[[Systems/Mock-DSP]]、[[Systems/執行迴圈]]、[[Systems/提案收件口]];依據計劃 [[Projects/RTB_Phase7提示注入與信任邊界_計劃]]。

## 結論

Phase 7 的五個增量(證據型別與分析端白名單、模擬 DSP 廣告名稱欄位、決策只讀可信證據與邊界證明、事故 F5 端到端、格式不合法提案的拒收紀錄)與一個小修正(指標時間窗必須等於請求的)都完成並推送,CI 綠。事故 F5 已轉正,留在 [[Systems/分析行程流程與檢查點]](使用者裁定不搬家),措辭照實寫成「不可信的廣告文字(目前是廣告名稱;素材文字會走同一條路)」,經獨立審計同意並有四條殺傷力配方。下面的缺口與偏離如實標明,不宣稱比這更多。

## 對照交接文件 Phase 7 完成條件

- **F5 通過**:正式合約在 [[Systems/分析行程流程與檢查點]],主綁 [test:test_f5_an_injected_campaign_name_changes_nothing_end_to_end](真的模擬 DSP、分析行程、收件口與執行迴圈,十份對抗性素材各跑一整條路,DSP 恰好一次改預算 100 到 110、跟名稱正常時相同)。獨立審計卷證 governance/review-reports/rtb-phase7提示注入與信任邊界/f5-audit.md。
- **不可信資料選不了任意工具或網址、改不了政策、擴不了資源範圍、讀不到金鑰**:
  - 工具、網址、憑證、政策覆寫、指令:提案白名單拒收夾帶這些欄位 [test:test_unknown_fields_are_rejected_so_nothing_can_be_smuggled_in];端到端再確認提案只有白名單欄位、分析行程只打讀現況、讀指標、送提案三種端點。
  - 改政策、擴權(動作與金額):差異測試 [test:test_no_adversarial_campaign_name_changes_the_decision](決策與提案雜湊不變)、端到端兩支 [test:test_f5_an_injected_campaign_name_changes_nothing_end_to_end]、[test:test_f5_an_injected_name_on_a_healthy_campaign_triggers_no_write]。
  - 資源範圍:執行期由簽發器與執行前檢查綁定 [test:test_the_executor_refuses_to_sign_outside_its_current_tenant_configuration]、[test:test_each_failed_precheck_blocks_the_proposal_without_a_write];執行行程不讀、不送理由摘要 [test:test_the_execution_side_never_reads_free_text]。
  - 讀不到金鑰:[test:test_the_analyzer_can_neither_read_the_signing_key_nor_import_the_signer]。
- **格式不合法的提案被拒並記錄**:分析端(決策規則丟例外時任務轉失敗並記錯誤細節)既有;收件口在回應之前於標準錯誤寫一行只含錯誤類別的紀錄,不含攻擊者給的鍵名與請求內容 [test:test_an_invalid_proposal_is_rejected_and_logged_without_its_content](使用者裁定選 b,不碰資料庫、不拿鎖)。
- **工具回傳與提示裡的資料/指令邊界可由測試證明**:
  - 型別層:不可信文字只能配廣告文字種類 [test:test_untrusted_text_and_the_campaign_text_kind_always_come_together];可信證據的鍵與字串只能是短代號 [test:test_trusted_evidence_cannot_carry_free_text]。
  - 工具回傳的欄位白名單與大小:[test:test_fields_outside_the_allowlist_never_reach_the_evidence]、[test:test_a_malformed_trusted_field_fails_the_whole_fetch]、[test:test_an_oversized_or_odd_campaign_name_is_bounded_without_failing_the_fetch]。
  - 行為邊界:差異測試與八類對抗性素材 [test:test_the_adversarial_samples_cover_every_documented_category]。
  - 差異測試是取樣證明加回歸網,防忘記,不防刻意針對清單外寫法去改決策規則。

## 怎麼驗的(2026-09-23,提交 0ef3ad1)

- 驗收指令 `.venv/bin/python -m pytest -q`:1194 條通過;`ruff check src tests`、`mypy src` 乾淨;計劃 20 條條款全部綁了會真跑的測試,相依回歸 84 支綠。
- 審查:設計審 2 輪(卷證 governance/review-reports/rtb-phase7提示注入與信任邊界/);代碼審增量 1、2、3、4、5 與時間窗修正各自跑完(卷證 governance/review-reports/code-phase7-inc1/、code-phase7-inc2/、code-phase7-inc3/、code-phase7-inc4/、code-phase7-inc5/、code-phase7-window/)。
- 變異檢查:每道新防護都拿掉、清編譯快取後重跑對應測試,增量 1 十六道、增量 2 六道、時間窗兩道、增量 3 六道、增量 4 六道、增量 5 六道全紅;增量 3 另有一道預期存活(見下)。事故 F5 四條殺傷力配方由轉正時跑過,全部翻紅。

## 偏離與缺口

- **素材文字目前沒有**:使用者裁定模擬 DSP 只加廣告名稱;F5 措辭照實寫小,「素材文字會走同一條路」是設計意圖、沒有測試。將來加素材文字時要把差異測試與端到端延伸過去。落地頁網址不屬這個階段(會引出「系統會不會去抓網址」的新問題)。
- **沒有接語言模型**(使用者裁定):決策是示範規則,「被騙的模型」這種情境沒有測。執行閘沒有單筆變動比例上限,留給 Phase 6 護欄,計劃實務隱患已掛回頭條件。
- **拒收紀錄只到標準錯誤**:沒有持久化、告警與行程內拒收次數,歸 Phase 9 可觀測性;共用伺服器讀本文遇到連線中途斷線這類不是拒收例外的錯誤,不會留這一行紀錄(既有行為,代碼審通才席指出)。
- **工具呼叫紀錄看不出可信欄位不合格**:DSP 回應可信欄位壞掉時,兩個端點的呼叫紀錄都記成成功,查軌跡看不出這一步為什麼沒前進;使用者裁定歸 Phase 9。
- **名稱的控制字元與雙向覆寫字元原樣存進歷史表**:設計刻意不過濾不改寫;顯示給人時標明不可信文字、跟系統欄位分開,是 Phase 6 人工處置畫面的責任(代碼審資安席指出)。
- **決策規則「只讀可信」的條件目前拿掉行為不變**:證據型別的成對規則已經擋住,變異檢查預期存活;真正咬得住「名稱影響決策」的是差異測試。
- **比設計多做的兩條**(使用者事後裁定保留):可信證據的鍵也必須是短代號;指標時間窗必須等於請求的。
- **審查過程的偏離**:Codex 用量上限期間,多輪外家席由 opus 頂替或依 standard 分級缺席留痕;增量 4 的代碼審因輪次混記換了審查編號(code-phase7-f5-e2e),原編號作廢。
