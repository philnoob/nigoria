"""Emit a polymorphic VM interpreter over an encrypted flat bytecode stream.

Every build randomizes:
  * opcode numbers (via OpMap) — the bytecode of one build is meaningless to a
    devirtualizer written for another,
  * all interpreter identifier names,
  * the order of dispatch arms,
  * the constant-pool cipher parameters and the bytecode-stream cipher.

The program itself is serialized to a flat, length-prefixed, ciphered byte
stream (no nested Lua tables), which also removes the parser/'too many
constants' limits, so large scripts virtualize too.
"""

from __future__ import annotations

import random
from string import Template

from .compiler import Unsupported
from .opcodes import OpMap


class _Namer:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.used: set[str] = set()

    def new(self) -> str:
        head = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
        body = head + "0123456789"
        while True:
            name = self.rng.choice(head) + "".join(
                self.rng.choice(body) for _ in range(self.rng.randint(6, 13)))
            if name not in self.used:
                self.used.add(name)
                return name


# --- flat serialization -----------------------------------------------------
def _varint(n: int, out: bytearray) -> None:
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break


def _serialize(value, ops: OpMap, out: bytearray) -> None:
    if value is None:
        out.append(ops["ST_nil"])
    elif isinstance(value, bool):
        # not used, but keep booleans out of the stream
        raise Unsupported("bool in program")
    elif isinstance(value, int):
        out.append(ops["ST_int"])
        _varint(value, out)
    elif isinstance(value, list):
        out.append(ops["ST_arr"])
        _varint(len(value), out)
        for e in value:
            _serialize(e, ops, out)
    else:
        raise Unsupported(f"cannot serialize {type(value).__name__}")


def _byte_literal(data: bytes) -> str:
    return '"' + "".join("\\%03d" % b for b in data) + '"'


def _encrypt_pool(consts, a, b, c):
    concatenated = bytearray()
    offsets = []
    for i, s in enumerate(consts, start=1):
        raw = s.encode("utf-8", "surrogatepass")
        off = len(concatenated) + 1
        for j, byte in enumerate(raw, start=1):
            concatenated.append((byte + (i * a + j * b + c)) % 256)
        offsets.append((off, len(raw)))
    idx = []
    for off, length in offsets:
        idx += [off, length]
    return _byte_literal(bytes(concatenated)), "{" + ",".join(map(str, idx)) + "}"


