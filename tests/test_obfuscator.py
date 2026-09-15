"""Behaviour tests for the obfuscator.

These require a ``lua`` interpreter on PATH (any of lua5.4 / lua5.1 / luajit).
Each case obfuscates a sample and asserts the obfuscated program prints exactly
what the original does.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import os
import pytest

from obfuscator.pipeline import Obfuscator, Options
from obfuscator.packer import lzw_compress
import sys
sys.path.insert(0, os.path.dirname(__file__))

LUA = shutil.which("lua") or shutil.which("lua5.4") or shutil.which("luajit")
HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)

DEMO = os.path.join(ROOT, "examples", "demo.lua")


def _run_lua(path: str) -> str:
    out = subprocess.run([LUA, path], capture_output=True, text=True, timeout=30)
    return out.stdout + out.stderr


def _obfuscate_to_file(src: str, opt: Options) -> str:
    out = Obfuscator(opt).obfuscate(src)
    fd, path = tempfile.mkstemp(suffix=".lua")
    with os.fdopen(fd, "w") as fh:
        fh.write(out)
    return path


OPTION_CASES = {
    "base": Options(base_obfuscation=True, seed=1),
    "control_flow": Options(control_flow=True, seed=1),
    "static_env": Options(static_environment=True, seed=1),
    "globals": Options(hardcore_globals=True, seed=1),
    "optimize": Options(optimizations=True, seed=1),
    "free": Options.free(seed=1),
    "free_opt": Options.free(optimizations=True, seed=1),
    "pro": Options.pro(seed=1),
    "pro_full": Options.pro(virtualization=True, optimizations=True, seed=1),
}


@pytest.mark.skipif(LUA is None, reason="no lua interpreter available")
@pytest.mark.parametrize("name", list(OPTION_CASES))
def test_behaviour_preserved(name):
    src = open(DEMO).read()
    expected = _run_lua(DEMO)
    path = _obfuscate_to_file(src, OPTION_CASES[name])
    try:
        assert _run_lua(path) == expected
    finally:
        os.unlink(path)


def test_header_present():
    out = Obfuscator(Options.free(seed=1)).obfuscate("print(1)")
    assert out.startswith("--[[ Skid Optimzation v1.0]]")


def test_lzw_roundtrip_reference():
    # Mirror decoder in Python to guard the compressor.
    def decompress(buf: bytes) -> bytes:
        dic = {i: bytes([i]) for i in range(256)}
        nxt = 256
        res = []
        prev = None
        i = 0
        while i < len(buf):
            code = buf[i] * 256 + buf[i + 1]
            i += 2
            entry = dic[code] if code in dic else prev + prev[:1]
            res.append(entry)
            if prev is not None:
                dic[nxt] = prev + entry[:1]
                nxt += 1
                if nxt >= 65536:
                    dic = {j: bytes([j]) for j in range(256)}
                    nxt = 256
                    prev = None
                    continue
            prev = entry
        return b"".join(res)

    for data in [b"", b"a", b"abcabcabc", bytes(range(256)) * 100]:
        assert decompress(lzw_compress(data)) == data
