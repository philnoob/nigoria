"""Payload packing: LZW compression, byte ciphering, and loader emission.

This implements the "VM compression", "advanced VM compression", "intense VM
structure" and "virtualization" features. A payload (Lua source) is compressed
with LZW, ciphered, embedded in a loader that reconstructs and ``loadstring``s
it at runtime, and the whole thing can be nested through several layers.

Everything here is validated against Lua 5.4 in the test-suite and uses only
constructs available in Roblox's Luau (``loadstring``, string.byte/char,
table.concat), so packed output runs under executors.
"""

from __future__ import annotations

import base64

from .luastr import compact_lua_bytes as _compact_lua_bytes
from .namegen import PreambleNamer

LZW_LIMIT = 65536


# --- LZW --------------------------------------------------------------------
def lzw_compress(data: bytes) -> bytes:
    dic = {bytes([i]): i for i in range(256)}
    nxt = 256
    w = b""
    codes: list[int] = []
    for c in data:
        wc = w + bytes([c])
        if wc in dic:
            w = wc
        else:
            codes.append(dic[w])
            dic[wc] = nxt
            nxt += 1
            w = bytes([c])
            if nxt >= LZW_LIMIT:
                dic = {bytes([i]): i for i in range(256)}
                nxt = 256
    if w:
        codes.append(dic[w])
    out = bytearray()
    for code in codes:
        out.append((code >> 8) & 0xFF)
        out.append(code & 0xFF)
    return bytes(out)


# --- byte cipher ------------------------------------------------------------
def cipher_encode(data: bytes, seed: int, step: int) -> bytes:
    out = bytearray()
    for j, b in enumerate(data, start=1):
        out.append((b + (seed + j * step)) % 256)
    return bytes(out)


def _byte_literal(data: bytes) -> str:
    return _compact_lua_bytes(data)


# --- Lua templates ----------------------------------------------------------
def _decomp_fn(name: str, sbyte: str, schar: str) -> str:
    # LZW decompressor; verified byte-identical to the Python compressor.
    return (
        f"local function {name}(buf)"
        f"local d={{}};for i=0,255 do d[i]={schar}(i) end;"
        f"local nx=256;local r={{}};local rn=0;local pv=nil;"
        f"local i=1;local bl=#buf;"
        f"while i<bl do "
        f"local c={sbyte}(buf,i)*256+{sbyte}(buf,i+1);i=i+2;"
        f"local e;if d[c] then e=d[c] else e=pv..string.sub(pv,1,1) end;"
        f"rn=rn+1;r[rn]=e;"
        f"if pv~=nil then d[nx]=pv..string.sub(e,1,1);nx=nx+1;"
        f"if nx>=65536 then d={{}};for k=0,255 do d[k]={schar}(k) end;"
        f"nx=256;pv=nil else pv=e end else pv=e end;"
        f"end;return table.concat(r) end;"
    )


def _b64_decode_fn(name: str) -> str:
    return (
        f"local function {name}(data)"
        "local b='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';"
        "local lut={};for i=1,#b do lut[string.byte(b,i)]=i-1 end;"
        "local out={};local n=0;local bits=0;local nb=0;"
        "for i=1,#data do local v=lut[string.byte(data,i)];"
        "if v then bits=bits*64+v;nb=nb+6;"
        "if nb>=8 then nb=nb-8;local p=2^nb;"
        "n=n+1;out[n]=string.char(math.floor(bits/p)%256);bits=bits%p end end end;"
        "return table.concat(out) end;"
    )


def _payload_hash(data: bytes) -> int:
    h = 0
    for b in data:
        h = (h * 31 + b) % 4294967296
    return h


def _pool_checksum(data: bytes) -> int:
    return sum(data) % 16777216


def _loader_junk(namer: PreambleNamer, rng, count: int) -> str:
    out = []
    for _ in range(count):
        nm = namer.new()
        r = rng.randint(0, 2)
        if r == 0:
            val = str(rng.randint(1, 10 ** 9))
        elif r == 1:
            body = "".join(rng.choice("abcdef0123456789_")
                           for _ in range(rng.randint(6, 20)))
            val = '"' + body + '"'
        else:
            val = "{" + ",".join(str(rng.randint(0, 255))
                                 for _ in range(rng.randint(2, 6))) + "}"
        out.append(f"local {nm}={val};")
    return "".join(out)


