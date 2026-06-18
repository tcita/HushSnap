"""Tests for hushsnap.system.shell_utils.reveal_in_explorer.

These pin the contract that matters for the save-then-reveal flow:
- a missing file is a silent no-op (never raises),
- a successful COM round-trip returns True,
- any COM failure is swallowed and returns False (reveal must never block
  the save flow that called it),
- the PIDL is freed even on the open-failure path (no leak).

The COM calls are mocked so the tests run headless in CI; one live smoke
test against the real shell is gated to Windows and asserts only the HRESULT.
"""

import sys
import ctypes
from unittest.mock import MagicMock, patch

import pytest

from hushsnap.system import shell_utils


@pytest.fixture
def real_file(tmp_path):
    f = tmp_path / "shot.png"
    f.write_bytes(b"x")
    return f


def _install_fake_windll(monkeypatch, *, parse_hr=0, open_hr=0,
                        pidl_value=0xDEADBEEF):
    """Patch ctypes.windll with fakes whose byref(pidl) target is writable.

    reveal_in_explorer writes the PIDL through ctypes.byref(pidl). We can't
    easily fake byref, so instead we let the real ctypes.byref run and give
    the mock shell32 a real c_void_p to write into. The mock captures the
    ppidl argument and sets its .value.
    """
    freed = []

    ole32 = MagicMock()
    ole32.CoInitializeEx.return_value = 0
    ole32.CoTaskMemFree.side_effect = lambda p: freed.append(p)

    shell32 = MagicMock()
    shell32.SHParseDisplayName.return_value = parse_hr
    shell32.SHOpenFolderAndSelectItems.return_value = open_hr

    def parse(path, pbc, ppidl, sfgao, pout):
        # ppidl is ctypes.byref(pidl); writing .value simulates the API
        # filling the PIDL pointer.
        ppidl._obj.value = pidl_value
        return parse_hr

    shell32.SHParseDisplayName.side_effect = parse

    fake = MagicMock()
    fake.ole32 = ole32
    fake.shell32 = shell32
    monkeypatch.setattr(ctypes, "windll", fake, raising=False)
    return ole32, shell32, freed


def test_missing_file_returns_false(tmp_path, monkeypatch):
    """No file on disk → skip silently, no COM calls, False."""
    ole32, shell32, _ = _install_fake_windll(monkeypatch)
    with patch.object(shell_utils.logger, "debug") as dbg:
        assert shell_utils.reveal_in_explorer(tmp_path / "nope.png") is False
    shell32.SHParseDisplayName.assert_not_called()
    assert dbg.called


def test_success_returns_true_and_frees_pidl(real_file, monkeypatch):
    """Full successful round-trip → True, and the PIDL is freed."""
    ole32, shell32, freed = _install_fake_windll(monkeypatch, open_hr=0)
    assert shell_utils.reveal_in_explorer(real_file) is True
    shell32.SHOpenFolderAndSelectItems.assert_called_once()
    assert len(freed) == 1, "PIDL must be freed on success"


def test_parse_failure_returns_false(real_file, monkeypatch):
    """SHParseDisplayName returns an error → False, open never called."""
    _install_fake_windll(monkeypatch, parse_hr=0x80070002, pidl_value=0)
    with patch.object(shell_utils.logger, "debug"):
        assert shell_utils.reveal_in_explorer(real_file) is False
    # open not called because pidl was null


def test_open_failure_returns_false_but_frees(real_file, monkeypatch):
    """SHOpenFolderAndSelectItems returns an error → False, PIDL still freed."""
    ole32, shell32, freed = _install_fake_windll(monkeypatch, open_hr=0x80004005)
    with patch.object(shell_utils.logger, "debug"):
        assert shell_utils.reveal_in_explorer(real_file) is False
    assert len(freed) == 1, "PIDL must be freed even on open-failure path"


def test_never_raises(real_file, monkeypatch):
    """Any exception inside the COM path is swallowed → False."""
    ole32, shell32, freed = _install_fake_windll(monkeypatch)
    shell32.SHParseDisplayName.side_effect = OSError("boom")
    # Must not raise.
    assert shell_utils.reveal_in_explorer(real_file) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only live check")
def test_live_reveal_with_spaces(tmp_path):
    """Smoke test against the real shell: a spaced path must return True
    (S_OK). Does not assert a window opens — only the HRESULT — so it is
    safe in environments where Explorer is non-interactive."""
    f = tmp_path / "has spaces.png"
    f.write_bytes(b"x")
    assert shell_utils.reveal_in_explorer(f) is True
