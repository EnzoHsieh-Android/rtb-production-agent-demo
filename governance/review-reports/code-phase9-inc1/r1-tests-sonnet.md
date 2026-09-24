severity: major
審查完成,報告全文如下(已照 p9i1-fmt.md 的格式)。

---

severity: major

本輪對 [S600]–[S626] 的 27 條合約逐一核對綁定測試,並在 `/tmp/p9i1-review/repo`(git clone 的隔離副本,repo 根未改動)對高風險合約做拿掉關鍵一行的變異實驗:S600(事件寫入迴圈)、S612(收件側掃描逐列寫事件)、S617(維運套件邊界掃描)、S620(被取代事件欄位)、S624(from_existing 旗標)、S626(終點部分索引)、S610(唯讀交易守衛)共 7 組變異全部驗證測試會轉紅,S605/S608/S611/S615/S618 等靠真實並行連線或真實 HTTP 逾時驗證、非靠輸入本來排好或單執行緒僥倖過關。找到以下 2 類會讓測試守不住的洞。

### 1. [S617] 維運套件邊界掃描能被 `getattr` 動態派發繞過
severity: major
blocking: 是
引句:「從嚴:屬性不論是不是當場呼叫(先取方法再呼叫也算)、直接呼叫的名字(維運套件自己定義的除外)、」

觸發情境:維運套件(`src/rtb/ops/`)裡若有人寫 `getattr(store, "release")(tx, None, None, None)` 這種用字串動態派發呼叫寫入函式的寫法,而不是 `store.release(...)` 這種語法樹看得到的 `Attribute` 節點呼叫。

會出什麼錯的行為:`tests/ops/test_ops_boundaries.py` 的 `_ops_offenders()`(定義在第 57 行)只掃 `ast.Attribute`、`ast.Name`(裸呼叫/裸引用)、`ast.ImportFrom` 三種節點,不掃函式呼叫的字串參數。實測把上述一行 `getattr` 動態派發加進維運套件的臨時副本(在 `/tmp` 隔離目錄操作,未動 repo),`_ops_offenders()` 回傳空清單 `[]`——完全沒抓到這支呼叫其實在呼叫收件口的寫入函式 `release`。連測試自帶的殺傷力測試 `test_the_ops_scan_catches_a_write_call`(第 138 行,`@pytest.mark.parametrize` 六個案例在第 130–136 行,含「先取方法、之後再呼叫」這種變體)都沒有涵蓋 `getattr` 這種字串動態派發變體。也就是說 [S617] 承諾的「維運套件只准呼叫讀取函式白名單」這道邊界,實際上有一種寫法可以繞過去,且完全不會被任何測試發現——這正是規格特別點名要防的「白名單裡每一支都必須是機械判定的非寫入函式」防線的漏洞。

建議修法:在 `_ops_offenders()` 裡對 `ast.Call` 且 `func` 為 `Name(id="getattr")`(或 `hasattr`/`operator.attrgetter`)的呼叫額外判斷:若第二個參數是字串常量、且該字串屬於 `defined - READ_WHITELIST`,一併算違規;並把 `getattr(store, "release")(tx, None, None, None)` 這種案例補進 `test_the_ops_scan_catches_a_write_call` 的參數化清單,讓殺傷力測試真正覆蓋這條路徑。

file: `tests/ops/test_ops_boundaries.py:57`(`_ops_offenders` 定義)、`tests/ops/test_ops_boundaries.py:130-138`(殺傷力測試現有六案例,缺 getattr 變體)

### 2. [S625]、[S626] 規格的 `[test:]` 標記名字跟實際測試函式名不一致,機械綁定判「懸空」
severity: major
blocking: 是
引句:「def test_dsp_call_records_keep_the_dsp_error_code(dsp, tmp_path, clock):」
引句:「def test_the_last_terminal_event_of_a_proposal_uses_the_terminal_index(store, reader):」

觸發情境:規格文件裡 [S625] 寫 `[test:test_dsp_call_records_keep_the_error_code]`、[S626] 寫 `[test:test_the_last_terminal_event_lookup_uses_the_terminal_index]`,但實際落地的測試函式名分別是 `test_dsp_call_records_keep_the_dsp_error_code`(多了 `dsp_`)與 `test_the_last_terminal_event_of_a_proposal_uses_the_terminal_index`(多了 `of_a_proposal`、少了 `lookup`)。

會出什麼錯的行為:這兩支測試本身內容是紮實的(對 [S626] 做過變異實驗——拿掉 `lifecycle_events_terminal` 部分索引的建表語句,`EXPLAIN QUERY PLAN` 斷言立刻轉紅,證實真的在驗終點索引有沒有被用到,不是空殼),問題出在名字對不上。實跑 `python3 scripts/lumos spec-trace "Projects/RTB_Phase9可觀測與SLO_計劃"` 確認:27 條裡只有這兩條被判「懸空(寫錯)」,其餘 24 條(含 S622 的靠人驗證)都正常綁定。CLAUDE.md 明寫這種情況會被 pre-push 擋(「計劃寫了測試名不等於測試存在」),governance 帳本也認不到這兩條合約已經有測試守著,等同這兩條在機械稽核眼裡是「沒人管」的合約,即使程式碼與測試都已正確落地。

建議修法:二選一改到一致——要嘛把規格文件 [S625]/[S626] 的 `[test:]` 標記改成實際函式名,要嘛把兩支測試函式改名去配規格;改完重跑 `lumos spec-trace` 確認 47 條裡「懸空」數從 22(全是尚未實作的增量 2)降到剛好等於增量 2 未做的那些、增量 1 這段變成全綁。

file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md:135`([S626] 的 `[test:]` 標記)、`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md:136`([S625] 的 `[test:]` 標記)、`tests/executor/test_read_only.py:230`、`tests/executor/test_dsp_calls.py:173`
