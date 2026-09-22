severity: major

# 架構對齊審查:analyzer/flow.py + analyzer/task_store.py(r1-snapshot.patch)

## 檢查清單結論(先講對的地方)

- **行程互不匯入**:`src/rtb/analyzer/flow.py`、`src/rtb/analyzer/task_store.py` 只 import
  `rtb.analyzer.*`、`rtb.domain.*`、`rtb.sqlitekit`,沒有 import `rtb.dsp` 或 `rtb.executor`。
  新增的 `src/rtb/analyzer/ruff.toml` 同時禁掉 `rtb.dsp` 與 `rtb.executor` 兩個 banned-api,寫法跟
  `src/rtb/executor/ruff.toml`、`src/rtb/dsp/ruff.toml`、`src/rtb/domain/ruff.toml` 的
  `extend = "../../../pyproject.toml"` + `[lint.flake8-tidy-imports.banned-api]` 格式一致(只是
  analyzer 兩邊都要防,所以兩條都列,這點合理)。這條規則守住了,乾淨。
- **`_STEPS` 字典分派**:`_STEPS: dict[TaskState, Callable[[TaskStore, TaskRow, _Collaborators],
  _StepOutcome]]` 跟 `src/rtb/domain/proposal.py` 的
  `CHECKS: dict[str, Callable[[dict[str, Any]], bool]]`、
  `src/rtb/executor/inbox_server.py` 的 `REJECTION_STATUS = {ContentConflict: 409, ...}`
  是同一種「鍵到函式/值」對照表風格,沒有另立一套 if/elif 路由。乾淨。
- **`Accepted`/型別化例外風格**:`Accepted(replayed: bool)` 成功回傳值、`SubmitStale`/`SubmitBusy`
  例外對應暫時性與否,比照 `src/rtb/executor/inbox_store.py` 的
  `Accepted`/`InboxRejected` 之分(成功回傳值、預期失敗用型別化例外),文件裡也明講是刻意比照。乾淨。
- **圖譜節點分工**:`docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md` 的
  `about_code` 列了 `task_store.py`、`flow.py` 兩支檔,`responsibility` 跟
  `Systems/任務流程領域模型.md`(網域層純判斷)、`Systems/提案收件口.md`(執行行程)清楚分工、沒有重疊
  描述同一支檔。`analyzer/ruff.toml` 沒有家,但專案裡 `domain/ruff.toml`、`executor/ruff.toml`、
  `dsp/ruff.toml` 也都沒有被任何 Systems 節點的 `about_code` 收錄,屬於既有慣例,不算新違規。

## 問題

### 1. `Protocol` 是專案第一次出現的可替換介面寫法,取代了既有的 `Callable[...]` 慣例

severity: major
blocking: 是 全專案原本用 `Callable[[Args], Ret]` 型別提示表達「可替換的單一函式協作者」,這支檔改用 `typing.Protocol` 類別包一層 `__call__`,是同一件事的第二種做法,不是風格偏好。
引句:「class EvidenceSource(Protocol):」

說明:`flow.py` 用三個 `Protocol` 類別(`EvidenceSource`、`Decide`、`Submit`)各定義一個
`__call__` 方法來表達「可替換的協作者」,這在整個專案是第一次出現 `typing.Protocol`——
在 `src/`、`tests/` 下對 `Protocol` 的引用只有這支檔:

```
$ grep -rn "Protocol" src/ --include="*.py"
src/rtb/analyzer/flow.py:16:from typing import Protocol
```

而專案既有的「可替換單一函式協作者」全部是直接用 `Callable[[Args], Ret]` 當型別提示,不另外宣告類別:

- file: `src/rtb/executor/inbox_server.py:119` —
  `clock: Callable[[], datetime] = utc_now,`
- file: `src/rtb/executor/inbox_store.py:153` —
  `clock: Callable[[], datetime],`(`accept()` 的參數,連同 `before_commit: Callable[[], None] | None`)
- file: `src/rtb/dsp/store.py:171` —
  `clock: Callable[[], str] = _utc_now,`
- file: `src/rtb/domain/proposal.py:163` —
  `CHECKS: dict[str, Callable[[dict[str, Any]], bool]] = {`

這幾處的協作者(時鐘、提交前鉤子、欄位檢查函式)跟 `EvidenceSource`/`Decide`/`Submit` 是同一種
「單一可呼叫、可替換、需要型別化」的需求,而且三個 `Protocol` 都只定義一個 `__call__`,語意上跟
`Callable[[TaskRow], tuple[Evidence, ...]]` 這類型別別名完全等價——並沒有用到 `Protocol` 才能表達
的東西(例如多個方法、屬性、結構化子型別跨模組共用)。這是在既有慣例之外,替同一件事引入了第二種
寫法,之後的行程(或別人抄這支檔當範例)容易把 `Protocol` 誤認成專案的標準做法,造成兩套介面定義
方式並存。

建議(僅供參考,不改動審材):若需要具名型別給 `_Collaborators` 的三個欄位用,可用型別別名
(`EvidenceSource = Callable[[TaskRow], tuple[Evidence, ...]]` 等)取代 `Protocol` 類別,跟專案既有
寫法一致;若確實需要 `Protocol` 的能力,應該在圖譜裡留一筆決定「為何這裡例外」,而不是無聲引入。