# --- interpreter generation -------------------------------------------------
_HEADER = Template(r"""
local $ENV=(getfenv and getfenv(0)) or _ENV or _G
local $unpack=table.unpack or unpack
local $pack=function(...) return {n=select('#',...),...} end
local $sbyte,$schar,$concat=string.byte,string.char,table.concat
local $tonum=tonumber
local $NIL={}
local $POOL=__POOL__
local $IDX=__IDX__
local $K={}
for i=1,#$IDX/2 do
  local o=$IDX[i*2-1] local n=$IDX[i*2] local b={}
  for j=1,n do b[j]=$schar(($sbyte($POOL,o+j-1)-((i*$pa+j*$pb+$pc)%256))%256) end
  $K[i]=$concat(b)
end
local $C=__STREAM__
local $S do local t={} for i=1,#$C do t[i]=$schar(($sbyte($C,i)-(($cs+i*$ct)%256))%256) end $S=$concat(t) end
local $pos=1
local function $rb() local x=$sbyte($S,$pos) $pos=$pos+1 return x end
local function $rv() local sh=0 local r=0 while true do local x=$rb() r=r+(x%128)*(2^sh) if x<128 then break end sh=sh+7 end return r end
local $readval
$readval=function() local t=$rb() if t==$stint then return $rv() elseif t==$stnil then return nil else local n=$rv() local a={} for i=1,n do a[i]=$readval() end return a end end
local $PROG=$readval()
local $CH=$PROG[1]
local $PROTOS=$PROG[2]
local $evalexpr,$evalmulti,$evallist,$runstmts,$makeclosure,$assign,$ismulti,$runblock
local function $newframe(p) return {[0]=p} end
local function $getlocal($env,id) local f=$env while f do local v=f[id] if v~=nil then if v==$NIL then return nil end return v end f=f[0] end end
local function $declare($env,id,v) $env[id]=(v==nil) and $NIL or v end
local function $setex($env,id,v) local f=$env while f do if f[id]~=nil then f[id]=(v==nil) and $NIL or v return end f=f[0] end $env[id]=(v==nil) and $NIL or v end
$ismulti=function(nd) local o=nd[1] return o==$op_ECALL or o==$op_EMETHOD or o==$op_EVARARG end
$makeclosure=function(pi,defenv)
  local proto=$PROTOS[pi] local np=proto[1] local va=proto[2] local params=proto[3] local body=proto[4]
  return function(...)
    local fenv=$newframe(defenv)
    for i=1,np do $declare(fenv,params[i],(select(i,...))) end
    local vt if va==1 then vt=$pack(select(np+1,...)) else vt={n=0} end
    local s=$runstmts(body,fenv,vt)
    if s and s[1]==$sret then return $unpack(s[2],1,s[2].n) end
  end
end
$evalmulti=function($nd,$env,$va)
  local op=$nd[1]
  if op==$op_ECALL then local f=$evalexpr($nd[2],$env,$va) local a=$evallist($nd[3],$env,$va) return $pack(f($unpack(a,1,a.n)))
  elseif op==$op_EMETHOD then local ob=$evalexpr($nd[2],$env,$va) local m=ob[$K[$nd[3]]] local a=$evallist($nd[4],$env,$va) return $pack(m(ob,$unpack(a,1,a.n)))
  elseif op==$op_EVARARG then return $va
  else return $pack($evalexpr($nd,$env,$va)) end
end
$evallist=function(nodes,$env,$va)
  local out,n={},0 local cnt=#nodes
  for i=1,cnt do local nd=nodes[i]
    if i==cnt and $ismulti(nd) then local r=$evalmulti(nd,$env,$va) for k=1,r.n do n=n+1 out[n]=r[k] end
    else n=n+1 out[n]=$evalexpr(nd,$env,$va) end
  end
  out.n=n return out
end
$assign=function(tg,val,$env,$va)
  local op=tg[1]
  if op==$op_ELOCAL then $setex($env,tg[2],val)
  elseif op==$op_EGLOBAL then $ENV[$K[tg[2]]]=val
  else $evalexpr(tg[2],$env,$va)[$evalexpr(tg[3],$env,$va)]=val end
end
$runblock=function(block,$env,$va) return $runstmts(block,$newframe($env),$va) end
""")

_ENTRY = Template("local $top=$newframe(nil) return $makeclosure($CH,$top)(...)\n")


def _emit_evalexpr(M, rng):
    arms = [
        (["EK"], "return $K[$nd[2]]"),
        (["EKNUM"], "return $tonum($K[$nd[2]])"),
        (["ENIL"], "return nil"),
        (["ETRUE"], "return true"),
        (["EFALSE"], "return false"),
        (["EVARARG"], "return $va[1]"),
        (["ELOCAL"], "return $getlocal($env,$nd[2])"),
        (["EGLOBAL"], "return $ENV[$K[$nd[2]]]"),
        (["EINDEX"], "return $evalexpr($nd[2],$env,$va)[$evalexpr($nd[3],$env,$va)]"),
        (["EPAREN"], "return $evalexpr($nd[2],$env,$va)"),
        (["ECALL", "EMETHOD"], "local r=$evalmulti($nd,$env,$va) return r[1]"),
        (["EFUNC"], "return $makeclosure($nd[2],$env)"),
        (["EUN"], _UN_BODY),
        (["EBIN"], _emit_bin(M, rng)),
        (["ETABLE"], _TABLE_BODY),
    ]
    rng.shuffle(arms)
    return _dispatch("$evalexpr=function($nd,$env,$va)\nlocal op=$nd[1]\n", arms, M)


_UN_BODY = ("local a=$evalexpr($nd[3],$env,$va) local u=$nd[2] "
            "if u==$op_U_neg then return -a elseif u==$op_U_not then return not a "
            "elseif u==$op_U_len then return #a else return ~a end")

