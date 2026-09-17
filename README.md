# Nigoria — Roblox Lua Obfuscator + Discord Bot

A layered Lua/Luau **source obfuscator** whose output still runs under Roblox
executors (the loader uses `loadstring`/`load`), plus a **Discord bot** that
gates access through Free and Pro panels.

Obfuscated output is always prefixed with:

```lua
--[[ Skid Optimzation v1.0]]
```

## What it does

The obfuscator parses Lua 5.1 + common Luau (it understands `continue`, type
annotations, generics and compound assignment). If a script uses something the
parser can't represent, it automatically falls back to packing the raw source,
so obfuscation always produces runnable output.

### Passes

| Feature (panel label)        | Implementation |
|------------------------------|----------------|
| Base / Good Obfuscation      | Scope-aware local renaming to opaque `l/I` identifiers + integer-literal arithmetic obfuscation. Globals are left intact so Roblox APIs keep working. |
| Control Flow                 | Control-flow flattening: straight-line blocks become a dispatcher `while` loop driven by an opaque state variable, with cases emitted in shuffled order. |
| Static Environment           | String-literal encryption: every string moves into an encoded byte pool decoded at runtime (position-dependent additive cipher, no bitwise ops). |
| Hardcore Globals             | Every global reference is routed through a captured environment table (`ENV["print"]` …); combined with string encryption the names are hidden too. |
| VM Compression               | Payload is LZW-compressed, ciphered, embedded in a loader that reconstructs and `loadstring`s it. |
| Advanced VM Compression / Intense VM Structure | Additional nested loader layers, each with a distinct cipher. |
| Virtualization (Pro)         | Compiles the script into a **polymorphic custom bytecode VM**. Every build randomizes the opcode numbers, all interpreter identifiers, the dispatch order, and the cipher parameters, and the program is serialized to an **encrypted flat bytecode stream** (no readable tables). Because the layout differs every build, a devirtualizer written for one output does not work on the next. Uses table-based frames, so it also runs scripts past Lua's 200-local limit. Falls back to the transform pipeline for unsupported constructs (`goto`, string interpolation). |
| Optimizations                | Constant folding and trivial dead-code removal. |
| Anti Tamper                  | Self-checking loader: a builtin sanity check and a payload hash abort if the code is swapped, plus (Pro) a self-correcting cipher whose key depends on a checksum of the embedded data — editing/beautifying the payload yields garbage instead of clean source. |
| Junk / decoy code            | Injects unused locals, decoy tables and dead functions (bounded) to grow the output and mislead deobfuscators. |

> This is obfuscation, not cryptography. The goal is to make scripts hard to
> read and tamper with, not to provide secrecy against a determined analyst.

### Tiers

* **Free** (`--[[ Skid Optimzation v1.5 Free]]`): Base Obfuscation, Control
  Flow, VM Compression, a mid Anti Tamper, light junk, and optional
  Optimization. Limited to **3 obfuscations per day** per user.
* **Pro** (`--[[ Skid Optimzation v2.0 Pro]]`): Good Obfuscation, Static
  Environment, Hardcore Globals, strong self-correcting Anti Tamper, Intense VM
  Structure, Advanced VM Compression, Control Flow, more junk, deeper number
  obfuscation, plus Virtualization and optional Optimizations. Unlimited.
  Requires Pro access.

Output size overhead scales with the input (roughly proportional, not
multiplied): a single compressed loader layer with a compact base64 payload
keeps a large script's growth in the hundreds of KB rather than megabytes.

**On deobfuscation:** the layered loaders can still be peeled by hooking
`loadstring`, but with Pro Virtualization enabled that only reveals a
*polymorphic* interpreter over an encrypted bytecode stream and encrypted
constant pool. To recover the original a reverser must, **per build**: peel the
self-correcting anti-tamper loaders, identify this build's randomized opcode
map, decrypt the bytecode stream and constant pool (per-build ciphers), and
reconstruct the AST — and a tool built for one output does not transfer to the
next because every symbol, name and cipher is randomized. This is a real
virtualizer, not literally unbreakable (a determined expert can still trace one
interpreter by hand), but there is no reusable one-click devirtualizer.
Scripts outside the VM's supported subset fall back to the strong transform
pipeline.

