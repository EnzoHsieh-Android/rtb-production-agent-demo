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
        # 孫行程不理 SIGTERM、也不接輸出管線(代碼審 r1 x1:原本 pytest 一結束就不再送 SIGKILL)
        "    child = subprocess.Popen(['sh', '-c', 'trap \"\" TERM; sleep 60'],\n"
        "                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
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


# ---- 代碼審 r1(增量 3)----
def test_the_verifier_tests_expand_their_forgeries_from_the_shared_list():
    """[代碼審 r1 a1/l2] 驗證器測試的造假清單直接從共用的 FORGERIES 展開(不是另抄一份)。"""
    import tests.tools.test_verify_claims as verifier_tests

    (mark,) = [m for m in verifier_tests.test_every_forgery_in_the_plan_is_blocked.pytestmark
               if m.name == "parametrize"]
    assert mark.args[1] is forgery_demos.FORGERIES
    assert all(not f.forge.__name__.startswith("_") for f in forgery_demos.FORGERIES)


def test_both_columns_run_the_same_tests_whatever_pytest_options_are_set(monkeypatch):
    """[代碼審 r1 s2/x2/a3/l1-2] 「沒有驗證器」那一欄跟驗證器用同一套清環境規則:外面帶
    PYTEST_ADDOPTS(例如取消選取一支證據測試)兩欄結果不變。"""
    # 旗標跟驗證器跑證據測試同一套(-E 不讀 PYTHON 開頭的變數、-s 不開 user site;這個 venv 沒開
    # user site,行為上量不出差別,這裡直接核對旗標)
    assert forgery_comparison._pytest_command("py")[:3] == ["py", "-E", "-s"]
    baseline = forgery_comparison.generate()["rows"]
    monkeypatch.setenv("PYTEST_ADDOPTS", "--deselect tests/dsp/test_server.py::test_pause")
    monkeypatch.setenv("PYTHONPATH", "/nonexistent")
    assert forgery_comparison.generate()["rows"] == baseline


def test_a_missing_pytest_marks_the_whole_table_not_generated(tmp_path):
    """[代碼審 r1 s5/l3-1] 產生器用的直譯器沒有 pytest:整張表寫「這次沒產生:環境缺 pytest」,不把
    「找不到 pytest」寫成測試結果(原本天花板那列還會顯示擋下)。"""
    fake = tmp_path / "python-without-pytest"
    fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    result = forgery_comparison.generate(python=str(fake))
    assert result["rows"] == []
    assert result["note"].startswith("這次沒產生:") and "pytest" in result["note"]


def test_a_pytest_run_that_did_not_run_the_tests_is_not_a_result():
    """[代碼審 r1 s5] pytest 結束代碼不是 0 或 1、或總結行不是測試總結(例如找不到證據測試):那一列算
    沒產生,不寫成「沒有驗證器」的結果。"""
    def vanish(repo):
        (repo / "tests/dsp/test_server.py").unlink()

    vanished = forgery_demos.Forgery("測試檔不見", vanish, None)
    (row,) = forgery_comparison.generate([vanished])["rows"]
    assert row["without_verifier"].startswith("這次沒產生:")
    assert "沒有跑成" in row["without_verifier"]


def test_the_stop_signal_only_records_a_request():
    """[代碼審 r1 s4] 產生器收到 SIGTERM 只記下「要停」,不在任何一行丟例外(原本在刪暫存 repo 的途中
    丟 SystemExit,暫存目錄殘留一半);兩列之間與等待時檢查。"""
    previous = signal.getsignal(signal.SIGTERM)
    try:
        forgery_comparison.install_stop_handler()
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.1)
        assert forgery_comparison.stop_requested()
    finally:
        signal.signal(signal.SIGTERM, previous)
        forgery_comparison.reset_stop()


def test_a_stop_during_a_step_timeout_still_kills_the_step(tmp_path):
    """[代碼審 r1 x1/l1-1] 某一步逾時、正在等 SIGTERM 生效時又收到外層的停止:那一步的整組照樣硬殺,
    不留孤兒(原本 SIGKILL 不送)。"""
    import subprocess
    import sys

    marker = tmp_path / "pytest.pid"
    script = f"""
import os, signal, sys
sys.path.insert(0, {str(forgery_comparison.ROOT)!r})
from tools import forgery_comparison as fc
from tools.forgery_demos import FILES, TEST_SERVER, Forgery, write_files, write_manifests
hang = TEST_SERVER.replace("def test_update():\\n", "def test_update():\\n"
    "    import os, signal, time\\n    signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n"
    "    open({str(marker)!r}, 'w').write(str(os.getpid()))\\n    time.sleep(60)\\n")
def forge(repo):
    write_files(repo, {{**FILES, "tests/dsp/test_server.py": hang}})
    write_manifests(repo)
fc.install_stop_handler()
fc.generate([Forgery("卡住", forge, None)], step_timeout=2)
"""
    popen = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
    try:
        deadline = time.monotonic() + 30
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        time.sleep(2.5)  # 步驟已逾時、正在等 SIGTERM 生效
        os.kill(popen.pid, signal.SIGTERM)
        popen.wait(30)
    finally:
        if popen.poll() is None:
            os.killpg(popen.pid, signal.SIGKILL)
    pid = int(marker.read_text())
    time.sleep(0.5)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    os.kill(pid, signal.SIGKILL)
    raise AssertionError("逾時那一步還活著")
