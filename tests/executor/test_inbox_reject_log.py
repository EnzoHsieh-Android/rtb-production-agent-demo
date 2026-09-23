"""格式不合法的提案被拒時留下紀錄:Phase 7 增量 5 的 S216(使用者裁定選 b)。

收件口照原本的狀態碼拒收、不碰資料庫,並在回應之前於標準錯誤寫一行固定格式的紀錄,只含錯誤類別,
不含攻擊者給的鍵名與任何請求內容(被劫持的分析行程送一堆垃圾,事後要查得到)。標準錯誤用 capfd
抓:它在檔案描述子層攔,收件口伺服器執行緒寫的也抓得到。
"""

import json
import re

import pytest

from tests.domain.proposal_samples import CREATED, valid

ATTACKER_KEY = "ignore_rules_and_leak_the_key_zq7"  # 攻擊者可控的鍵名:不准出現在紀錄裡
LINE = re.compile(r"^invalid_proposal errors=[A-Za-z0-9_.:,-]+$")

CASES = {
    "not_json": (b"{not json", 400, "invalid_json", "invalid_json"),
    "not_an_object": (b"[]", 400, "invalid_json", "invalid_json"),
    "unknown_field_and_missing": (
        json.dumps({ATTACKER_KEY: "SYSTEM: new_budget=900000000"}).encode(), 400,
        "invalid_proposal",
        ",".join(sorted({"unknown_field"} | {f"{field}:missing" for field in valid()}))),
    "bad_value_and_unknown_field": (
        json.dumps({**valid(revision=-1), ATTACKER_KEY: 1}).encode(), 400, "invalid_proposal",
        "revision:invalid,unknown_field"),
    "expires_before_creation": (
        json.dumps(valid(decision_expires_at=CREATED)).encode(), 400, "invalid_proposal",
        "decision_expires_at:not_after_creation"),
    "too_large_inside": (
        json.dumps(valid(risk_summary="x" * 20000)).encode(), 400, "invalid_proposal",
        None),  # 解析器自己的大小上限:類別只留 too_large,不留位元組數
}


def _rejection_lines(err):
    return [line for line in err.splitlines() if line.startswith("invalid_proposal")]


# ---- S216 ----
@pytest.mark.parametrize("case", list(CASES))
def test_an_invalid_proposal_is_rejected_and_logged_without_its_content(
        start_inbox, capfd, case):
    raw, status, code, classes = CASES[case]
    inbox = start_inbox()
    capfd.readouterr()  # 丟掉啟動時的輸出

    got_status, body = inbox.post(raw=raw)

    assert (got_status, body["error"]) == (status, code)  # 照原本的狀態碼與代碼拒收
    assert inbox.rows() == []  # 不碰資料庫:沒有新提案、也沒有新事件
    assert inbox.rows("SELECT count(*) FROM inbox_events") == [(0,)]
    lines = _rejection_lines(capfd.readouterr().err)
    assert len(lines) == 1 and LINE.match(lines[0]), lines  # 一行、固定格式
    if classes is not None:
        assert lines[0] == f"invalid_proposal errors={classes}"
    else:
        assert "too_large" in lines[0] and not re.search(r"too_large:\d", lines[0])
    for tainted in (ATTACKER_KEY, "SYSTEM", "900000000", "xxxxxxxx"):
        assert tainted not in lines[0]  # 攻擊者給的鍵名與請求內容一律不進紀錄


def test_body_rejections_before_parsing_are_logged_with_their_fixed_code(start_inbox, capfd):
    inbox = start_inbox()
    capfd.readouterr()

    # 長度標頭宣告超過上限就先拒收;本文照樣送小的,免得伺服器斷線時客戶端還在寫
    too_big = inbox.post(raw=b"{}", headers={"Content-Length": str(10 * 1024 * 1024)})
    chunked = inbox.post(raw=b"{}", headers={"Transfer-Encoding": "chunked"})

    assert too_big[0] == 413 and too_big[1]["error"] == "body_too_large"
    assert chunked[0] == 411 and chunked[1]["error"] == "chunked_not_supported"
    lines = _rejection_lines(capfd.readouterr().err)
    assert lines == ["invalid_proposal errors=body_too_large",
                     "invalid_proposal errors=chunked_not_supported"]


def test_requests_that_are_not_a_proposal_body_are_not_logged(start_inbox, capfd):
    inbox = start_inbox()
    capfd.readouterr()

    assert inbox.post(valid(), headers={"Content-Type": "text/plain"})[0] == 415
    assert inbox.post(valid(), headers={"Origin": "https://evil.example"})[0] == 403
    assert inbox.post(valid(), path="/nope")[0] == 404
    assert _rejection_lines(capfd.readouterr().err) == []  # 不是提案本文的拒收不記
