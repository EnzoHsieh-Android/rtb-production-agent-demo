severity: major
# 報告全文

severity: major

本輪鏡頭是「新測試殺傷力與改既有測試」,材料為 `governance/review-reports/code-phase9-inc1/r2-delta-tests.patch`(對應 `r2-delta-src.patch` 的 8 條第 1 輪修正)。所有變異實驗都在 `/tmp/p9i1-mut-src`(`cp -r` 出來的臨時副本,操作完已刪除)裡直接改檔跑 pytest,repo 根 `/Users/enzo/rtb-p9i1` 全程只讀,收工前用 `diff -q` 核對過六支被實驗到的原始碼檔跟 repo 裡的版本完全一致、`git status` 也確認沒有新增改動。

---

## 逐條修正的殺傷力(拿掉關鍵一行會不會紅)

8 條修正全部做了變異實驗,結果都會紅(新測試守得住):

| 修正 | 拿掉的關鍵行 | 結果 |
|---|---|---|
| dsp-1/x1-4/inbox-1(撞鎖蓋掉 DSP 結果) | `execution.py` `flush_calls` 的 `except InboxBusy: return` | `test_a_busy_database_defers_the_call_record_without_losing_the_dsp_result` 紅:`InboxBusy` 直接往外炸穿 `process_one` |
| arch-1(拿掉模組層級範圍變數,改顯式傳回呼) | `dsp_client.py` 把 `on_call` 改回選填(`= None`) | `test_every_dsp_call_leaves_one_classified_call_record` 紅:簽章斷言抓到 `on_call` 不再是必填關鍵字參數 |
| spec-1(release 事件記 coalesce 後的值) | `inbox_store.py` `release` 改回直接記傳入的 `failure` | `test_a_lease_release_event_records_the_last_failure_left_on_the_row` 紅:第二筆事件的 `reason` 從 `table_full` 變 `None` |
| x1-1(自己的租約沒到期不算被接手) | `inbox_store.py` `take_over` 拿掉 `before[1] != owner or ...` 判斷 | `test_the_same_owner_taking_over_its_live_lease_writes_no_reclaim_event` 紅:自己續做也多寫出 `reclaimed` 事件 |
| x1-2(關聯鍵帶內容雜湊) | `trace.py` `_revision_keys` 改回只用 `(task, revision)` 去重 | `test_a_reused_task_and_revision_with_new_content_is_traced_separately` 與既有的 `test_a_shared_operation_key_is_attributed_to_the_revision_that_created_it` 都紅:兩份不同內容的提案被併成一份 |
| x1-3(解析失敗帶狀態碼) | `httpclient.py` `_parsed` 改回丟裸 `ValueError` | `test_every_dsp_call_leaves_one_classified_call_record` 與 `test_an_error_status_with_an_unreadable_body_keeps_its_status` 都紅:狀態碼從 `500`/`200` 掉回 `None` |
| tests-1(維運套件禁用 getattr 類) | `test_ops_boundaries.py` 把 `_dynamic_lookups` 改成永遠回 `[]` | `test_the_ops_scan_catches_a_write_call` 六個新案例全紅 |
| tests-2([S625]/[S626] 改名配規格) | 純改名對規格 `[test:]` 標記,無邏輯可變異;已用 `lumos spec-trace` 的判準核對過(依 r1-tests-sonnet.md 記錄的重現方式)確認改名後兩條不再懸空 |

## [S618]「200 加非 JSON 本文」預期從空狀態碼改成 200

引句:「((200, b"not json", 0.0), R.UNREADABLE, 200, True),」

這個改動合理,沒有掩蓋回歸。原本 `request_json` 對任何本文解析失敗(無論狀態碼)都丟裸 `ValueError`,在 `dsp_client._send` 裡被歸類成「沒拿到回應」,狀態碼一律記 `None`——這正是 x1-3 修的洞(狀態碼遺失)。現在 `httpclient.py` 新增的 `UnreadableResponse` 帶著原始狀態碼往外丟,`_send` 改成 `except UnreadableResponse as exc: failure = _by_status(exc.status, False)`,所以「200 + 本文讀不懂」這個案例現在**應該**記到狀態碼 `200`(分類仍是 `R.UNREADABLE`,只是狀態碼從遺失變成保留)。我用上面 x1-3 的變異實驗直接驗證過:把 `_parsed` 改回丟裸 `ValueError`,這條新預期(狀態碼 `200`)立刻由綠轉紅——代表這個斷言確實在守著修正後的行為,不是隨手放寬。

## 7 支既有測試為了必填 on_call 補參數

