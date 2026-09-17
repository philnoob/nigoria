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
    assert out.startswith("--[[ Skid Optimzation v1.5 Free]]")
    out = Obfuscator(Options.pro(seed=1)).obfuscate("print(1)")
    assert out.startswith("--[[ Skid Optimzation v2.0 Pro]]")


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


@pytest.mark.skipif(LUA is None, reason="no lua interpreter available")
@pytest.mark.parametrize("preset", ["free", "pro"])
def test_anti_tamper_hides_payload(preset):
    import re
    opt = Options.free(seed=5) if preset == "free" else Options.pro(seed=5)
    opt.pad_output = False  # padding would otherwise be the largest string
    out = Obfuscator(opt).obfuscate('print("SECRET_OK_12345")')
    # sanity: clean output runs and prints the secret
    p = _obfuscate_path(out)
    try:
        assert "SECRET_OK_12345" in _run_lua(p)
    finally:
        os.unlink(p)
    # tamper the largest embedded string (the encoded pool / stream) and
    # confirm the secret never surfaces
    longest = max(re.finditer(r'"((?:[^"\\]|\\.){40,})"', out),
                  key=lambda m: len(m.group(1)))
    mid = (longest.start(1) + longest.end(1)) // 2
    tampered = out[:mid] + ("X" if out[mid] != "X" else "Y") + out[mid + 1:]
    p = _obfuscate_path(tampered)
    try:
        assert "SECRET_OK_12345" not in _run_lua(p)
    finally:
        os.unlink(p)


def _obfuscate_path(code: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".lua")
    with os.fdopen(fd, "w") as fh:
        fh.write(code)
    return path


@pytest.mark.skipif(LUA is None, reason="no lua interpreter available")
def test_vm_preserves_closures_and_multiret():
    from obfuscator.vm import compile_chunk, emit_vm
    from obfuscator.parser import parse
    code = (
        "local fns={}\n"
        "for i=1,3 do fns[i]=function() return i end end\n"
        "local function m() return 1,2,3 end\n"
        "local a,b,c=m()\n"
        "print(fns[1](),fns[2](),fns[3](),a,b,c)\n"
    )
    vm = emit_vm(compile_chunk(parse(code)))
    p = _obfuscate_path(vm)
    try:
        assert _run_lua(p).strip() == "1\t2\t3\t1\t2\t3"
    finally:
        os.unlink(p)


def test_vm_falls_back_on_goto():
    from obfuscator.vm import compile_chunk, Unsupported
    from obfuscator.parser import parse
    with pytest.raises(Unsupported):
        compile_chunk(parse("::top::\ngoto top\n"))


@pytest.mark.skipif(LUA is None, reason="no lua interpreter available")
@pytest.mark.parametrize("seed", [1, 2, 3, 7, 99])
def test_vm_polymorphic_seeds_preserve_behaviour(seed):
    """Each seed produces a different interpreter/opcode layout but the same
    runtime behaviour."""
    from obfuscator.vm import compile_chunk, emit_vm, OpMap
    from obfuscator.parser import parse
    code = (
        "local f={} for i=1,3 do f[i]=function() return i*i end end\n"
        "local function sum(t) local s=0 for _,v in ipairs(t) do s=s+v end return s end\n"
        "local o=setmetatable({n=1},{__index=function(_,k) return k end})\n"
        "print(f[1](),f[2](),f[3](),sum({f[1](),f[2](),f[3]()}),o.zzz,o.n)\n"
    )
    vm = emit_vm(compile_chunk(parse(code), OpMap(seed)), seed)
    p = _obfuscate_path(vm)
    try:
        assert _run_lua(p).strip() == "1\t4\t9\t14\tzzz\t1"
    finally:
        os.unlink(p)


def test_two_pro_builds_differ():
    a = Obfuscator(Options.pro(seed=1)).obfuscate('print("hi")')
    b = Obfuscator(Options.pro(seed=2)).obfuscate('print("hi")')
    assert a != b  # polymorphic: no two builds share a layout


@pytest.mark.skipif(LUA is None, reason="no lua interpreter available")
def test_loadstring_dumper_gets_only_decoys():
    """A loadstring-hook dumper must capture only decoys, never real logic,
    and the real program must still run."""
    src = ('local secret="REAL_LOGIC_9931" '
           'local function calc(a,b) return a*b+7 end print(secret, calc(6,7))')
    out = Obfuscator(Options.pro(seed=8)).obfuscate(src)
    path = _obfuscate_path(out)
    dumper = (
        'local cap={} local real=load\n'
        'local function h(s,...) cap[#cap+1]=s return function() end end\n'
        'loadstring=h load=h\n'
        'pcall(real(io.open("%s"):read("*a")))\n'
        'local leaked=false\n'
        'for _,c in ipairs(cap) do if tostring(c):find("REAL_LOGIC_9931") '
        'or tostring(c):find("calc") then leaked=true end end\n'
        'io.write(leaked and "LEAK" or "SAFE")\n' % path)
    dpath = _obfuscate_path(dumper)
    try:
        assert _run_lua(dpath).strip().endswith("SAFE")
    finally:
        os.unlink(path); os.unlink(dpath)


@pytest.mark.skipif(LUA is None, reason="no lua interpreter available")
def test_anti_sandbox_only_runs_in_roblox():
    """With anti_sandbox on, the payload runs only when typeof(game)==Instance
    (a real executor), and does nothing in a plain-Lua dumper sandbox."""
    from bot.obfuscation_service import obfuscate_script, default_keys
    out = obfuscate_script("pro", default_keys("pro"),
                           'print("SANDBOX_CHECK_42")', seed=5).output

    def run(prelude=""):
        fd, p = tempfile.mkstemp(suffix=".lua")
        with os.fdopen(fd, "w") as fh:
            fh.write(prelude + "\n" + out)
        try:
            return _run_lua(p).strip()
        finally:
            os.unlink(p)

    # dumper sandbox: no game/typeof -> payload must NOT run
    assert "SANDBOX_CHECK_42" not in run()
    # real executor: game is an Instance -> payload runs
    mock = ('typeof=function(x) return x==game and "Instance" or type(x) end '
            'game={}')
    assert "SANDBOX_CHECK_42" in run(mock)
