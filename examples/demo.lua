-- A feature-rich sample used for round-trip behaviour testing.
local function factorial(n)
    if n <= 1 then
        return 1
    end
    return n * factorial(n - 1)
end

local Account = {}
Account.__index = Account

function Account.new(owner, balance)
    local self = setmetatable({}, Account)
    self.owner = owner
    self.balance = balance or 0
    return self
end

function Account:deposit(amount)
    self.balance = self.balance + amount
    return self.balance
end

local function sumTable(t)
    local total = 0
    for _, v in ipairs(t) do
        total = total + v
    end
    return total
end

local acc = Account.new("nigoria", 100)
acc:deposit(50)
acc:deposit(25)

local squares = {}
for i = 1, 6 do
    squares[i] = i * i
end

local greeting = "Hello" .. ", " .. "world!"
local flags = { enabled = true, count = 0x0A, name = "config" }

print("factorial(5) =", factorial(5))
print("balance =", acc.balance, "owner =", acc.owner)
print("sum squares =", sumTable(squares))
print("greeting =", greeting)
print("flags =", flags.enabled, flags.count, flags.name)

local msg = ""
local x = 3
while x > 0 do
    msg = msg .. tostring(x)
    x = x - 1
end
print("countdown =", msg)