_TABLE_BODY = (
    "local t={} local arr=0 local fs=$nd[2] "
    "for i=1,#fs do local f=fs[i] "
    "if f[1]==$op_T_arr then "
    "if i==#fs and $ismulti(f[2]) then local r=$evalmulti(f[2],$env,$va) "
    "for k=1,r.n do arr=arr+1 t[arr]=r[k] end "
    "else arr=arr+1 t[arr]=$evalexpr(f[2],$env,$va) end "
    "else t[$evalexpr(f[2],$env,$va)]=$evalexpr(f[3],$env,$va) end end return t")


def _emit_bin(M, rng):
    ops = [
        ("B_add", "return l+r"), ("B_sub", "return l-r"), ("B_mul", "return l*r"),
        ("B_div", "return l/r"), ("B_mod", "return l%r"), ("B_pow", "return l^r"),
        ("B_idiv", "return l//r"), ("B_concat", "return l..r"),
        ("B_eq", "return l==r"), ("B_ne", "return l~=r"), ("B_lt", "return l<r"),
        ("B_le", "return l<=r"), ("B_gt", "return l>r"), ("B_ge", "return l>=r"),
        ("B_band", "return l&r"), ("B_bor", "return l|r"), ("B_bxor", "return l~r"),
        ("B_shl", "return l<<r"), ("B_shr", "return l>>r"),
    ]
    rng.shuffle(ops)
    parts = ["local b=$nd[2] "
             "if b==$op_B_and then local l=$evalexpr($nd[3],$env,$va) "
             "if not l then return l end return $evalexpr($nd[4],$env,$va) "
             "elseif b==$op_B_or then local l=$evalexpr($nd[3],$env,$va) "
             "if l then return l end return $evalexpr($nd[4],$env,$va) end "
             "local l=$evalexpr($nd[3],$env,$va) local r=$evalexpr($nd[4],$env,$va) "]
    for i, (name, body) in enumerate(ops):
        kw = "if" if i == 0 else "elseif"
        if i == len(ops) - 1:
            parts.append(f"else {body} end")
        else:
            parts.append(f"{kw} b==$op_{name} then {body} ")
    return "".join(parts)


def _emit_runstmts(M, rng):
    arms = [
        (["SLOCAL"], "local vals=$evallist(st[3],$env,$va) local ids=st[2] "
                     "for k=1,#ids do $declare($env,ids[k],vals[k]) end"),
        (["SASSIGN"], "local vals=$evallist(st[3],$env,$va) local tg=st[2] "
                      "for k=1,#tg do $assign(tg[k],vals[k],$env,$va) end"),
        (["SCALL"], "$evalmulti(st[2],$env,$va)"),
        (["SDO"], "sig=$runblock(st[2],$env,$va)"),
        (["SIF"], "local dn=false for c=1,#st[2] do local cl=st[2][c] "
                  "if $evalexpr(cl[1],$env,$va) then sig=$runblock(cl[2],$env,$va) "
                  "dn=true break end end if not dn and st[3] then "
                  "sig=$runblock(st[3],$env,$va) end"),
        (["SWHILE"], "while $evalexpr(st[2],$env,$va) do "
                     "local s=$runblock(st[3],$env,$va) if s then "
                     "if s[1]==$sbrk then break elseif s[1]~=$scont then sig=s break end end end"),
        (["SREPEAT"], "repeat local fr=$newframe($env) local s=$runstmts(st[2],fr,$va) "
                      "if s then if s[1]==$sbrk then break elseif s[1]==$sret then sig=s break end end "
                      "until $evalexpr(st[3],fr,$va)"),
        (["SNUMFOR"], "local a=$evalexpr(st[3],$env,$va) local b=$evalexpr(st[4],$env,$va) "
                      "local stp=1 if st[5]~=nil then stp=$evalexpr(st[5],$env,$va) end "
                      "for i=a,b,stp do local fr=$newframe($env) $declare(fr,st[2],i) "
                      "local s=$runstmts(st[6],fr,$va) if s then "
                      "if s[1]==$sbrk then break elseif s[1]~=$scont then sig=s break end end end"),
        (["SGENFOR"], "local it=$evallist(st[3],$env,$va) local f,s0,ct=it[1],it[2],it[3] "
                      "while true do local r=$pack(f(s0,ct)) if r[1]==nil then break end ct=r[1] "
                      "local fr=$newframe($env) local ids=st[2] "
                      "for k=1,#ids do $declare(fr,ids[k],r[k]) end "
                      "local s=$runstmts(st[4],fr,$va) if s then "
                      "if s[1]==$sbrk then break elseif s[1]~=$scont then sig=s break end end end"),
        (["SRETURN"], "sig={$sret,$evallist(st[2],$env,$va)}"),
        (["SBREAK"], "sig={$sbrk}"),
        (["SCONTINUE"], "sig={$scont}"),
    ]
    rng.shuffle(arms)
    head = ("$runstmts=function(block,$env,$va)\n"
            "for i=1,#block do local st=block[i] local op=st[1] local sig\n")
    body = _dispatch_stmt(head, arms)
    return body


