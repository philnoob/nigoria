"""Decoy loadstring traps to poison hook-based dumpers.

A hook-based deobfuscator replaces ``loadstring``/``load`` and reports whatever
string is passed to it. The real VM never calls loadstring, so instead we feed
such a dumper deliberately misleading, believable "source" through a handful of
guarded loadstring calls whose results are never run. The dumper captures the
decoys and produces convincing but wrong output; the real program is unaffected.
"""

from __future__ import annotations

import random

# Believable but fake Roblox-ish snippets. A dumper that grabs these will
# "recover" a plausible script that does nothing like the real one.
_FAKE_TEMPLATES = [
    'local Players=game:GetService("Players")\n'
    'local LocalPlayer=Players.LocalPlayer\n'
    'local Remote=game:GetService("ReplicatedStorage"):WaitForChild("%s")\n'
    'while task.wait(%d) do Remote:FireServer(LocalPlayer,%d) end\n',

    'local w=game:GetService("Workspace")\n'
    'local function %s(a,b) return (a*%d+b)%%%d end\n'
    'for _,v in ipairs(w:GetChildren()) do if v:IsA("Part") then v.Anchored=true end end\n',

    'local hs=game:GetService("HttpService")\n'
    'local key="%s"\nlocal data=hs:JSONDecode("{}")\n'
    'local function verify(k) return k==key end\nif verify(key) then return true end\n',

    'local uis=game:GetService("UserInputService")\n'
    'uis.InputBegan:Connect(function(i) if i.KeyCode==Enum.KeyCode.%s then '
    'print(%d) end end)\n',
]

_WORDS = ["Handler", "Secure", "Auth", "Loader", "Config", "Session", "Token",
          "Payload", "Verify", "Client", "Gateway", "Sync", "Cache", "Router"]
_KEYS = ["F", "G", "H", "J", "K", "E", "R", "T", "Y", "U"]


def _fake_source(rng: random.Random) -> str:
    tmpl = rng.choice(_FAKE_TEMPLATES)
    n = tmpl.count("%s") + tmpl.count("%d")
    args = []
    for ch in tmpl.split("%")[1:]:
        if ch and ch[0] == "s":
            args.append(rng.choice(_WORDS) if rng.random() < 0.5 else rng.choice(_KEYS))
        elif ch and ch[0] == "d":
            args.append(rng.randint(2, 9999))
    try:
        return tmpl % tuple(args)
    except TypeError:
        return "local _=1\n"


def _lua_string(s: str) -> str:
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif o < 32 or o > 126:
            out.append("\\%d" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def decoy_traps(namer, rng: random.Random, count: int = 3) -> str:
    """Return Lua that calls loadstring on decoy source, results discarded."""
    ls = namer.new()
    parts = [f"local {ls}=loadstring or load;"]
    for _ in range(max(1, count)):
        fake = _fake_source(rng)
        parts.append(f"pcall({ls},{_lua_string(fake)});")
    return "do " + "".join(parts) + " end;"
