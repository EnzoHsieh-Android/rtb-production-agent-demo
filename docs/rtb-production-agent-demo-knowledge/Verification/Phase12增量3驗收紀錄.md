---
type: verification
status: pass
date: 2026-09-25
valid_under:
  - 主線合入 phase12-inc3 a458a5c 時的程式
revalidate_when:
  - 五種造假示範、驗證器跑證據測試的旗標或比較表產生器再改時
tags:
  - type/verification
  - status/pass
plan_refs:
  - "[[Projects/RTB_Phase12一鍵展示與HTML報告_計劃]]"
---
# Phase12增量3驗收紀錄

範圍:Phase 12 增量 3「前後比較表」:五種造假加天花板一列,各跑一次「沒有驗證器」與「有驗證器」,在全部跑一次的最後一步產生,報告底部並排顯示。

## 審查
- 代碼審 3 輪,卷證在 governance/review-reports/code-phase12-inc3/:第 1 輪 6 席 16 條(5 major)、第 2 輪 3 席 5 條(2 major)、第 3 輪 3 席 6 條(1 major)全修。
- 過程中查出 Phase 11 驗證器本身的洞:repo 內頂替 pytest 內部模組(`_pytest/`、標準庫同名檔)能不重算雜湊就把失敗改成通過。處理:跑證據測試加 `-P`(擋最直接的一條);其餘依威脅模型「防疏忽不防存心繞過」不追,比較表說明與 Systems/宣稱驗證器 照實寫明已知繞過手法。
- 第 3 輪修正由協調者自己驗收:逐條讀改動、在乾淨複本(git archive,不設 PYTHONPATH)跑全套。

## 結果(2026-09-25,協調者本機,乾淨複本)
- ruff、mypy 乾淨;全套 2875 過、1 跳過;宣稱驗證器 5 條宣稱、77 支證據測試全過。
- 驗證器測試項目:搬家前 278 項 id 逐字不變,新增 1 項(pytest 內部頂替的防回歸)。
- 實作者真跑全部跑一次(F7 縮小版):比較表六列都產生,約 2.5 秒。

## 已知限制
- 驗證器不防存心繞過(已知兩類:連雜湊一起重算;在 repo 內頂替 pytest 內部或標準庫模組)。
- 子行程環境白名單多了 LC_ALL、LC_CTYPE(代使用者裁定:語系變數不是秘密;固定設 C.UTF-8 在 macOS 不一定有)。
