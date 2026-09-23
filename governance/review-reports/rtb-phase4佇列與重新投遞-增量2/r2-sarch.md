severity: major

### 1. 新當機測試把重啟子行程綁在真實時鐘,卻沿用既有 DSP 測試夾具那個凍結在固定日期的假時鐘,重簽的憑證會被 DSP 判過期而全數拒收

severity: major
blocking: 是 照設計字面實作,S118–S125 全部八條合約測試在重啟後的重送/驗證步驟都會撞上 DSP 端「簽發時間在未來」或「憑證已過期」而失敗,達不到設計自己列的預期終態,F2、F3 的轉正證據站不住
引句:「測試行程裡的 DSP 伺服器時鐘同步往後撥(它本來就能注入時鐘)」

說明:設計要求「重啟的子行程時鐘往後撥 5 分鐘(真實時間加 5 分鐘)」,而「DSP 伺服器時鐘同步往後撥」。但既有可重用的 DSP 時鐘注入夾具(`tests/executor/test_execution_e2e.py` 的 `PlannedDsp`)是 `clock=lambda: clock().timestamp() + self.offset`,這裡的 `clock` 是測試行程共用的假時鐘 `Clock`,凍結在固定日期,只靠測試呼叫 `clock.advance()` 才會動(見 `tests/executor/conftest.py:14` `NOW = datetime(2026, 9, 22, 12, 5, tzinfo=UTC)`)。子行程是獨立作業系統行程,無法共享這個 Python 物件,只能自己組一個以「真實牆鐘時間」為底的時鐘(如 `datetime.now(UTC) + timedelta(minutes=5)`)。這兩個時鐘的底(一個是凍結在 2026-09-22 12:05 的假時鐘、一個是跑測試當下的真實牆鐘)彼此沒有關係,差距通常是數小時到一天以上——而 DSP 對憑證簽發時間的容忍只有 30 秒(`MAX_SKEW_SECONDS`)、有效期上限 300 秒(`MAX_LIFETIME_SECONDS`)。設計文字只講「同步往後撥」(暗示只是把既有的 offset 再加 300),沒有點出「必須把 DSP 時鐘的底也換成真實牆鐘,不能沿用既有假時鐘夾具」這個關鍵差異;若實作者照抄既有的 `PlannedDsp` 建構方式(PRIOR-ART 段落明講「照抄」),簽出的憑證會立刻被 DSP 判為過期/未來時間拒收,S118–S125 沒有一條能走到「DSP 共收到 1 次寫入、最終已驗證/已擋下」這些預期終態。

file: `src/rtb/executor/capability_signer.py:24`
file: `src/rtb/dsp/capability.py:36-37`
file: `src/rtb/dsp/server.py:250`
file: `tests/executor/test_execution_e2e.py:33-41`
file: `tests/executor/conftest.py:14`

### 2. 「照抄既有猝死測試的做法」換掉 Executor 私有方法會直接撞上 FrozenInstanceError,既有前例（CampaignStore）不是 frozen dataclass

severity: major
blocking: 是 至少 S119、S122、S124(還有依同一手法的 S118 之「呼叫 DSP 之前」變體)需要換掉 `Executor._write`／`Executor._verify` 這類實例方法;若照抄既有做法在實例上賦值,會在建置死亡子行程時就丟例外,測試根本跑不起來,不是「行為錯」而是「連跑都跑不了」
引句:「選擇照抄既有猝死測試的做法(它也是換掉私有方法)」

說明:設計明白宣告這批當機測試的做法是「照抄」`tests/dsp/test_store.py` 的 CRASH_CHILD 模式——把要監控的私有方法在**實例**上換成 `die` 函式(`store._record_idempotency = die`,見 `tests/dsp/test_store.py:400`)。這個手法成立的前提是 `CampaignStore` 是一般類別(`src/rtb/dsp/store.py:196` `class CampaignStore:`),沒有覆寫 `__setattr__`。但增量 2 要換掉的是 `Executor` 的私有方法(例如 `_write`、`_verify`,對應「DSP 已提交、本地還沒寫結果」「已驗證與確認的交易提交」這幾個當機點),而 `Executor` 是 `@dataclass(frozen=True)`(`src/rtb/executor/execution.py:309-310`)。凍結的 dataclass 會攔下任何**實例層級**的屬性賦值並丟出 `dataclasses.FrozenInstanceError`,`executor_instance._verify = die` 這種寫法直接炸掉,不是「換掉方法後行為不對」而是連子行程都起不來。要繞過去,必須改成類別層級賦值(`Executor._verify = die`,對所有實例都生效),這跟設計引用的既有前例在技術手法上不是同一件事,設計文字完全沒有點出這個差異,對照該區「每一種當機注入都應對到既有前例」的自我要求,這裡的「照抄」其實抄不過去。

file: `src/rtb/executor/execution.py:309-310`
file: `src/rtb/dsp/store.py:196`
file: `tests/dsp/test_store.py:400`
