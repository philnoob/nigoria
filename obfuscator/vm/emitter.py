"""Emit a polymorphic VM interpreter over an encrypted flat bytecode stream.

Per build, everything is randomized: opcode numbers (with several aliases per
operation), all identifiers, dispatch-arm order, cipher parameters, AND the
interpreter architecture itself — either an if/elseif dispatch chain or a table
of handler closures. Decoy handlers for unused opcode numbers add noise. The
program is a ciphered, length-prefixed flat byte stream (no readable tables).
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
        out.append(b | 0x80 if n else b)
        if not n:
            break


def _serialize(value, ops: OpMap, out: bytearray) -> None:
    if value is None:
        out.append(ops["ST_nil"])
    elif isinstance(value, bool):
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


def _guard(var: str, nums) -> str:
    return " or ".join(f"{var}=={n}" for n in nums)


# --- static runtime scaffolding --------------------------------------------
_SCAFFOLD = Template(r"""
local $ENV=(getfenv and getfenv(0)) or _ENV or _G
local $unpack=table.unpack or unpack
local $pack=function(...) return {n=select('#',...),...} end
local $sbyte,$schar,$concat=string.byte,string.char,table.concat
local $tonum=tonumber
local $mfloor=math.floor
local $NIL={}
local $P2={} for i=0,32 do $P2[i]=2^i end
local function $tou(a) return a%4294967296 end
local function $lshift(a,n) if n<0 or n>=32 then return 0 end return ($tou(a)%$P2[32-n])*$P2[n] end
local function $rshift(a,n) if n<0 or n>=32 then return 0 end return $mfloor($tou(a)/$P2[n]) end
local function $bnot(a) return 4294967295-$tou(a) end
local function $band(a,b) a=$tou(a) b=$tou(b) local r,p=0,1 for _=1,32 do local x,y=a%2,b%2 if x+y==2 then r=r+p end a=(a-x)/2 b=(b-y)/2 p=p*2 end return r end
local function $bor(a,b) a=$tou(a) b=$tou(b) local r,p=0,1 for _=1,32 do local x,y=a%2,b%2 if x+y>=1 then r=r+p end a=(a-x)/2 b=(b-y)/2 p=p*2 end return r end
local function $bxor(a,b) a=$tou(a) b=$tou(b) local r,p=0,1 for _=1,32 do local x,y=a%2,b%2 if x~=y then r=r+p end a=(a-x)/2 b=(b-y)/2 p=p*2 end return r end
local $POOL=__POOL__
local $IDX=__IDX__
local $K={}
for i=1,#$IDX/2 do
  local o=$IDX[i*2-1] local n=$IDX[i*2] local b={}
  for j=1,n do b[j]=$schar(($sbyte($POOL,o+j-1)-((i*$pa+j*$pb+$pc)%256))%256) end
  $K[i]=$concat(b)
end
local $C=__STREAM__
local $chk=0 for i=1,#$C do $chk=($chk+$sbyte($C,i))%16777216 end
local $ds=$cs+($chk-__EXPCHK__)
local $S do local t={} for i=1,#$C do t[i]=$schar(($sbyte($C,i)-(($ds+i*$ct)%256))%256) end $S=$concat(t) end
local $pos=1
local function $rb() local x=$sbyte($S,$pos) $pos=$pos+1 return x end
local function $rv() local sh=0 local r=0 while true do local x=$rb() r=r+(x%128)*(2^sh) if x<128 then break end sh=sh+7 end return r end
local $readval
$readval=function() local t=$rb() if t==$stint then return $rv() elseif t==$stnil then return nil else local n=$rv() local a={} for i=1,n do a[i]=$readval() end return a end end
local $PROG=$readval()
local $CH=$PROG[1]
local $PROTOS=$PROG[2]
local $evalexpr,$evalmulti,$evallist,$runstmts,$makeclosure,$assign,$ismulti,$runblock,$EH,$SH
local function $newframe(p) return {[0]=p} end
local function $getlocal($env,id) local f=$env while f do local v=f[id] if v~=nil then if v==$NIL then return nil end return v end f=f[0] end end
local function $declare($env,id,v) $env[id]=(v==nil) and $NIL or v end
local function $setex($env,id,v) local f=$env while f do if f[id]~=nil then f[id]=(v==nil) and $NIL or v return end f=f[0] end $env[id]=(v==nil) and $NIL or v end
$evallist=function(nodes,$env,$va)
  local out,n={},0 local cnt=#nodes
  for i=1,cnt do local nd=nodes[i]
    if i==cnt and $ismulti(nd) then local r=$evalmulti(nd,$env,$va) for k=1,r.n do n=n+1 out[n]=r[k] end
    else n=n+1 out[n]=$evalexpr(nd,$env,$va) end
  end
  out.n=n return out
