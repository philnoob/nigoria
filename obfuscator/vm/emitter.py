"""Serialize a compiled VM program to Lua: interpreter + program table + pool.

The emitted chunk is self-contained and, when run, executes the original
program. It is designed to be fed through the normal packer afterwards, so the
final artifact only exposes a generic interpreter over an opaque table.

Because the program is emitted as nested Lua table literals, very large or very
deep programs can exceed Lua's parser/'too many constants' limits. The emitter
tracks depth and size and raises Unsupported past safe bounds so the pipeline
falls back to the plain-transform path.
"""

from __future__ import annotations

from .compiler import RawNum, Unsupported

MAX_DEPTH = 140
MAX_NODES = 60000


class _Ser:
    def __init__(self):
        self.nodes = 0
        self.max_depth = 0

    def ser(self, value, depth=0) -> str:
        self.nodes += 1
        if depth > self.max_depth:
            self.max_depth = depth
        if self.nodes > MAX_NODES or depth > MAX_DEPTH:
            raise Unsupported("program too large/deep for VM")
        if isinstance(value, RawNum):
            return value.text
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return "nil"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, list):
            return "{" + ",".join(self.ser(v, depth + 1) for v in value) + "}"
        raise Unsupported(f"cannot serialize {type(value).__name__}")


def _proto_literal(proto: dict, ser: _Ser) -> str:
    params = "{" + ",".join(str(p) for p in proto["params"]) + "}"
    body = ser.ser(proto["body"], 1)
    return (f"{{np={proto['np']},va={proto['va']},"
            f"params={params},body={body}}}")


def _encrypt_pool(consts: list[str]):
    concatenated = bytearray()
    offsets = []
    for i, s in enumerate(consts, start=1):
        raw = s.encode("utf-8", "surrogatepass")
        off = len(concatenated) + 1
        for j, b in enumerate(raw, start=1):
            concatenated.append((b + (i * 7 + j * 3 + 0x5A)) % 256)
        offsets.append((off, len(raw)))
    pool_lit = '"' + "".join("\\%03d" % b for b in concatenated) + '"'
    idx = []
    for off, length in offsets:
        idx += [off, length]
    idx_lit = "{" + ",".join(str(x) for x in idx) + "}"
    return pool_lit, idx_lit


