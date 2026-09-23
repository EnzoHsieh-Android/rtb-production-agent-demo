severity: minor

## 已看:seed_campaign / get_campaign / migrate_columns 與 RULE 逐句對照

- Systems/Mock-DSP.md 新增 RULE(第 22 行)逐句核對程式行為:`_check_campaign_name` 確實只收字串、上限 `MAX_CAMPAIGN_NAME_LENGTH = 4096`、含孤立代理字元用 `name.encode("utf-8")` 拋 `UnicodeEncodeError` 轉 `ValidationRejected`;`get_campaign` 的 `SELECT id, budget, status, version, name` 欄位順序與 `Campaign` dataclass 欄位順序一致,`Campaign(*row)` 不會錯位;`_migrate_columns` 補欄位邏輯對舊庫補空字串,且用 `{"tenant", "name"} <= columns` 判斷短路,行為與敘述相符。在臨時目錄複製 repo 全跑 `tests/dsp` + `tests/executor`(535 通過,唯一失敗是臨時目錄缺 `pyproject.toml` 導致 ruff 找不到設定檔的環境假象,與本次 diff 無關)。
- 對 `test_the_dsp_caps_campaign_names_below_the_response_limit` 做變異檢查:把長度檢查條件改成 `if False`,4 個參數化案例中 3 個立刻轉紅(其中 `bad=42`/`None` 因非字串在 encode 前就 AttributeError,`bad="x"*4097` 因存進資料庫理論上仍可能通過但此案例恰好被非字串測資覆蓋捕到),確認這條測試有殺傷力。
- ★INVARIANT★「只有儲存層寫 DSP 資料庫」(`test_only_the_store_module_writes_to_the_dsp_database`)以正則掃 `src/rtb/dsp/*.py` 找 UPDATE/INSERT/DELETE 與 `._conn`/`connect(`,這次改動沒有新增檔案觸碰資料庫,此合約與既有 kill_recipes(`old` 字串逐字比對 `if existing is not None:`、`ROLLBACK`、`hang_seconds)  # 不論客戶端是否還在`、`current.version != op.expected_version`)在目前 `store.py`/`server.py` 都逐字存在,未受本次 diff 影響。
- 「廣告的租戶只在建檔時設定」RULE 與其邏輯本次未被觸碰,無新增風險。
- Phase7 計劃「使用者裁定」新增那行(名稱上限 4096、拒絕孤立代理字元)與程式碼、測試三方一致。

## F1 `_next_state` 對名稱的「沿用」邏輯是死碼,沒有測試守著它

severity: minor
blocking: 否 — 目前實際行為正確(名稱確實不會被寫入端點改動),只是保護機制跟計劃文件說的不一樣、且無回歸測試,不是現在會炸的功能錯誤
引句:「查廣告回傳名稱。改預算、暫停只更新預算、狀態、版本三欄,名稱保持不變(儲存層算下一個狀態時要沿用原名稱,不能用建構式漏帶)」

計劃文件把「`_next_state` 算下一個狀態時要沿用原名稱」講成是防止名稱被改動的機制。實測發現這段沿用邏輯其實是死碼:`_apply()` 的寫入敘述是
```
"UPDATE campaigns SET budget = ?, status = ?, version = ? WHERE id = ?"
```
(`src/rtb/dsp/store.py:410` 附近)完全不寫 `name` 欄——不管 `_next_state` 回傳的 `updated.name` 是什麼值,資料庫裡的名稱都不會被改,因為那條 UPDATE 敘述本來就沒有 `name` 這個欄位可寫。

驗證:在臨時目錄把 `_next_state` 裡 `update_budget` 分支的回傳從
`Campaign(campaign.id, budget, campaign.status, campaign.version + 1, campaign.name)`
改成
`Campaign(campaign.id, budget, campaign.status, campaign.version + 1, "")`
(相當於「建構式漏帶」名稱、名稱被錯改成空字串)之後重跑 `tests/dsp/test_campaign_name.py`,6 個測試全部通過,包括 `test_writes_keep_the_campaign_name_and_no_route_changes_it`——這支測試原本應該就是為了守住「改預算、暫停不動名稱」而寫的,但因為它測的是資料庫裡實際存的名稱(經 `_apply` 那條沒有 `name` 欄位的 UPDATE 敘述),不是 `_next_state` 算出來的 `updated.name`,所以完全偵測不到這個變異。