def build_loader(payload_src: str, namer: PreambleNamer, rng,
                 virtualize: bool = False, anti_tamper: int = 0,
                 junk: bool = False, junk_intensity: int = 1) -> str:
    """Wrap ``payload_src`` in one loader layer and return Lua source."""
    data = payload_src.encode("utf-8", "surrogatepass")
    comp = lzw_compress(data)
    seed = rng.randint(1, 250)
    step = rng.randint(1, 250)
    ciphered = cipher_encode(comp, seed, step)

    n_decomp = namer.new()
    n_pool = namer.new()
    n_dec = namer.new()
    n_comp = namer.new()
    n_src = namer.new()
    n_load = namer.new()
    n_sb = namer.new()
    n_sc = namer.new()

    b64 = base64.b64encode(ciphered).decode("ascii")
    pool_lit = '"' + b64 + '"'          # base64 text: compact and Lua-safe
    n_b64 = namer.new()
    n_raw = namer.new()
    parts = []
    if junk:
        parts.append(_loader_junk(namer, rng, 2 + junk_intensity * 2))
    parts += [
        f"local {n_sb}=string.byte;local {n_sc}=string.char;",
        _b64_decode_fn(n_b64),
        _decomp_fn(n_decomp, n_sb, n_sc),
        f"local {n_pool}={pool_lit};",
        f"local {n_load}=loadstring or load;",
    ]
    if junk:
        parts.append(_loader_junk(namer, rng, 1 + junk_intensity))

    # Which seed expression the decode uses.
    if anti_tamper >= 2:
        # Self-correcting: the runtime checksum of the base64 pool text must
        # match the embedded one or the derived seed is wrong and the payload
        # decodes to garbage.
        n_chk = namer.new()
        n_ds = namer.new()
        exp_chk = sum(b64.encode("ascii")) % 16777216
        seed_expr = n_ds
        pre_decode = (
            f"local {n_chk}=0;"
            f"for j=1,#{n_pool} do {n_chk}=({n_chk}+{n_sb}({n_pool},j))%16777216 end;"
            f"local {n_ds}={seed}+({n_chk}-{exp_chk});"
        )
    else:
        seed_expr = str(seed)
        pre_decode = ""

    decode = (
        f"local {n_raw}={n_b64}({n_pool});"
        f"{pre_decode}"
        f"local {n_dec}={{}};"
        f"for j=1,#{n_raw} do "
        f"{n_dec}[j]={n_sc}(({n_sb}({n_raw},j)-((({seed_expr})+j*{step})%256))%256);"
        f"end;"
        f"local {n_comp}=table.concat({n_dec});"
        f"local {n_src}={n_decomp}({n_comp});"
    )

    if anti_tamper >= 1:
        n_h = namer.new()
        exp_hash = _payload_hash(data)
        check = (
            f"local {n_h}=0;"
            f"for j=1,#{n_src} do {n_h}=({n_h}*31+{n_sb}({n_src},j))%4294967296 end;"
            f"if {n_h}~={exp_hash} then return end;"
            f"if {n_sb}(\"A\")~=65 or {n_sc}(65)~=\"A\" then return end;"
        )
    else:
        check = ""

    run = f"return {n_load}({n_src})(...);"
    decode_and_run = decode + check + run

    if virtualize:
        parts.append(_virtual_wrapper(decode_and_run, namer))
    else:
        parts.append(decode_and_run)

    return "".join(parts)


def _virtual_wrapper(inner_body: str, namer: PreambleNamer) -> str:
    """Express the decode/run step as a tiny opcode-dispatched VM."""
    vm_ip = namer.new()
    vm_prog = namer.new()
    vm_thunk = namer.new()
    vm_pc = namer.new()
    return (
        f"local {vm_thunk}=function(...) {inner_body} end;"
        f"local {vm_prog}={{1}};"
        f"local {vm_pc}=1;"
        f"while true do "
        f"local {vm_ip}={vm_prog}[{vm_pc}];"
        f"if {vm_ip}==1 then return {vm_thunk}(...);"
        f"else break end;"
        f"end;"
    )


def pack(payload_src: str, rng, layers: int = 1, virtualize: bool = False,
         anti_tamper: int = 0, junk: bool = False, junk_intensity: int = 1,
         seed: int | None = None) -> str:
    """Pack ``payload_src`` through ``layers`` nested loaders."""
    namer = PreambleNamer(seed)
    src = payload_src
    layers = max(1, layers)
    for depth in range(layers):
        use_vm = virtualize and depth == layers - 1
        src = build_loader(src, namer, rng, virtualize=use_vm,
                           anti_tamper=anti_tamper, junk=junk,
                           junk_intensity=junk_intensity)
    return src
