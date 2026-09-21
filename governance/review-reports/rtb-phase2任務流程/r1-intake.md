# rtb-phase2任務流程 r1 intake

preflight-4: ran

## 前掃(機械宣稱驗語意)
| 宣稱 | 做法 | 結果 |
|---|---|---|
| 提案有「修訂序號」欄位可當冪等鍵的一半 | 讀 src/rtb/domain/proposal.py 的 Proposal | 屬實,欄位名 revision、且 to_primitives 存在 |
| 增量 1 有可重用的解析函式 | 同上 | 屬實:parse_proposal;但「內容雜湊」函式不存在,計劃已改成「增量 2 新增」 |
| Phase 0 架構的冪等鍵是 (task_id, revision) | 讀 Phase 0 架構節點第 149 行 | 屬實 |

## 收貨
- 六席全數收齊後才動計劃。normalize:六份已是正規化格式;quote-check 六份全數錨定;refcheck 無缺檔或超出行號。
- 判讀:34 條都是「設計缺口或條款矛盾」,審材是文字設計,我逐條回讀第 1 版對應段落確認屬實(例:S1 要回原結果而 S3 要拒收較低修訂,兩條確實矛盾;在途上限確實沒有定義與釋放條件)。沒有需要程式重現的宣稱,所以沒有重現失敗的項目。
- 各席 major 數:s1 4、s2 5、s3 4、s4 2、s5 5、sarch 2,共 22 條 major,12 條 minor(合計 34);blocking 與 severity 綁定,我在上面的計劃紀錄寫的「blocking 20」是筆誤,以機器數為準。

## 處置
- 全部折入第 2 版設計(見計劃「審計修正紀錄」),沒有放行(有 major,規定不得放行)。
- 重疊的歸併:S1/S3 順序矛盾(s1f1、s1f4、s2f2、s5f6)→ 明寫判斷順序與單一交易;在途上限(s2f3、s3f2、s4f3、s1f3)→ 定義在途、取代先釋放名額、預設 8;事件表(s1f6、s2f5、s3f3、s4f6、s5f4)→ 固定欄位、封閉列舉、有界;S8 過期(s1f3、s4f1)→ 收件時過期回 422 不存,之後過期標為已過期;自建 HTTP 與 SQLite(sarch f1)→ 抽共用基礎並要求 DSP 測試不變(S19);無家的新檔(sarch f2)→ lands_in 加兩篇新節點。

refuted:none。