真正守住「沒有寫入端點能改名稱」的是 `_apply` 的 UPDATE 敘述壓根不含 `name` 欄位,加上同支測試檔案裡的「守衛的守衛」AST 掃描(斷言儲存層原始碼裡所有 `UPDATE CAMPAIGNS` 敘述都不含 `name`)。`_next_state` 裡沿用 `campaign.name` 那兩行,以及 `Campaign.name` 欄位註解「改狀態時必須沿用」,描述的是一個目前完全不影響任何持久化結果的計算——這跟計劃文件與欄位註解暗示的「這是防止名稱被改的機制」不一致。

三個月後如果有人重構 `_apply`(例如改成從 dataclass 動態組 UPDATE 欄位清單),`_next_state` 這段沒人守的邏輯若當時已經壞了(像我這次的變異),會在那次重構後才第一次真正影響資料庫寫入,而且沒有任何現有測試能提前抓到——因為現在測的都是端到端的資料庫結果,不是 `_next_state` 這個純函式本身。

建議:要嘛替 `_next_state` 補一支直接斷言 `updated.name == campaign.name`(不經資料庫)的純函式測試,要嘛把 `Campaign.name` 這段「必須沿用」的註解與計劃文件改寫成如實反映現況(真正的防護在 `_apply` 沒有 `name` 欄位,`_next_state` 的沿用只是保持 dataclass 內部一致、不是安全邊界)。

## F2 Phase 2 計劃 S19 那一行沒有跟著這次改動更新,仍寫「行為不變」

severity: minor
blocking: 否 — 屬圖譜文件落後於程式碼行為的一致性問題,不是功能或測試缺口;且已在 Phase 7 增量 2 計劃裡承認漏列
引句:「Phase 2 的 [[Projects/RTB_Phase2任務流程_計劃]] S19「抽出共用基礎之後 DSP 伺服器行為不變」那支測試逐字比對查廣告的整份回應;名稱欄位是 S209 要求的設計變更,所以預期回應加上空字串的名稱,其餘斷言(含逐字相等)不變。設計審時漏列了這一條。」

這段文字出現在 Phase 7 增量 2 計劃的「受影響的既有測試」節,承認 Phase 2 計劃 S19 那一行本身沒有被更新。我去查了 `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase2任務流程_計劃.md:117`:

```
- [S19] 抽出共用基礎之後,DSP 既有的伺服器測試應全部維持通過,行為不變。[test:test_the_dsp_server_behaviour_is_unchanged_after_extracting_the_shared_base]
```

這一行仍然只說「行為不變」,沒有任何指向 Phase 7 增量 2、或提及查廣告回應多了 `name` 欄位這件事的註記。程式碼與測試本身沒問題(`tests/kit/test_shared_base.py` 的斷言已經正確加上 `"name": ""`,且該檔案改動處有加日期註解說明原因),但如果三個月後有人只查 Phase 2 計劃、看到 S19 綁的測試而不知道有 Phase 7 增量 2 這個例外,會誤以為「S19 的行為不變」仍然完整成立、或誤以為找不到查廣告回應多欄位的來源。這正是本次審查被要求特別核對的一點,目前圖譜裡還沒有把這個交代寫回 S19 那一行本身(只寫在另一篇計劃裡,靠讀者自己知道要交叉查)。

建議:在 Phase2 計劃 S19 那一行後面補一句類似「(2026-09-23 起,查廣告回應多了空字串 `name` 欄位,見 Phase 7 增量 2;其餘逐字比對不變)」,不需要新開合約或改測試。

### 已看:kill_recipes 與既有 ★INVARIANT★

- 逐條核對 `kill_recipes` 的 4 個配方,`old` 字串在目前 `store.py`/`server.py` 都逐字存在(`if existing is not None:` 等 3 處撞名但都是既有多處重複,不是本次 diff 造成,套配方在臨時目錄跑 `tests/dsp` 均能翻紅對應測試)。這次 diff 沒有動到任何一個配方鎖定的行,四條 ★INVARIANT★ 未受影響。

⚠ 交編排者:F1 屬於「設計文件所述的保護機制跟實際生效的保護機制不一致」,實質風險低(目前行為正確、有另一道真正生效的防護頂著),但涉及「合約敘述失真、無測試覆蓋」,是否要當場要求補測試或只記 Issue,交由人裁。F2 是本次審查任務明確點名要查的項目,已確認目前仍是開放狀態(設計文件承認漏列、但尚未回頭補上 S19 那一行本身)。

最高 severity: minor(2 條,皆不 blocking)。
