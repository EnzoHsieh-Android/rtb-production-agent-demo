"""F7 效能:簽發器讀租戶設定的內容快取(計劃 [[Projects/F7效能_計劃]],S684 到 S685)。

快取在簽發器物件上、只記一份:每次呼叫照舊做安全讀檔(每一道檢查照原順序),讀到的位元組跟上次驗過的
完全相同才沿用上次的解析結果;任何一個位元組不同就重新解析、驗證,驗過才換進快取,驗不過照舊拒絕、
快取不動。
"""

import json
import os
import threading

import pytest

from rtb.executor import capability_signer
from rtb.executor.capability_signer import CapabilitySigner, SigningRefused
from tests.executor.fakes import TEST_KEY, write_config


@pytest.fixture
def parses(monkeypatch):
    """把解析函式換成計次的:數它被叫幾次。"""
    calls = []
    real = capability_signer._parse_tenants

    def counted(data):
        calls.append(data)
        return real(data)

    monkeypatch.setattr(capability_signer, "_parse_tenants", counted)
    return calls


@pytest.fixture
def config(tmp_path):
    directory = tmp_path / "conf"
    directory.mkdir(mode=0o700)
    return write_config(directory / "tenants.json")


def _rewrite(path, **kwargs):
    """原子地換掉設定檔內容(先寫暫存檔再改名),權限照舊。"""
    temporary = path.with_name(path.name + ".tmp")
    write_config(temporary, **kwargs)
    os.replace(temporary, path)


# ---- [S684] ----
def test_the_tenant_config_is_revalidated_whenever_its_bytes_change(config, parses):
    signer = CapabilitySigner(TEST_KEY)
    first = signer.read_tenants(config)
    assert signer.read_tenants(config) is first  # 內容一樣:不重解析,回同一份
    assert len(parses) == 1
    _rewrite(config, max_budget=999)  # 任一欄改了
    changed = signer.read_tenants(config)
    assert len(parses) == 2 and changed[0].max_budget == 999
    body = config.read_bytes()
    config.write_bytes(body.replace(b"999", b"998"))  # 只差一個位元組
    assert signer.read_tenants(config)[0].max_budget == 998 and len(parses) == 3
    good = config.read_bytes()
    config.write_bytes(json.dumps({"tenants": {"t-default": {"campaigns": ["c1"]}}}).encode())
    with pytest.raises(SigningRefused, match="config_invalid"):  # 新內容不合法:照舊拒絕
        signer.read_tenants(config)
    assert len(parses) == 4
    config.write_bytes(good)  # 改回上次驗過的內容:快取沒被不合法的內容換掉,不必再解析
    assert signer.read_tenants(config)[0].max_budget == 998 and len(parses) == 4
    # 另一個簽發器有自己的快取(不是全域狀態)
    CapabilitySigner(TEST_KEY).read_tenants(config)
    assert len(parses) == 5


def test_concurrent_reads_always_match_the_bytes_they_read(config, monkeypatch):
    """多個執行緒同時讀、期間一直改設定檔:每次回的解析結果一定對應那一次讀到的那份位元組。"""
    signer = CapabilitySigner(TEST_KEY)
    seen = threading.local()
    real_read = capability_signer._read_config_securely

    def remembering(path):
        seen.data = real_read(path)
        return seen.data

    monkeypatch.setattr(capability_signer, "_read_config_securely", remembering)
    mismatches, stop = [], threading.Event()

    def reader():
        while not stop.is_set():
            try:
                tenants = signer.read_tenants(config)
            except SigningRefused:
                continue  # 改名的瞬間可能讀不到:照舊拒絕,不是這裡要驗的
            if tenants != capability_signer._parse_tenants(seen.data):
                mismatches.append(seen.data)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    for round_ in range(200):
        _rewrite(config, max_budget=500 + round_ % 2)
    stop.set()
    for thread in threads:
        thread.join(10)
    assert mismatches == []


# ---- [S685] ----
def _owner_mismatch(_config, monkeypatch):
    real_uid = os.getuid()  # 換掉「目前使用者」:目錄與檔案的擁有者都對不上
    monkeypatch.setattr(capability_signer.os, "getuid", lambda: real_uid + 1)
    return "config_directory_insecure"


def _file_mode(mode, reason):
    def change(config, _monkeypatch):
        os.chmod(config, mode)
        return reason
    return change


def _dir_other_writable(config, _monkeypatch):
    os.chmod(config.parent, 0o702)  # noqa: S103 - 故意設成別人可寫,驗拒絕
    return "config_directory_insecure"


def _symlink(config, _monkeypatch):  # 換成指向同樣內容的符號連結
    target = config.with_name("real.json")
    target.write_bytes(config.read_bytes())
    os.chmod(target, 0o600)
    config.unlink()
    config.symlink_to(target)
    return "config_unreadable"


def _named_pipe(config, _monkeypatch):
    config.unlink()
    os.mkfifo(config, 0o600)
    return "config_unreadable"


CHANGES = {
    "file_group_writable": _file_mode(0o620, "config_file_insecure"),
    "file_other_writable": _file_mode(0o602, "config_file_insecure"),
    "dir_other_writable": _dir_other_writable,
    "symlink": _symlink,
    "owner_mismatch": _owner_mismatch,
    "named_pipe": _named_pipe,
}


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_the_config_cache_never_skips_the_ownership_and_mode_checks(config, change, monkeypatch):
    signer = CapabilitySigner(TEST_KEY)
    calls = []
    real_read = capability_signer._read_config_securely
    monkeypatch.setattr(capability_signer, "_read_config_securely",
                        lambda path: calls.append(path) or real_read(path))
    signer.read_tenants(config)
    signer.read_tenants(config)  # 快取命中
    assert len(calls) == 2  # 命中時安全讀檔照樣每次呼叫
    reason = CHANGES[change](config, monkeypatch)
    try:
        with pytest.raises(SigningRefused, match=reason):
            signer.read_tenants(config)
    finally:
        monkeypatch.undo()
        os.chmod(config.parent, 0o700)
    assert len(calls) == 3