def _dispatch(head, arms, M):
    parts = [head]
    for i, (syms, body) in enumerate(arms):
        guard = " or ".join(f"op==$op_{s}" for s in syms)
        kw = "if" if i == 0 else "elseif"
        parts.append(f"{kw} {guard} then {body}\n")
    parts.append("end\nend\n")
    return "".join(parts)


def _dispatch_stmt(head, arms):
    parts = [head]
    for i, (syms, body) in enumerate(arms):
        guard = " or ".join(f"op==$op_{s}" for s in syms)
        kw = "if" if i == 0 else "elseif"
        parts.append(f"{kw} {guard} then {body}\n")
    parts.append("end\nif sig then return sig end\nend\nend\n")
    return "".join(parts)


def emit_vm(program: dict, seed: int | None = None) -> str:
    rng = random.Random(seed)
    ops = program["_ops"]
    namer = _Namer(rng)

    # names
    names = [
        "ENV", "unpack", "pack", "sbyte", "schar", "concat", "tonum", "NIL",
        "POOL", "IDX", "K", "C", "S", "pos", "rb", "rv", "readval", "PROG",
        "CH", "PROTOS", "evalexpr", "evalmulti", "evallist", "runstmts",
        "makeclosure", "assign", "ismulti", "runblock", "newframe", "getlocal",
        "declare", "setex", "nd", "env", "va", "top",
    ]
    M = {n: namer.new() for n in names}
    # opcode numbers
    for sym in ops.map:
        M["op_" + sym] = str(ops[sym])
    M["stint"] = str(ops["ST_int"])
    M["stnil"] = str(ops["ST_nil"])
    M["starr"] = str(ops["ST_arr"])
    # signal kinds (internal, still randomized)
    sig_vals = rng.sample(range(1, 200), 3)
    M["sret"], M["sbrk"], M["scont"] = map(str, sig_vals)
    # cipher params
    M["pa"], M["pb"], M["pc"] = str(rng.randint(3, 250)), str(rng.randint(3, 250)), str(rng.randint(3, 250))
    M["cs"], M["ct"] = str(rng.randint(1, 250)), str(rng.randint(1, 250))

    # pool (uses pa/pb/pc)
    pool_lit, idx_lit = _encrypt_pool(program["consts"], int(M["pa"]),
                                      int(M["pb"]), int(M["pc"]))

    # serialize program -> [chunk, protos-as-arrays]
    protos = [[p["np"], p["va"], p["params"], p["body"]] for p in program["protos"]]
    prog_struct = [program["chunk"], protos]
    raw = bytearray()
    _serialize(prog_struct, ops, raw)
    # cipher the stream
    cs, ct = int(M["cs"]), int(M["ct"])
    ciph = bytes((b + (cs + (i + 1) * ct)) % 256 for i, b in enumerate(raw))
    stream_lit = _byte_literal(ciph)

    # assemble raw templates (all still carry $ placeholders), then substitute
    # once so every name/opcode is consistent.
    raw_body = (_HEADER.template
                + _emit_evalexpr(M, rng)
                + _emit_runstmts(M, rng)
                + _ENTRY.template)
    body = Template(raw_body).substitute(M)
    body = (body.replace("__POOL__", pool_lit)
                .replace("__IDX__", idx_lit)
                .replace("__STREAM__", stream_lit))
    return body
