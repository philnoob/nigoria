local function pack(...) return select("#", ...), {...} end
local n, t = pack(10, 20, nil, 40)
print("varargs count", n, t[1], t[2], t[4])

local function counter()
    local c = 0
    return function() c = c + 1; return c end
end
local inc = counter()
print("closure", inc(), inc(), inc())

local function multi() return 1, 2, 3 end
local a, b, c = multi()
print("multi", a, b, c)

local mt = setmetatable({}, { __index = function(_, k) return "def:" .. k end })
print("meta", mt.foo, mt.bar)

local ok, err = pcall(function() error("boom") end)
print("pcall", ok, err ~= nil)

local parts = {}
for word in string.gmatch("a,bb,ccc", "[^,]+") do
    parts[#parts + 1] = word:upper()
end
print("gmatch", table.concat(parts, "-"))

local i = 0
::top::
i = i + 1
if i < 3 then goto top end
print("goto", i)

local neg = -5 * 3 + 2
print("arith", neg, 2.5 * 4, 100 % 7)
