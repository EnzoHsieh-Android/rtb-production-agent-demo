severity: minor

# r2 架構對齊席 (tarch)

範圍:只判修復差異與專案既有做法是否一致、有無第二種做法或跨層直呼。已讀 python-idioms 存在(`/Users/enzo/.claude/skills/python-idioms/SKILL.md`),本輪無對應條款觸發。

## 逐項結論(無 finding 者)

- PITFALL 引用的 8 個測試名稱逐一 grep,全部存在:
  - `test_when_mypy_itself_cannot_run_...`、`test_lumos_passes_temporary_extracted_copies_...`、`test_errors_without_a_column_number_...`、`test_a_nonzero_exit_with_output_nobody_can_parse_...` 在 `tests/tools/test_mypy_sarif.py`
  - `test_every_configured_analyzer_is_executed_by_the_ci_workflow`、`test_the_ci_workflow_runs_on_every_push_...`、`test_every_lumos_lint_command_can_actually_run_...` 在 `tests/test_static_wiring.py`
  - `test_even_hostile_python_objects_...` 在 `tests/domain/test_proposal.py`
- Mock-DSP.md 新段落所述 `_next_state` 用 `_is_plain_int` 收窄,與 `src/rtb/dsp/store.py:159-163` 相符;`dict[str, object]` 與 `object` 標註屬實。
- 任務流程領域模型.md 把「343 條測試仍全過」改成「既有測試全部通過」,不再寫死數字,如實。
- 測試檔內放自寫 CI 剖析器:tests/tools 用 importlib 載入 `tools/mypy_sarif.py`(該檔是被交付的執行檔),而 wiring 測試的剖析器只有測試自己用,不是交付物,留在測試檔內不算第二種做法;約 40 行,不算過度工程化(理由已記在缺口清單第 6 條)。

## Finding

### 1
severity: minor
blocking: 否 屬過時敘述,不是引入第二種做法。

- 位置:`docs/rtb-production-agent-demo-knowledge/Systems/靜態檢查閘.md:26`(正文首段,本次 diff 未動到這一行)
- 問題:首段仍寫「推送與 pull request 都會跑 ruff、mypy 與 pytest」,但本次 diff 把 ci.yml 的 `pull_request:` 移除,且同篇新增的缺口寫「CI 只在 push 觸發,不開 pull request 觸發」。同一篇內互相矛盾,依 CLAUDE.md 第 3 條會讓下個 session 讀到錯的現況。
- 走到哪:讀正文首段(說 PR 也跑)再讀缺口清單(說 PR 不跑);`grep -n "pull_request" .github/workflows/ci.yml` 無輸出,確認程式現況是只跑 push。
- 引句:「+  push:」「-  pull_request:」(diff 中 ci.yml 的變更,見 r2-snapshot.patch 第 10-11 行)
- 佐證:file: `docs/rtb-production-agent-demo-knowledge/Systems/靜態檢查閘.md:26`

### 2
severity: minor
blocking: 否 缺 REVISIT 只影響筆記紀律,不影響程式行為。

- 位置:`docs/rtb-production-agent-demo-knowledge/Systems/靜態檢查閘.md` 已知缺口的三條:
  - 「同一次推送裡 mypy 與 ruff 各被跑三遍」
  - 「CI 只在 push 觸發,不開 pull request 觸發」
  - 「接線檢查對 CI 的剖析是自寫的簡易版」
- 問題:這三條都承認了風險或局限,卻沒有獨立成行的 `REVISIT:日期`。CLAUDE.md 鐵則 4 要求承認風險必附回頭條件;其餘缺口(2026-09-25、2026-10-05、2026-10-22)都有。第二條只寫「若之後改用 PR 流程要補上」,是純散文,沒人會回頭;第三條的自寫剖析器沒有撤換條件。
- 走到哪:`grep -n REVISIT` 只列出 5 行,對應 5 條缺口,上述 3 條下方都沒有。已有的 5 個日期(距今 2026-09-22 為 3 天到 1 個月)本身合理,無問題。
- 引句:「CI 只在 push 觸發,不開 pull request 觸發,避免同一個分支雙跑;若之後改用 PR 流程,要補上。」
- 佐證:file: `docs/rtb-production-agent-demo-knowledge/Systems/靜態檢查閘.md:38-40`

### 3
severity: minor
blocking: 否 是耦合點未記錄與一處可收斂的重複,沒有跨層直呼。

- 位置:`tools/mypy_sarif.py` 的 `select_paths`、`LUMOS_TEMP_PREFIX`、`configured_directories`
- 問題:
  1. 轉換器現在同時知道 lumos 內部的臨時目錄命名(`.lumos/lintbase-*/base|head/`)與 pyproject 的 `[tool.mypy].files`。前者在程式內有註解,PITFALL 也記了;後者(範圍來源是 pyproject,以及為何要自己過濾:mypy 收到明確檔案參數時會忽略 `files` 設定,且測試不做嚴格檢查)在筆記正文與摘要都沒有一句說明。`--only-configured-paths` 這個旗標在筆記中完全沒出現。lumos 若改臨時目錄命名,只有 PITFALL 那條測試(用寫死的樣例路徑)會知道,不是 lumos 真實行為的契約檢查,筆記也未標這是耦合點。
  2. 同檔內有兩種「把路徑相對化」的寫法:`_relative_uri` 用 `contextlib.suppress(ValueError)`,`select_paths` 用 `try/except ValueError: continue`,語意也不同(前者保留絕對路徑,後者丟棄)。屬同檔內第二種寫法,可抽成一個 `_project_relative`,非風格偏好而是重複邏輯,但影響小。
- 走到哪:輸入 `tools/mypy_sarif.py --only-configured-paths tests/test_x.py` 走 `main` 的 `select_paths(mypy_args, configured_directories())`,pyproject 的 `files = ["src","tools"]` 決定結果;該路徑的設計理由只在 `--help` 字串裡,不在圖譜。
- 引句:「LUMOS_TEMP_PREFIX = re.compile(r"^\.lumos/lintbase-[^/]+/(?:base|head)/")」
- 佐證:file: `tools/mypy_sarif.py`(`_relative_uri` 與 `select_paths` 相鄰);file: `pyproject.toml:41`

## 總結
最嚴重等級為 minor,blocking 條數 0。
