severity: minor
逐條驗收與回歸排查完成,測試全綠(1648 個,90.38 秒)。以下是完整報告全文。

---

severity: minor

# code-phase9-inc3 第 3 輪(r3)代碼審驗收報告

範圍:`governance/review-reports/code-phase9-inc3/r3-delta.patch`(b34055f..HEAD),對照 `r2-intake.md` 折入的 3 件事(合併自資安-1/regress-1/x1-1、arch-1、x1-2 共 5 條發現)逐條讀碼驗收,並針對指定的四個回歸熱點覆核。

## 逐條驗收(讀碼,不只看測試綠)

**例外分類(資安-1/regress-1/x1-1 合併)**:已修。`src/rtb/ops/slo.py:239` 的 `evaluate()` 把原本的 `except Exception as exc` 收窄成只接 `DspUnreadable`,其餘一律不接、原樣往外丟。我讀過完整呼叫鏈確認兩件事都成立:(1)程式錯誤不再被吞——`AssertionError`/`TypeError`/`AttributeError`/`KeyError`/`sqlite3.DatabaseError` 現在會從 `evaluate()` 一路穿到 `run()`,`run()` 本身(`slo.py:269-299`)沒有任何 `except Exception` 兜底,會讓行程真的崩潰、非零離開,不再回 `EXIT_OK`;(2)「DSP 暫時讀不到」沒有漏接——`unauthorized()`(`src/rtb/ops/side_effects.py:278-292`)與 `duplicates()`(`side_effects.py:301-313`)本身各自已經把 `read_dsp_window`/`_tally_duplicates` 整段包在 `try/except DspUnreadable` 裡、回 `Tally(missing=True)`,`DspUnreadable` 在到達 `evaluate()` 這一層之前就已經被吸收,兩條目標為零的指標讀不到 DSP 一樣會被標成資料來源缺,不會變成整支評估崩潰。測試 `tests/ops/test_slo.py::test_a_programming_error_or_a_broken_database_is_not_swallowed` 用五種例外分別驗過 `evaluate()` 與 `run()` 都不吞。

**結束代碼次序(資安-1/regress-1/x1-1 合併的後續建議)**:已修,次序正確。`slo.py:44-50` 新增 `EXIT_INCOMPLETE = 8`,`run()` 在印完 JSON 之後先檢查 `any(s.missing or s.error for s in statuses)`(`slo.py:290-295`)回 8,只有這個條件不成立才檢查 `not all(s.stable for s in statuses)`(`slo.py:296-298`)回 5——也就是「缺資料/出錯」優先於「不穩定」。這個次序在語意上站得住:`end_to_end_handoff` 是唯一會 `stable=False` 的指標,跟 `unauthorized_side_effects`/`harmful_duplicates` 的 `missing` 是彼此獨立的兩件事,兩者同時成立時(DSP 連不上,同時執行端剛好有併發寫入讓端到端交給執行三輪不同)如果讓 5 蓋過 8,監控腳本會漏掉「這次稽核根本沒讀到 DSP」這個更該優先處理的狀態。測試 `tests/ops/test_slo.py::test_an_unstable_cross_database_read_is_reported` 確實構造了「DSP 讀不到 + handoff 不穩定」同時成立的情境,驗到回 8;把 `unauthorized`/`duplicates` mock 成不缺資料後同一個不穩定情境才回 5,兩段都驗到位,不是只挑其中一種組合測。8 這個數字也沒有跟 `slo.py` 自己或其他命令列入口(`ops/metrics.py`、`ops/trace.py`、`executor/approve.py`、`executor/runner.py`、`executor/replay.py`)既有用掉的代碼相撞。

**稽核金鑰專用標頭(arch-1)**:已修。`src/rtb/capabilitykit.py:29` 新增 `AUDIT_HEADER = "X-Dsp-Audit-Key"`,`src/rtb/httpclient.py:33` 在封閉列舉 `ClientHeader` 新增獨立成員 `AUDIT_KEY`,`side_effects.get_json()`(`side_effects.py:198-206`)與 `DspHandler._require_audit_key()`(`src/rtb/dsp/server.py:173-193`)都改用這個新標頭,不再共用 `X-Capability`。既有能力憑證的驗證路徑(`verified_claims`/`CAPABILITY_HEADER`,`server.py:30,241,261`)完全沒有動到。測試新增了「把稽核金鑰放進 `ClientHeader.CAPABILITY` 標頭送」的情境,驗到回 401(因為專用標頭沒收到值,等同沒帶)——標頭語意分離這件事有測試釘住,不是只有文件宣稱。

**base64url 編解碼(x1-2)**:已修,邊界正確。`capabilitykit.encode_audit_key()`/`decode_audit_key()`(`capabilitykit.py:63-71`)包一層既有的 `_b64encode`/`_b64decode`(`capabilitykit.py:80-90`),`_require_audit_key()` 解碼失敗時接 `TokenRejected` 轉 403(`server.py:189-191`),不會讓解碼例外原樣穿出去變成 500。我另外拿真的 Python 直譯器驗過 `_b64decode` 的邊界:段落長度 mod 4 == 1(例如長度 1、5、9)一律被 `binascii.Error` 擋下(`Invalid base64-encoded string: … cannot be 1 more than a multiple of 4`),不會靜默解出錯的位元組;`_SEGMENT` 正則要求整段只能是 `[A-Za-z0-9_-]+`,空字串、帶 `\r`/`\n`、帶非 ASCII 位元組的偽造標頭值一律在正則這關就被拒收,不會進到 `base64.urlsafe_b64decode`。新測試 `test_a_non_latin1_audit_key_goes_through` 用「密」重複 32 次(96 bytes、非 Latin-1)實測整段從 `read_dsp_window` 到 `sli.count` 都送得出去、讀得回來,`x1-2` 指出的 `UnicodeEncodeError` 場景不會再發生。

