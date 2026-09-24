"""前後比較表(Phase 12 增量 3):同一份造假改動、同一份清單、同一組證據測試,差別只在有沒有跑
驗證器。

每一列都真的跑:在暫存目錄造小 repo、套上造假改法,跑證據測試(沒有驗證器那一欄)與驗證器
(有驗證器那一欄)。
"""

import os
import signal
import time

from tools.forgery_demos import FILES, TEST_SERVER, write_files, write_manifests

from tools import forgery_comparison, forgery_demos


def test_the_comparison_shows_every_forgery_with_and_without_the_verifier():
    """[S1041] 五種造假加一列天花板,每一列並排兩欄:沒有驗證器時證據測試照樣全過,有驗證器時五種都擋下
    而且原因對得上;天花板兩欄都是通過,寫明有驗證器也擋不住、歸人審。說明寫明比的是有沒有機械驗證,
    不是比模型寫程式的品質。"""
    result = forgery_comparison.generate()

    rows = result["rows"]
    expected = [*forgery_demos.FORGERIES, forgery_demos.CEILING]
    assert [row["forgery"] for row in rows] == [f.label for f in expected]
    for row, forgery in zip(rows, expected, strict=True):
        assert row["without_verifier"].startswith("pytest 結束代碼 0:"), row
        if forgery.expected is None:
            assert row["with_verifier"].startswith("通過"), row
        else:
            assert row["with_verifier"].startswith("擋下:"), row
            assert forgery.expected in row["with_verifier"], row
    assert "有驗證器也擋不住,這部分歸人審" in rows[-1]["forgery"]
    assert "有沒有機械驗證" in result["note"] and "不是比模型寫程式的品質" in result["note"]
    assert "有驗證器也擋不住" in result["note"]
    assert result["seconds"] > 0


def test_the_rows_come_from_the_same_definitions_the_verifier_tests_use():
    """比較表的列跟驗證器測試的造假清單取自同一個模組(同一批函式),不是另抄一份。"""
    from tests.tools import test_verify_claims as verifier_tests

    used = {verifier_tests._only_says_done, verifier_tests._code_changed_hash_not,
            verifier_tests._claim_wider_than_evidence, verifier_tests._skipped_test,
            verifier_tests._conftest_changed_without_rehash}
    assert {f.forge for f in forgery_demos.FORGERIES} == used


def _broken(_repo):
    raise RuntimeError("這一列造不出來")


def test_a_row_that_fails_to_generate_says_why_and_the_others_still_run():
    """某一列產生失敗:那一列照實寫「這次沒產生:原因」,不寫假結果;其他列照常。"""
    rows = forgery_comparison.generate(
        [forgery_demos.Forgery("造不出來", _broken, None), forgery_demos.FORGERIES[0]])["rows"]

    assert rows[0]["without_verifier"].startswith("這次沒產生:")
    assert "這一列造不出來" in rows[0]["with_verifier"]
    assert rows[1]["with_verifier"].startswith("擋下:")


def test_a_row_that_times_out_leaves_no_process_behind(tmp_path):
    """某一列逾時:整個行程群組(含證據測試再起的孫行程)一起收掉,那一列寫逾時,不留行程。"""
    marker = tmp_path / "grandchild.pid"
    hanging = TEST_SERVER.replace(
        "def test_update():\n",
        "def test_update():\n    import subprocess, time\n"
        f"    child = subprocess.Popen(['sleep', '60'])\n"
        f"    open({str(marker)!r}, 'w').write(str(child.pid))\n    time.sleep(60)\n")

    def hang(repo):
        write_files(repo, {**FILES, "tests/dsp/test_server.py": hanging})
        write_manifests(repo)

    started = time.monotonic()
    rows = forgery_comparison.generate([forgery_demos.Forgery("卡住", hang, None)],
                                       step_timeout=3)["rows"]

    assert time.monotonic() - started < 30
    assert rows[0]["without_verifier"].startswith("這次沒產生:") and "逾時" in rows[0][
        "without_verifier"]
    pid = int(marker.read_text())
    time.sleep(0.5)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    os.kill(pid, signal.SIGKILL)
    raise AssertionError("證據測試起的孫行程還活著")
