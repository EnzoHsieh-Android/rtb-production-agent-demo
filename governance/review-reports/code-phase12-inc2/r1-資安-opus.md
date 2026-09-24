severity: major

**1. 重跑過的情境在下一次「全部跑一次」時仍被換上去,頁面把舊結果當成這次的結果,「現在進度」也指到舊的那一次**
severity: major
blocking: 是
引句:「swaps = dict(self.reruns)」
引句:「current=other.current if running else shown.current,」

file: `src/rtb/demo/server.py:161`、`src/rtb/demo/server.py:169`、`src/rtb/demo/server.py:141`

問題有兩個:
- **舊重跑結果在新全跑時沒清掉。** `self.reruns` 只在 `_finished` 裡、而且要等全部跑一次跑完才清空。所以新一次全部跑一次還在跑的時候,`state()` 仍把之前重跑過的情境換成舊的那一次。這是假綠。
- **現在進度會指錯情境。** `dict.update` 遇到已經存在的鍵,不會改變它的位置。所以「這次正在重跑的情境」不一定排在 swaps 最後。迴圈最後一輪用的是 `other.current`,結果頁面顯示的「現在進度」、自動重讀網址、主畫面焦點,都指向另一個舊重跑的情境。

重現:在 /tmp 複本,把 HOME 導到暫存目錄,用測試的假情境跑。
- `exp1.py`:先全部跑一次,再重跑 F3、F5,然後開新的全部跑一次並讓它卡住。這時 F1 顯示「正在執行」,F2/F4/F6/F7 顯示「尚未開始」,**F3、F5 卻顯示「結果符合預期」**,來源是舊的重跑編號(…41710cca、…91ec93ab)。
- `exp2.py`:同樣先重跑 F3、F5,各寫一筆 `x_done` 判斷,模擬真驅動會留下目前節點。然後再重跑 F3,並讓它停在 `a_collect`。頁面的 aside 顯示「現在進度 情境 5 / 共 7・F5 … ▶ 完成…」,meta refresh 是 `url=/?scenario=F5…`,焦點也跑到 F5。

建議:
- `start(full=True)` 一開始就讓 `state()` 不套用 `self.reruns`:running 而且 current.full 時,swaps 設成空的。
- 正在跑的那一次最後才套用,或者 `current` 直接取 `self.current.demo_id` 那一次,不要取迴圈的最後一輪。
- 補兩個測試:「全跑中不顯示舊重跑」「重跑中的現在進度一定指向正在跑的那一次」。

**2. 展示跑完以後,主頁仍一直顯示「現在進度 ▶ … 上一個判斷(剛剛)」,秒數持續累加**
severity: major
blocking: 是
引句:「if current is not None and current.scenario in {c.value for c in ScenarioCode}:」
引句:「current = _render_current_progress(state) if interactive else ""」

file: `src/rtb/demo/present.py:211`、`src/rtb/demo/page.py:305`、`src/rtb/demo/page.py:446`

`build_demo_state` 不看 `running`:只要 `current_node` 表裡有列就給 `CurrentStep`。頁面的 `_render_current_progress` 只看 `state.current` 有沒有值,也不看 `running`。真驅動每寫一筆判斷都會更新 `current_node`,所以每次真跑結束後,主頁都會留著一個會跳動的「現在進度」條,「已經在這一步 N 秒」一直變大,還標「剛剛」。這會讓人以為展示還在跑。

重現:`exp4.py` 跑完全部跑一次以後,寫一筆三小時前的 `x_done` 判斷。`state().running` 是 False,但頁面顯示「現在進度 情境 7 / 共 7・F7 … ▶ 完成…・已經在這一步 10800 秒 上一個判斷（剛剛）…」。

建議:`running` 為 False 時 `current` 給 None,或者頁面只在 `state.running` 為真時畫這一條。補一個「跑完不顯示現在進度」的測試。

**3. GET /report 內嵌的 `<style>` 被自家內容安全政策擋掉,從伺服器開報告時整頁沒有樣式**
severity: minor
blocking: 否
引句:「head = _render_head(refresh_url=None, inline_styles=True)」

file: `src/rtb/demo/page.py:255`、`src/rtb/demo/page.py:40`、`src/rtb/demo/server.py:292`

另存到磁碟的報告要把 CSS 內嵌,這沒問題。但同一份 HTML 也從 `/report` 送出,送出時帶著 `style-src 'self'`,沒有 `'unsafe-inline'`、沒有 hash。瀏覽器會照規範擋掉 `<style>`。結果流程圖的 SVG 全部變成預設的黑色填色,狀態顏色、淡色與虛線圖例都讀不出來。

重現:`exp3.py` 對 `/report` 發 GET,回應裡確實有 `<style>`,CSP 標頭是 `default-src 'none'; … style-src 'self'; …`。

建議:`/report` 路由改用外連 `/static/demo.css`(另存檔仍內嵌),或者只對這一段 CSS 加 `'sha256-…'`。

**4. 報告保留清理會刪掉同目錄裡名稱剛好符合樣式的其他檔;目錄權限只在新建時才設**
severity: minor
blocking: 否
引句:「old = sorted(self.reports.glob(f"demo-{kind}-*.html"))」
引句:「self.reports.mkdir(parents=True, exist_ok=True, mode=0o700)」

file: `src/rtb/demo/server.py:178-184`

