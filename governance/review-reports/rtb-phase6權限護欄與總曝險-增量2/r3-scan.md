severity: major

範圍:只審凍結快照「## 增量 2 設計:護欄表格」節裡跟 [S408]、實作細節第 5 點(靜態匯入閉包掃描)相關的部分。其他規則表、比例上限內容不在我的鏡頭內,未評。

## 做法驗證(用臨時腳本實測目前的閉包)

用 `ast.walk` 從 `src/rtb/analyzer/*.py` 出發、遞迴收本專案模組(`mod.split(".")[0] == "rtb"` 且能對到檔案),在 `/Users/enzo/rtb-3b` 實際跑一次,結果閉包是:

```
rtb/analyzer/{dsp_client,flow,inbox_client,instrumented,policy,task_store}.py
rtb/domain/{_checks,attempt,evidence,metrics,proposal,task_state}.py
rtb/httpclient.py
rtb/sqlitekit.py
```

不含 `rtb.executor.*`、`rtb.dsp.*`——跟既有測試 `test_importing_the_analyzer_does_not_load_the_capability_module`(tests/analyzer/test_boundaries.py)的斷言一致,目前不會誤報把執行側拉進來。以下按快照原文逐項核對可行性。

引句:「凡是本專案的模組就加進來、再讀它的匯入,直到沒有新模組」

- **函式內部、if/try 裡的匯入**:`ast.walk` 本身就會走進 `FunctionDef`/`If`/`Try` 內部的 `Import`/`ImportFrom` 節點,不需要特別處理——實測過 `def foo(): import rtb.executor.execution as bad` 與 `try: from rtb.dsp import server`,兩者都被收到。可行。
- **TYPE_CHECKING 區塊**:因為是純文字解析(不是執行期反射),`if TYPE_CHECKING:` 底下的匯入照樣被 `ast.walk` 收進去,不用另外偵測這個區塊——這點對「不漏報」是好事,但也表示型別提示用的匯入會被當成真的閉包成員,可能造成過嚴的假警報(見下)。
- **「從套件匯入子模組」**:`instrumented.py` 現在就有 `from rtb.analyzer import dsp_client` 這種寫法(base="rtb.analyzer", name="dsp_client"),必須把 `base + "." + alias.name` 接起來才能解析到 `rtb/analyzer/dsp_client.py`;只看 `node.module` 會解析成 "rtb.analyzer"(一個沒有原始碼檔的命名空間套件,查無此路徑),就會漏掉這支檔案。快照第 5 段有明講這個實作細節,且是本專案真實存在的寫法,設計是對的。
- **命名空間套件**:`src/rtb` 底下完全沒有 `__init__.py`(`rtb`、`rtb.analyzer`、`rtb.domain`、`rtb.executor` 全是命名空間套件),「查不到檔案就跳過、不當錯」這條是必要的,不是多餘的防呆——已用腳本驗證過 `is_project_module("rtb.analyzer")` 正確回 False(改用「base.name 接起來」的候選路徑才解析得到實際檔案)。

## F1 相對匯入完全沒被提到,literal 實作會靜默漏掃

severity: major
blocking: 是 — 照字面實作會漏掃,S408 的測試在漏掃的情境下會綠燈放過真的洞,不是措辭問題

引句:「讀出檔案裡任何位置(含函式內部)的匯入敘述,凡是本專案的模組就加進來」

引句:「『從套件匯入子模組』的寫法要把套件路徑與名稱接起來判斷是不是共用 HTTP 用戶端;沒有原始碼檔的模組(命名空間套件)跳過不當錯」

這是設計文對「怎麼判斷是不是本專案模組」列出的唯二實作細節,兩輪審計也都在這個主題上抓過東西(r1 的寫入掃描席、r2 的外家席),但完全沒提到**相對匯入**(`from .flow import X`、`from . import policy`)要怎麼解析成完整模組路徑。實測過:對 `from .flow import DspOperation` 這種寫法,`ast.ImportFrom` 給出的是 `module="flow"`、`level=1`(不是 `"rtb.analyzer.flow"`)。若實作者照字面「凡是本專案的模組就加進來」去判斷(檢查 `mod.split(".")[0] == "rtb"`),`"flow"` 這個裸名字會直接判定成「不是本專案模組」而被跳過——不是報錯,是**靜默漏掉**,閉包少了這個模組,遞迴到此為止。

目前 `src/rtb` 裡確實還沒有任何相對匯入(用 `grep -rnE "^\s*from \.+" src/` 驗過,零筆),所以現在寫的測試會綠。但相對匯入是 Python 裡完全平常的寫法(不是刻意繞過,是很多人重構套件內部呼叫時的自然選擇),跟快照自己承認的「防的是忘記,不防刻意繞過」的防線設定衝突:一個工程師以後在 `rtb/analyzer/instrumented.py` 把 `from rtb.analyzer.flow import Accepted` 改寫成 `from .flow import Accepted`(語意完全相同,常見的 IDE 自動改寫或風格統一),閉包就會在這個節點斷掉,如果 `flow.py` 之後又牽出別的模組(例如又新增一個從 `flow.py` 到別的檔案的匯入,且那支檔案有提到 `request_json`),S408 的測試不會抓到,而且不會有任何錯誤或例外提示——這正是這份設計要防的那類「忘記」,卻恰好被它自己的判斷邏輯放過。

需要在設計裡明講怎麼把 `level`(node.level)、匯入所在檔案的套件路徑,跟 `node.module` 接成完整的 dotted path 才能正確解析(這跟「從套件匯入子模組要接路徑與名稱」是同一類問題,只是多了「往上不確定幾層」這個維度),否則 literal 實作會在這裡留一個靜默漏洞。

## 次要觀察(measure 過但不到 blocking)

TYPE_CHECKING 區塊會被原樣收進閉包(見上),這對「不漏抓」是安全的方向,但反過來可能造成假警報:如果分析端某支檔案為了型別提示寫了 `if TYPE_CHECKING: from rtb.executor.capability_signer import CapabilitySigner`(純型別用,執行期從不 import),靜態閉包一樣會把 `capability_signer.py` 收進來去掃「有沒有提到 request_json」——這支檔案本身不含 request_json,不會真的讓 S408 紅,但如果閉包因此牽到別的、真的含 request_json 呼叫但屬於正常執行側用途的模組,會造成測試對著一個實際上執行期到不了的路徑報錯,要花時間排查是真違規還是型別提示的副作用。快照沒有處理這個情境;因為目前分析端原始碼裡沒有任何 TYPE_CHECKING 區塊(已用 `grep -rn "TYPE_CHECKING" src/` 確認零筆),這不影響目前這批測試能不能綠,只在文件精度上標記,不算擋。