# The interpreter. Names are local to this chunk so they cannot collide; the
# whole thing is compressed/packed afterwards.
_INTERP = r"""
local ENV=(getfenv and getfenv(0)) or _ENV or _G
local unpack=table.unpack or unpack
local sbyte,schar,concat=string.byte,string.char,table.concat
local function pack(...) return {n=select('#',...),...} end
local NIL={}
local POOL=%(pool)s
local IDX=%(idx)s
local K={}
for i=1,#IDX/2 do
  local o=IDX[i*2-1];local n=IDX[i*2];local b={}
  for j=1,n do b[j]=schar((sbyte(POOL,o+j-1)-((i*7+j*3+90)%%256))%%256) end
  K[i]=concat(b)
end
local PROTOS=%(protos)s
local evalexpr,evalmulti,evallist,runstmts,makeclosure,assign
local function newframe(p) return {__p=p} end
local function getlocal(env,id)
  local f=env
  while f do local v=f[id]; if v~=nil then if v==NIL then return nil end return v end f=f.__p end
end
local function declare(env,id,v) env[id]=(v==nil) and NIL or v end
local function setexisting(env,id,v)
  local f=env
  while f do if f[id]~=nil then f[id]=(v==nil) and NIL or v return end f=f.__p end
  env[id]=(v==nil) and NIL or v
end
local function ismulti(nd) local o=nd[1] return o==9 or o==10 or o==5 end
function makeclosure(pi,defenv)
  local proto=PROTOS[pi]
  return function(...)
    local fenv=newframe(defenv)
    local np=proto.np
    for i=1,np do declare(fenv,proto.params[i],(select(i,...))) end
    local va
    if proto.va==1 then va=pack(select(np+1,...)) else va={n=0} end
    local s=runstmts(proto.body,fenv,va)
    if s and s.k==1 then return unpack(s.vals,1,s.vals.n) end
  end
end
function evalmulti(nd,env,va)
  local op=nd[1]
  if op==9 then
    local f=evalexpr(nd[2],env,va)
    local a=evallist(nd[3],env,va)
    return pack(f(unpack(a,1,a.n)))
  elseif op==10 then
    local obj=evalexpr(nd[2],env,va)
    local m=obj[K[nd[3]]]
    local a=evallist(nd[4],env,va)
    return pack(m(obj,unpack(a,1,a.n)))
  elseif op==5 then
    return va
  else
    return pack(evalexpr(nd,env,va))
  end
end
function evallist(nodes,env,va)
  local out,n={},0
  local cnt=#nodes
  for i=1,cnt do
    local nd=nodes[i]
    if i==cnt and ismulti(nd) then
      local r=evalmulti(nd,env,va)
      for k=1,r.n do n=n+1 out[n]=r[k] end
    else
      n=n+1 out[n]=evalexpr(nd,env,va)
    end
  end
  out.n=n return out
end
function evalexpr(nd,env,va)
  local op=nd[1]
  if op==1 then return K[nd[2]]
  elseif op==16 then return nd[2]
  elseif op==2 then return nil
  elseif op==3 then return true
  elseif op==4 then return false
  elseif op==5 then return va[1]
  elseif op==6 then return getlocal(env,nd[2])
  elseif op==7 then return ENV[K[nd[2]]]
  elseif op==8 then return evalexpr(nd[2],env,va)[evalexpr(nd[3],env,va)]
  elseif op==15 then return evalexpr(nd[2],env,va)
  elseif op==9 or op==10 then local r=evalmulti(nd,env,va) return r[1]
  elseif op==13 then return makeclosure(nd[2],env)
  elseif op==12 then
    local a=evalexpr(nd[3],env,va) local u=nd[2]
    if u==1 then return -a elseif u==2 then return not a elseif u==3 then return #a else return ~a end
  elseif op==11 then
    local b=nd[2]
    if b==15 then local l=evalexpr(nd[3],env,va) if not l then return l end return evalexpr(nd[4],env,va)
    elseif b==16 then local l=evalexpr(nd[3],env,va) if l then return l end return evalexpr(nd[4],env,va) end
    local l=evalexpr(nd[3],env,va) local r=evalexpr(nd[4],env,va)
    if b==1 then return l+r elseif b==2 then return l-r elseif b==3 then return l*r
    elseif b==4 then return l/r elseif b==5 then return l%%r elseif b==6 then return l^r
    elseif b==7 then return l//r elseif b==8 then return l..r elseif b==9 then return l==r
    elseif b==10 then return l~=r elseif b==11 then return l<r elseif b==12 then return l<=r
    elseif b==13 then return l>r elseif b==14 then return l>=r elseif b==17 then return l&r
    elseif b==18 then return l|r elseif b==19 then return l~r elseif b==20 then return l<<r
    else return l>>r end
  elseif op==14 then
    local t={} local arr=0 local fs=nd[2]
    for i=1,#fs do local f=fs[i]
      if f[1]==0 then
        if i==#fs and ismulti(f[2]) then
          local r=evalmulti(f[2],env,va)
          for k=1,r.n do arr=arr+1 t[arr]=r[k] end
        else arr=arr+1 t[arr]=evalexpr(f[2],env,va) end
      else t[evalexpr(f[2],env,va)]=evalexpr(f[3],env,va) end
    end
    return t
  end
end
function assign(tg,val,env,va)
  local op=tg[1]
  if op==6 then setexisting(env,tg[2],val)
  elseif op==7 then ENV[K[tg[2]]]=val
  else evalexpr(tg[2],env,va)[evalexpr(tg[3],env,va)]=val end
end
local function runblock(block,env,va) return runstmts(block,newframe(env),va) end
function runstmts(block,env,va)
  for i=1,#block do
    local st=block[i] local op=st[1] local sig
    if op==1 then
      local vals=evallist(st[3],env,va) local ids=st[2]
      for k=1,#ids do declare(env,ids[k],vals[k]) end
    elseif op==2 then
      local vals=evallist(st[3],env,va) local tg=st[2]
      for k=1,#tg do assign(tg[k],vals[k],env,va) end
    elseif op==3 then evalmulti(st[2],env,va)
    elseif op==12 then sig=runblock(st[2],env,va)
    elseif op==4 then
      local done=false
      for c=1,#st[2] do local cl=st[2][c]
        if evalexpr(cl[1],env,va) then sig=runblock(cl[2],env,va) done=true break end
      end
      if not done and st[3] then sig=runblock(st[3],env,va) end
    elseif op==5 then
      while evalexpr(st[2],env,va) do
        local s=runblock(st[3],env,va)
        if s then if s.k==2 then break elseif s.k~=3 then sig=s break end end
      end
    elseif op==6 then
      repeat
        local fr=newframe(env)
        local s=runstmts(st[2],fr,va)
        if s then if s.k==2 then break elseif s.k==1 then sig=s break end end
      until evalexpr(st[3],fr,va)
    elseif op==7 then
      local a=evalexpr(st[3],env,va) local b=evalexpr(st[4],env,va)
      local stp=1 if st[5]~=nil then stp=evalexpr(st[5],env,va) end
      for i=a,b,stp do
        local fr=newframe(env) declare(fr,st[2],i)
        local s=runstmts(st[6],fr,va)
        if s then if s.k==2 then break elseif s.k~=3 then sig=s break end end
      end
    elseif op==8 then
      local it=evallist(st[3],env,va)
      local f,s0,ctrl=it[1],it[2],it[3]
      while true do
        local r=pack(f(s0,ctrl))
        if r[1]==nil then break end
        ctrl=r[1]
        local fr=newframe(env) local ids=st[2]
        for k=1,#ids do declare(fr,ids[k],r[k]) end
        local s=runstmts(st[4],fr,va)
        if s then if s.k==2 then break elseif s.k~=3 then sig=s break end end
      end
    elseif op==9 then sig={k=1,vals=evallist(st[2],env,va)}
    elseif op==10 then sig={k=2}
    elseif op==11 then sig={k=3}
    end
    if sig then return sig end
  end
end
local top=newframe(nil)
return makeclosure(%(chunk)d,top)(...)
"""


def emit_vm(program: dict) -> str:
    ser = _Ser()
    protos = program["protos"]
    proto_lits = [_proto_literal(p, ser) for p in protos]
    protos_lit = "{" + ",".join(proto_lits) + "}"
    pool_lit, idx_lit = _encrypt_pool(program["consts"])
    return _INTERP % {
        "pool": pool_lit,
        "idx": idx_lit,
        "protos": protos_lit,
        "chunk": program["chunk"],
    }