逐一核對 `fakes.py` + 6 支既有測試檔(`test_f4_end_to_end.py`、`test_trust_boundary.py`、`test_execution_e2e.py`、`test_queue.py`、`test_runner.py`、`test_version_conflict.py`,共 7 支因新增必填 `on_call` 關鍵字參數而動的檔案)裡每一處補參數的寫法:

- `fakes.py` 的 `FakeDsp`、`test_queue.py` 的 `steal_first`、`test_runner.py` 的 `operation_record` 包裝、`test_f4_end_to_end.py`/`test_version_conflict.py` 的 `write` 包裝,全部是**原樣轉發**收到的 `on_call` 給底層(`on_call=on_call`),沒有吞掉或換成空回呼。
- `test_execution_e2e.py` 頂端新增 `IGNORE = lambda _call: None`,用在 8 處呼叫上;但逐一核對過這些測試本來就只斷言用戶端的**回傳值**(`.version_after`、`.state`、查回的版本號等),從來沒有在 Phase 9 之前或之後斷言過呼叫紀錄——呼叫紀錄的驗證責任本來就在 `test_dsp_calls.py`,不在這幾支。用空回呼沒有讓任何原本該驗的紀錄消失。
- `test_trust_boundary.py` 唯一一處 `on_call=lambda _call: None` 同理:那支測試驗的是「執行端送出的請求本文裡不含分析端的自由文字標記」,跟呼叫紀錄無關。

結論:這 7 支檔案的補參數都是純粹的型別相容性修補,沒有順手放寬斷言或用空回呼掩蓋原本該驗的呼叫紀錄。

## 維運套件 getattr 類禁用掃描的殘留洞

### 1. `eval`/`exec` 字串動態派發完全繞過新加的 DYNAMIC_LOOKUPS 掃描
severity: major
blocking: 是
引句:「DYNAMIC_LOOKUPS = frozenset({"getattr", "attrgetter", "methodcaller", "__getattribute__", "vars",」

觸發情境:有人在 `src/rtb/ops/` 底下寫類似 `eval("s.release")(tx, None, None, None)` 這種用字串拼出方法名、再靠 `eval`(或 `exec`)動態執行取代 `getattr` 的寫法——這是跟 `getattr(s, 'rel' + 'ease')`(這輪新加的殺傷力案例之一)同一等級、同樣容易在寫維運腳本時隨手用到的寫法,不是刻意繞過掃描器的攻擊手法。

會出什麼錯的行為:我在 `/tmp` 用未經任何修改的真實掃描邏輯(`tests/ops/test_ops_boundaries.py` 的 `_ops_offenders`/`_dynamic_lookups`,程式碼跟 repo 現況一致)對一份加了上述 `eval` 那行的 `src/rtb/ops/trace.py` 副本掃描,`_ops_offenders()` 回傳 `[]`——完全沒偵測到這其實在動態呼叫收件表的寫入函式 `release`。原因是 `_dynamic_lookups`(`tests/ops/test_ops_boundaries.py:91`)只認 `ast.Name`/`ast.Attribute`/`ast.alias` 節點裡名字等於 `DYNAMIC_LOOKUPS`(`tests/ops/test_ops_boundaries.py:87-88`)這幾個字串;`eval`/`exec`/`compile` 完全不在清單裡,而它們本身也不是 `InboxStore`/`attempt_store` 定義的名字,所以連掃描器原本就有的「呼叫已知寫入函式名字」那條路也接不到。新的殺傷力測試 `test_the_ops_scan_catches_a_write_call`(`tests/ops/test_ops_boundaries.py:147-170`)六個新案例涵蓋了 `getattr`、字串拼接、`operator.attrgetter`、`methodcaller`、`__getattribute__`、`vars`,唯獨沒有 `eval`/`exec` 這條路,所以這個洞連測試自己都不會發現。也就是說這輪修正把「維運套件本身也用不到,一律不准」這句承諾實際上只做到「靠屬性/名字查表能認出的動態取屬性」,字串執行這條更常見的動態派發手法沒被堵上。

建議修法:把 `eval`、`exec`、`compile` 加進 `DYNAMIC_LOOKUPS`(維運套件本來就不該出現字串執行,加了不會誤傷正常寫法),並在 `test_the_ops_scan_catches_a_write_call` 的參數化清單裡補一個 `eval("s.rel" + "ease")(tx, None, None, None)` 之類的案例,讓殺傷力測試真正覆蓋這條路徑。

file: `tests/ops/test_ops_boundaries.py:87-97`(`DYNAMIC_LOOKUPS` 定義與 `_dynamic_lookups`)、`tests/ops/test_ops_boundaries.py:147-170`(殺傷力測試現有案例,缺 `eval`/`exec` 變體)