## 指定的四個回歸熱點

1. **evaluate 的例外分類(接、丟)**:如上,已驗證「不漏接 DSP 讀不到」「不吞程式錯誤」都成立。**唯一要指出的落差列在下面的新發現,是 minor,不影響上述兩個結論的正確性。**
2. **結束代碼 8 與 5 的次序**:已驗證,8 優先於 5,且有真的構造「兩者同時成立」的測試覆蓋,不是只測單一條件。
3. **稽核金鑰專用標頭與 base64url 邊界**:已驗證標頭分離乾淨、解碼邊界安全,沒有找到能讓非法輸入繞過或讓合法金鑰失敗的路徑。
4. **ClientHeader 加成員後既有防線**:`tests/httpclient/test_httpclient.py::test_the_closed_header_enum_contains_exactly_the_documented_members`(`tests/httpclient/test_httpclient.py:138-139`)與 `test_client_headers_are_exactly_idempotency_key_and_capability`(同檔 `:149-152`)都改成把新成員 `X-Dsp-Audit-Key` 一起寫進釘住的集合斷言裡,不是放寬成「至少包含」或拿掉比對——加了新標頭反而是「有意識地擴充」而不是「削弱」。故障注入標頭的防線(`request_json` 對每個標頭鍵 `isinstance(key, ClientHeader)` 的檢查、`test_a_header_key_that_is_not_a_clientheader_member_is_rejected`、`test_a_header_mapping_that_changes_between_reads_cannot_smuggle_an_unvalidated_header`)完全沒被這個 patch 動到,新成員一樣要通過同一道 `isinstance` 檢查才能送出,分析端(或任何呼叫端)仍然沒有辦法送出 `X-Fault` 或其他不在列舉裡的標頭。

## 新發現(minor,非本輪新回歸)

### 1. `evaluate()` 新收窄的 `except DspUnreadable` 在目前呼叫圖裡永遠接不到,`error` 欄位形同永遠不會被觸發
severity: minor
blocking: 否
引句:「except DspUnreadable as exc:  # 一條讀不到不拖垮另外五條;原因記在這一條的狀態裡」
file: `src/rtb/ops/slo.py:239`
file: `src/rtb/ops/side_effects.py:282,300-301,312-313`

觸發情境:目標為零的兩條指標(`unauthorized_side_effects`/`harmful_duplicates`)是唯一會讓計數函式走到 DSP 的路徑,而它們對應的 `unauthorized()`/`duplicates()` 本身已經各自把 `read_dsp_window`/`_tally_duplicates` 整段包進自己的 `try/except DspUnreadable`,直接回 `Tally(missing=True)`,函式簽章上完全不會讓 `DspUnreadable` 逃出去;其餘四條指標的計數函式(`safe_completion`/`unknown_reconciled_in_time`/`end_to_end_handoff`/`queue_wait`)只讀本地資料庫,程式裡也沒有任何一處會拋出 `DspUnreadable`。逐一讀過六條指標對應的計數函式後,找不到任何一條真的會讓 `DspUnreadable` 傳到 `evaluate()` 那一層的 `try` 區塊。

會出什麼錯的行為:這不是這輪引入的功能性缺陷——測試要驗這個分支時得靠 `monkeypatch` 讓假的 `counter` 直接丟 `DspUnreadable`(`tests/ops/test_slo.py::test_one_unreadable_slo_does_not_hide_the_others`),production 路徑走不到。但這代表 `evaluate()` 這一層的 `except DspUnreadable` 目前是死碼,`SloStatus.error` 欄位在真實部署裡永遠是 `None`,`run()` 裡 `incomplete` 判斷式裡的 `s.error is not None` 這一半條件也永遠不會被觸發(只有 `s.missing` 那一半在起作用)。模組說明(`slo.py:18-19`)寫「一條的資料來源讀不到…只把那一條標資料來源缺並記下原因」,讀起來像是 `evaluate()` 這一層在做隔離,但實際隔離發生在 `side_effects.py` 更下面一層,`evaluate()` 只是重複了一層目前用不到的防線。日後如果有人加第七條會呼叫 DSP、但計數函式本身沒有自己接住 `DspUnreadable` 的新指標,`evaluate()` 這層防線會第一次真的派上用場而且行為是對的——不是錯誤設計,只是現況跟模組說明字面上暗示的分工不完全一致,容易讓人以為 `error` 欄位在現行六條指標下也有機會被觀察到。

建議修法:不必改行為,把模組說明或 `evaluate()` 旁的註解改成明講「目前六條指標裡沒有一條會讓 `DspUnreadable` 傳到這一層,這是給未來新增、且沒有自己處理 DSP 例外的指標用的防線」,或者在 `SloStatus.error` 的欄位說明加一句「目前恆為 None」,避免下一個人看 JSON 輸出、找不到任何一次 `error` 不是 `None` 的樣本時懷疑是不是漏測。

## 測試

`cd /Users/enzo/rtb-p9i3 && PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest`:1648 個測試全數通過(90.38 秒),比 r2 多 6 個(`test_a_non_latin1_audit_key_goes_through`、`test_a_programming_error_or_a_broken_database_is_not_swallowed` 的 5 個參數化案例)。base64url 邊界(段落長度 mod 4 == 1 的拒收行為)另外用 `PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python3` 起一個唯讀直譯器實驗驗證,沒有寫入或修改工作樹任何檔案。