- **會誤刪別人的檔。** 清理只比對 glob,不比對 `new_demo_id` 的格式(`\d{8}-\d{6}-[0-9a-f]{8}`)。使用者放在 `~/.rtb/demo-reports/` 裡、名字像 `demo-full-1999-notes.html` 的檔,排序在前面,會被當成舊報告刪掉。測試本身也用 `demo-full-2000xx-old.html` 當舊檔,確認了這種寬鬆比對的行為。
- **權限沒有保證。** `mode=0o700` 只對新建的末層目錄生效,中間的 `~/.rtb` 照 umask 建成 0755;目錄已經存在時也不檢查權限。報告檔本身是 0644(`exp3.py` 實測,目錄是新建的 0700)。報告裡沒有金鑰,也沒有表單隨機值(實測沒有),所以只算 minor。
- **不會跟著符號連結寫到別處。** 檔名是伺服器產生的隨機值,無法預先放符號連結。

建議:清理時用完整的格式 regex 篩,並跳過符號連結;已經存在的目錄如果權限寬於 0700 就警告或收緊。

**5. 核可簽發跟驅動程式「逾時清掉確認」之間沒有互斥;連按兩次會簽出兩張**
severity: minor
blocking: 否
引句:「pending = reader.confirmation(run.demo_id)」
引句:「run, code, request = self._confirmed(form)」

file: `src/rtb/demo/server.py:188-214`、`src/rtb/demo/driver.py:958-972`

`_confirmed` 讀到確認請求,之後才 `_still_waiting`、`_sign`。這段期間驅動程式可能剛好逾時,已經 `clear_confirmation` 並判成「沒有人確認」。`_still_waiting` 只重讀收件口狀態,不重讀確認表還在不在,而決策到期比驅動等人的上限多 60 秒。所以在很窄的時間窗裡,仍可能在判成沒有人確認之後才簽入一張核可,執行端(關閉前)就可能寫入。
另外,重複送出同一張表單,在執行端取件前都會通過,等於簽兩張。簽的是同一筆、同一關,不會簽錯對象。

重現:讀程式碼推論,沒有做成可重現的競態。

建議:簽之前在同一個臨界區再確認一次 `reader.confirmation(run.demo_id)` 仍然存在;簽完寫一個標記(或讓驅動程式清掉確認),拒絕第二次送出。

**6. 回應沒有 Cache-Control: no-store / X-Content-Type-Options / Referrer-Policy**
severity: minor
blocking: 否
引句:「state, form_token=service.token, refresh_tick=service.next_tick(),」

file: `src/rtb/httpkit.py:351-367`(不在審材內)

帶表單隨機值的主頁與確認頁可以進瀏覽器的磁碟快取。隨機值每次啟動才換,本機情境下風險低。頁面沒有外部連結,所以隨機值不會經由 Referer 外洩(只放在 POST 本文,不進網址)。

建議:HTML 伺服器的回應一律加 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`。

**查過、沒有問題的地方**(`exp3.py` 實測):
- **跨站請求**:以下全部被拒——
  - 不帶 Origin 與 Sec-Fetch-Site:403
  - same-site 或其他連接埠的 Origin:403
  - cross-site:403
  - `Origin: null`:403
  - text/plain 表單:415
  - 隨機值錯誤:403
  - 只有同源而且隨機值正確才會 303。
- **主機標頭**:evil 主機、沒帶連接埠、`127.0.0.1:port.evil` 都回 400;只收 `127.0.0.1:port` 與 `localhost:port`,不分大小寫。
- **點擊劫持**:`frame-ancestors 'none'` 每個回應都帶,錯誤頁也帶。
- **注入**:page.py 所有動態文字、屬性、SVG `<text>`/`<title>` 都經過 `escape_text`;連結與 id 只用列舉代碼。不可見字元與雙向控制字元會轉成可見的 `〔U+XXXX〕`。F5 的對抗名稱完全不上頁面,只有固定的說明文字。
- **錯誤頁**:只有固定錯誤代碼,traceback 只寫到 stderr。
- **核可綁定**:欄位都取自伺服器存下的請求,不取自表單;摘要綁定展示編號、內容雜湊與數字;換展示或舊表單重放時,展示編號對不上會回 403。
- **金鑰**:只在 `Run` 的記憶體裡,repr 已遮蔽;不進展示狀態庫、頁面、報告或驗證器環境。

**實驗與清理**:
- 複本 /tmp/secopus-p12i2,HOME=/tmp/secopus-p12i2/exp/home,報告目錄導到暫存目錄。另跑了 tests/demo/test_server.py、test_page.py、test_present.py,全部通過。
- `ls ~/.rtb` 前後一樣:都只有空目錄 `drwxr-xr-x 17:29`,也沒有 demo-reports。
- 我用的伺服器都是行程內執行緒,已經 shutdown。`ps` 看到的 rtb 行程,工作目錄都是其他席位的(/tmp/p12i2-srv、/tmp/p12i2-page、/tmp/p12i2-t4、/tmp/audit-h-copy*),沒有我起的。

**看過的檔**(涵蓋 r1-snapshot.patch 全部改動檔):
- src/rtb/demo/server.py、page.py、present.py、state_store.py、state.py、flow.py、basis.py、observe.py、driver.py(diff 加 F7 確認段)、static/demo.css
- src/rtb/analyzer/task_store.py
- tests/demo/test_server.py、test_page.py(安全相關測試)、sample_data.py、test_basis.py、test_driver.py、test_flow.py、test_observe.py、test_present.py、test_state_store.py(以 diff 掃過)
- claims/aggregate-blast-radius.json、concurrency.json、permission-guardrail.json、prompt-injection.json
- docs/…/Systems/一鍵展示.md、Systems/展示頁面.md、Issues/執行端寫入前再確認沒記讀到的平台版本.md
- 審材外參照:src/rtb/httpkit.py、src/rtb/demo/keys.py、src/rtb/executor/inbox_store.py(add_approval)

6 條,blocking 2