end
$runblock=function(block,$env,$va) return $runstmts(block,$newframe($env),$va) end
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
""")

_ENTRY = Template("local $top=$newframe(nil) return $makeclosure($CH,$top)(...)\n")
# Gated entry: only run the real program inside a genuine Roblox environment
# (typeof(game)=="Instance"). In a dumper's plain-Lua / emulated sandbox this
# is false, so the payload never executes and a run/trace-based dumper sees
# nothing. Real executors always provide game as an Instance.
_ENTRY_GATED = Template(
    "local $top=$newframe(nil) "
    "if typeof and typeof(game)==\"Instance\" then "
    "return $makeclosure($CH,$top)(...) end\n")


# --- arm bodies (shared by both architectures) -----------------------------
def _expr_arms(ops):
    return [
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
        (["EUN"], _un_body(ops)),
        (["EBIN"], _bin_body(ops)),
        (["ETABLE"], _table_body(ops)),
    ]


def _un_body(ops):
    return ("local a=$evalexpr($nd[3],$env,$va) local u=$nd[2] "
            f"if {_guard('u', ops.all('U_neg'))} then return -a "
            f"elseif {_guard('u', ops.all('U_not'))} then return not a "
            f"elseif {_guard('u', ops.all('U_len'))} then return #a "
            "else return $bnot(a) end")


def _table_body(ops):
    g = _guard("f[1]", ops.all("T_arr"))
    return (
        "local t={} local arr=0 local fs=$nd[2] "
        "for i=1,#fs do local f=fs[i] "
        f"if {g} then "
        "if i==#fs and $ismulti(f[2]) then local r=$evalmulti(f[2],$env,$va) "
        "for k=1,r.n do arr=arr+1 t[arr]=r[k] end "
        "else arr=arr+1 t[arr]=$evalexpr(f[2],$env,$va) end "
        "else t[$evalexpr(f[2],$env,$va)]=$evalexpr(f[3],$env,$va) end end return t")


def _bin_body(ops):
    arith = [
        ("B_add", "return l+r"), ("B_sub", "return l-r"), ("B_mul", "return l*r"),
        ("B_div", "return l/r"), ("B_mod", "return l%r"), ("B_pow", "return l^r"),
        ("B_idiv", "return $mfloor(l/r)"), ("B_concat", "return l..r"),
        ("B_eq", "return l==r"), ("B_ne", "return l~=r"), ("B_lt", "return l<r"),
        ("B_le", "return l<=r"), ("B_gt", "return l>r"), ("B_ge", "return l>=r"),
        ("B_band", "return $band(l,r)"), ("B_bor", "return $bor(l,r)"), ("B_bxor", "return $bxor(l,r)"),
        ("B_shl", "return $lshift(l,r)"), ("B_shr", "return $rshift(l,r)"),
    ]
    parts = [
        "local b=$nd[2] ",
        f"if {_guard('b', ops.all('B_and'))} then local l=$evalexpr($nd[3],$env,$va) "
        "if not l then return l end return $evalexpr($nd[4],$env,$va) ",
        f"elseif {_guard('b', ops.all('B_or'))} then local l=$evalexpr($nd[3],$env,$va) "
        "if l then return l end return $evalexpr($nd[4],$env,$va) end ",
        "local l=$evalexpr($nd[3],$env,$va) local r=$evalexpr($nd[4],$env,$va) ",
    ]
    for i, (name, body) in enumerate(arith):
        if i == len(arith) - 1:
            parts.append(f"else {body} end")
        elif i == 0:
            parts.append(f"if {_guard('b', ops.all(name))} then {body} ")
        else:
            parts.append(f"elseif {_guard('b', ops.all(name))} then {body} ")
    return "".join(parts)


def _stmt_arms():
    return [
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


def _multi_and_assign(ops):
    ev = ("$evalmulti=function($nd,$env,$va) local op=$nd[1] "
          f"if {_guard('op', ops.all('ECALL'))} then local f=$evalexpr($nd[2],$env,$va) "
          "local a=$evallist($nd[3],$env,$va) return $pack(f($unpack(a,1,a.n))) "
          f"elseif {_guard('op', ops.all('EMETHOD'))} then local ob=$evalexpr($nd[2],$env,$va) "
          "local m=ob[$K[$nd[3]]] local a=$evallist($nd[4],$env,$va) return $pack(m(ob,$unpack(a,1,a.n))) "
          f"elseif {_guard('op', ops.all('EVARARG'))} then return $va "
          "else return $pack($evalexpr($nd,$env,$va)) end end\n")
    im = ("$ismulti=function(nd) local o=nd[1] return "
          + _guard("o", ops.all("ECALL") + ops.all("EMETHOD") + ops.all("EVARARG"))
          + " end\n")
    asg = ("$assign=function(tg,val,$env,$va) local op=tg[1] "
           f"if {_guard('op', ops.all('ELOCAL'))} then $setex($env,tg[2],val) "
           f"elseif {_guard('op', ops.all('EGLOBAL'))} then $ENV[$K[tg[2]]]=val "
           "else $evalexpr(tg[2],$env,$va)[$evalexpr(tg[3],$env,$va)]=val end end\n")
    return im + ev + asg


# --- architecture A: if/elseif dispatch ------------------------------------
def _archA_expr(arms, ops, rng):
    rng.shuffle(arms)
    parts = ["$evalexpr=function($nd,$env,$va)\nlocal op=$nd[1]\n"]
    for i, (syms, body) in enumerate(arms):
        nums = [n for s in syms for n in ops.all(s)]
        kw = "if" if i == 0 else "elseif"
        parts.append(f"{kw} {_guard('op', nums)} then {body}\n")
    for fn in ops.free[:4]:
        parts.append(f"elseif op=={fn} then return nil\n")
    parts.append("end\nend\n")
    return "".join(parts)


def _archA_stmt(arms, ops, rng):
    rng.shuffle(arms)
    parts = ["$runstmts=function(block,$env,$va)\n"
             "for i=1,#block do local st=block[i] local op=st[1] local sig\n"]
    for i, (syms, body) in enumerate(arms):
        nums = [n for s in syms for n in ops.all(s)]
        kw = "if" if i == 0 else "elseif"
        parts.append(f"{kw} {_guard('op', nums)} then {body}\n")
    for fn in ops.free[4:7]:
        parts.append(f"elseif op=={fn} then local _z=1\n")
    parts.append("end\nif sig then return sig end\nend\nend\n")
    return "".join(parts)


# --- architecture B: handler-table dispatch --------------------------------
def _archB_expr(arms, ops, rng, namer):
    rng.shuffle(arms)
    lines = ["$EH={}\n"]
    for syms, body in arms:
        h = namer.new()
        lines.append(f"local {h}=function($nd,$env,$va) {body} end\n")
        for s in syms:
            for n in ops.all(s):
                lines.append(f"$EH[{n}]={h}\n")
    for fn in ops.free[:4]:
        h = namer.new()
        lines.append(f"local {h}=function($nd,$env,$va) return $nd end\n")
        lines.append(f"$EH[{fn}]={h}\n")
    lines.append("$evalexpr=function($nd,$env,$va) return $EH[$nd[1]]($nd,$env,$va) end\n")
    return "".join(lines)


def _archB_stmt(arms, ops, rng, namer):
    rng.shuffle(arms)
    lines = ["$SH={}\n"]
    for syms, body in arms:
        h = namer.new()
        lines.append(f"local {h}=function(st,$env,$va) local sig {body} return sig end\n")
        for s in syms:
            for n in ops.all(s):
                lines.append(f"$SH[{n}]={h}\n")
    for fn in ops.free[4:7]:
        h = namer.new()
        lines.append(f"local {h}=function(st,$env,$va) local _z=1 end\n")
        lines.append(f"$SH[{fn}]={h}\n")
    lines.append("$runstmts=function(block,$env,$va) for i=1,#block do "
                 "local st=block[i] local sig=$SH[st[1]](st,$env,$va) "
                 "if sig then return sig end end end\n")
    return "".join(lines)


def emit_vm(program: dict, seed: int | None = None,
            anti_sandbox: bool = False) -> str:
    rng = random.Random(seed)
    ops = program["_ops"]
    namer = _Namer(rng)

    names = [
        "ENV", "unpack", "pack", "sbyte", "schar", "concat", "tonum", "NIL",
        "POOL", "IDX", "K", "C", "S", "pos", "rb", "rv", "readval", "PROG",
        "CH", "PROTOS", "evalexpr", "evalmulti", "evallist", "runstmts",
        "makeclosure", "assign", "ismulti", "runblock", "newframe", "getlocal",
        "declare", "setex", "nd", "env", "va", "top", "EH", "SH",
        "mfloor", "P2", "tou", "band", "bor", "bxor", "lshift", "rshift", "bnot",
        "ds", "chk",
    ]
    M = {n: namer.new() for n in names}
    M["stint"], M["starr"], M["stnil"] = (
        str(ops["ST_int"]), str(ops["ST_arr"]), str(ops["ST_nil"]))
    sret, sbrk, scont = rng.sample(range(1, 400), 3)
    M["sret"], M["sbrk"], M["scont"] = str(sret), str(sbrk), str(scont)
    M["pa"], M["pb"], M["pc"] = (str(rng.randint(3, 250)), str(rng.randint(3, 250)),
                                 str(rng.randint(3, 250)))
    M["cs"], M["ct"] = str(rng.randint(1, 250)), str(rng.randint(1, 250))

    pool_lit, idx_lit = _encrypt_pool(program["consts"], int(M["pa"]),
                                      int(M["pb"]), int(M["pc"]))

    protos = [[p["np"], p["va"], p["params"], p["body"]] for p in program["protos"]]
    raw = bytearray()
    _serialize([program["chunk"], protos], ops, raw)
    cs, ct = int(M["cs"]), int(M["ct"])
    ciph = bytes((b + (cs + (i + 1) * ct)) % 256 for i, b in enumerate(raw))
    stream_lit = _byte_literal(ciph)
    expchk = sum(ciph) % 16777216

    expr_arms = _expr_arms(ops)
    stmt_arms = _stmt_arms()
    arch_b = rng.random() < 0.5
    if arch_b:
        expr_code = _archB_expr(expr_arms, ops, rng, namer)
        stmt_code = _archB_stmt(stmt_arms, ops, rng, namer)
    else:
        expr_code = _archA_expr(expr_arms, ops, rng)
        stmt_code = _archA_stmt(stmt_arms, ops, rng)

    raw_body = (_SCAFFOLD.template
                + _multi_and_assign(ops)
                + expr_code + stmt_code
                + (_ENTRY_GATED if anti_sandbox else _ENTRY).template)
    body = Template(raw_body).substitute(M)
    body = (body.replace("__POOL__", pool_lit)
                .replace("__IDX__", idx_lit)
                .replace("__STREAM__", stream_lit)
                .replace("__EXPCHK__", str(expchk)))
    return body