## CLI

```bash
python -m obfuscator.cli input.lua -o out.lua --preset pro
python -m obfuscator.cli input.lua --preset free --optimizations
python -m obfuscator.cli input.lua --base --control-flow --static-environment
```

## Discord bot

### Commands
* `/panel-free` — (admin only) posts the Free panel.
* `/panel-pro` — (admin only) posts the Pro panel.
* `/quota` — show remaining Free obfuscations today.

Scripts are submitted **through the panel** — either pasted (up to 4000 chars)
or uploaded as a file. Discord has no file-picker component for buttons, so the
**Upload file** button opens a DM with the bot and waits for you to drop a
`.lua`/`.luau`/`.txt` file there (this works even when the panel channel is
locked). The obfuscated file is returned in the DM.
* `/whitelist <user> [note] [days]` — (staff) grant a user Pro access. Blank
  `days` = lifetime. The bot also tries to assign the Pro role and posts a
  message pinging the user pointing them to the Pro panel channel (the channel
  where `/panel-pro` was last posted, or `PRO_PANEL_CHANNEL_ID`). Whitelisted
  users bypass the Free limit and the Pro role check. When a timed whitelist
  expires, a background task (runs every 5 minutes) removes the Pro role again
  — but only if the bot was the one that assigned it.
* `/unwhitelist <user>` — (staff) remove a user from the whitelist.
* `/check [user]` — with a user, show their access (Pro role / whitelist /
  expiry / effective tier). With no user, list everyone with Pro access
  (whitelisted + Pro-role members), 30 per page.

"Pro access" means either the Pro role **or** an active whitelist entry.
Whitelist management is available to the configured admin and to Discord
server administrators (Administrator / Manage Server permission).

Only the configured admin user (`PANEL_ADMIN_ID`, default
`392050489505873931`) can deploy panels. Pro access is granted by the role
`PRO_ROLE_ID` (default `1549480180672626688`).

### Panel flow
1. Admin posts a panel. It shows the tier's options and a **Configure &
   Obfuscate** button.
2. A user clicks it — Pro checks the role, Free checks the daily limit — and
   gets a private (ephemeral) configuration view with a multi-select of
   options and an **Obfuscate** button.
3. Obfuscate opens a modal to paste the script; the result is returned as an
   ephemeral `obfuscated.lua` attachment.

### Running

```bash
pip install -r requirements.txt
cp .env.example .env          # then edit DISCORD_TOKEN
export $(grep -v '^#' .env | xargs)   # or use your process manager
python -m bot.bot
```

Required Discord settings: enable the **Server Members Intent** (detect the Pro
role) and the **Message Content Intent** (receive files uploaded through the
panel) on the bot's page in the Developer Portal. Invite it with the
`applications.commands` and `bot` scopes, and give it Manage Roles (above the
Pro role) so it can grant the role on whitelist, plus Manage Messages so it can
tidy up uploaded files.

## Tests

```bash
pip install pytest
python -m pytest tests/
```

The suite obfuscates sample scripts under every option combination and asserts
the obfuscated program prints exactly what the original does (validated with a
`lua` interpreter).

## Layout

```
obfuscator/            the obfuscator library
  lexer.py parser.py generator.py   Lua front-end
  walker.py namegen.py luastr.py    shared helpers
  transforms/                       rename, numbers, strings, globals,
                                    controlflow, optimize
  packer.py                         LZW + cipher + loader layers + VM
  pipeline.py cli.py                orchestration + CLI
bot/                   the Discord bot
  config.py usage.py obfuscation_service.py panels.py bot.py
examples/              demo scripts used by the tests
tests/                 behaviour tests
```
