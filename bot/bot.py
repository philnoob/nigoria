"""Nigoria obfuscator Discord bot.

Commands:
  /panel-free   (admin only) posts the Free obfuscator panel
  /panel-pro    (admin only) posts the Pro obfuscator panel
  /obfuscate    (any user) obfuscate an uploaded .lua/.txt file; tier is
                decided by the Pro role, Free enforces the daily limit
  /quota        (any user) show remaining Free obfuscations today

Run with the DISCORD_TOKEN environment variable set.
"""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands

from . import config
from .obfuscation_service import default_keys, obfuscate_script
from .panels import (
    PanelView, build_panel_embed, is_pro_member, has_pro_access, _deliver,
)
from .usage import UsageTracker
from .whitelist import WhitelistStore


class NigoriaBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        # Member roles are needed to detect Pro membership.
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.usage = UsageTracker(config.USAGE_FILE, config.FREE_DAILY_LIMIT)
        self.whitelist = WhitelistStore(config.WHITELIST_FILE)

    async def setup_hook(self) -> None:
        # Register persistent panel views so buttons keep working after restart.
        self.add_view(PanelView("free"))
        self.add_view(PanelView("pro"))
        if config.GUILD_ID:
            guild = discord.Object(id=int(config.GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        print(f"Logged in as {self.user} (id {self.user.id})")


client = NigoriaBot()
tree = client.tree


def _admin_only(interaction: discord.Interaction) -> bool:
    return interaction.user.id == config.ADMIN_USER_ID


def _is_staff(interaction: discord.Interaction) -> bool:
    """Whitelist managers: the configured admin, or a Discord server admin."""
    if interaction.user.id == config.ADMIN_USER_ID:
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and (perms.administrator or perms.manage_guild))


@tree.command(name="panel-free", description="Post the Free obfuscator panel")
async def panel_free(interaction: discord.Interaction):
    if not _admin_only(interaction):
        await interaction.response.send_message(
            "Only the configured admin can deploy panels.", ephemeral=True)
        return
    await interaction.response.send_message(
        embed=build_panel_embed("free"), view=PanelView("free"))


@tree.command(name="panel-pro", description="Post the Pro obfuscator panel")
async def panel_pro(interaction: discord.Interaction):
    if not _admin_only(interaction):
        await interaction.response.send_message(
            "Only the configured admin can deploy panels.", ephemeral=True)
        return
    await interaction.response.send_message(
        embed=build_panel_embed("pro"), view=PanelView("pro"))


@tree.command(name="quota", description="Show your remaining Free obfuscations today")
async def quota(interaction: discord.Interaction):
    if has_pro_access(interaction):
        await interaction.response.send_message(
            "You have Pro access — unlimited obfuscations.", ephemeral=True)
        return
    left = client.usage.remaining(interaction.user.id)
    await interaction.response.send_message(
        f"Free obfuscations left today: **{left}/{config.FREE_DAILY_LIMIT}** "
        "(resets at UTC midnight).", ephemeral=True)


@tree.command(name="obfuscate",
              description="Obfuscate an uploaded .lua/.txt file")
@app_commands.describe(
    file="The Lua script file to obfuscate",
    virtualization="Pro only: add the virtualization VM layer",
    optimizations="Enable the optimizer pass",
)
async def obfuscate_cmd(interaction: discord.Interaction,
                        file: discord.Attachment,
                        virtualization: bool = False,
                        optimizations: bool = False):
    tier = "pro" if has_pro_access(interaction) else "free"

    if not file.filename.lower().endswith((".lua", ".txt", ".luau")):
        await interaction.response.send_message(
            "Please upload a .lua, .luau or .txt file.", ephemeral=True)
        return
    if file.size > config.MAX_SCRIPT_CHARS * 4:
        await interaction.response.send_message(
            "That file is too large.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        raw = await file.read()
        source = raw.decode("utf-8", "replace")
    except Exception as exc:
        await interaction.followup.send(
            f"Could not read the file: `{exc}`", ephemeral=True)
        return

    keys = default_keys(tier)
    if tier == "pro" and virtualization:
        keys.add("virt")
    if optimizations:
        keys.add("opt")

    await _deliver(interaction, tier, keys, source)



def _fmt_expiry(entry: dict) -> str:
    exp = entry.get("expires_at")
    if not exp:
        return "Lifetime"
    try:
        from datetime import datetime
        return datetime.fromisoformat(exp).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return "Lifetime"


@tree.command(name="whitelist", description="Whitelist a user (blank days = lifetime)")
@app_commands.describe(
    user="The user to whitelist (required)",
    note="Optional note about why",
    days="Days until it expires; leave blank for lifetime",
)
async def whitelist_cmd(interaction: discord.Interaction,
                        user: discord.User,
                        note: str = "",
                        days: int | None = None):
    if not _is_staff(interaction):
        await interaction.response.send_message(
            "You don't have permission to manage the whitelist.", ephemeral=True)
        return
    if days is not None and days < 0:
        await interaction.response.send_message(
            "`days` cannot be negative. Leave it blank for lifetime.",
            ephemeral=True)
        return
    entry = interaction.client.whitelist.add(
        user.id, note, days, interaction.user.id)
    await interaction.response.send_message(
        f"Whitelisted {user.mention} — expires: **{_fmt_expiry(entry)}**"
        + (f"\nNote: {note}" if note else ""),
        ephemeral=True)


@tree.command(name="unwhitelist", description="Remove a user from the whitelist")
@app_commands.describe(user="The user to remove")
async def unwhitelist_cmd(interaction: discord.Interaction, user: discord.User):
    if not _is_staff(interaction):
        await interaction.response.send_message(
            "You don't have permission to manage the whitelist.", ephemeral=True)
        return
    removed = interaction.client.whitelist.remove(user.id)
    msg = (f"Removed {user.mention} from the whitelist."
           if removed else f"{user.mention} was not whitelisted.")
    await interaction.response.send_message(msg, ephemeral=True)


PAGE_SIZE = 30


def _gather_access_lines(interaction: discord.Interaction) -> list[str]:
    """Every user with Pro access: active whitelist entries + Pro-role members."""
    client = interaction.client
    seen: dict[int, str] = {}

    for uid, entry in client.whitelist.active_entries():
        label = f"Whitelist ({_fmt_expiry(entry)})"
        if entry.get("note"):
            label += f" — {entry['note']}"
        seen[uid] = label

    guild = interaction.guild
    if guild is not None:
        role = guild.get_role(config.PRO_ROLE_ID)
        if role is not None:
            for member in role.members:
                if member.id not in seen:
                    seen[member.id] = "Pro role"

    lines = []
    for i, (uid, label) in enumerate(sorted(seen.items()), start=1):
        user = interaction.client.get_user(uid)
        name = f"{user}" if user else f"user {uid}"
        lines.append(f"**{i}.** {name} (`{uid}`) — {label}")
    return lines


class CheckPaginator(discord.ui.View):
    def __init__(self, lines: list[str], author_id: int):
        super().__init__(timeout=300)
        self.lines = lines
        self.author_id = author_id
        self.page = 0
        self.pages = max(1, (len(lines) + PAGE_SIZE - 1) // PAGE_SIZE)
        self._sync_buttons()

    def _sync_buttons(self):
        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= self.pages - 1

    def embed(self) -> discord.Embed:
        start = self.page * PAGE_SIZE
        chunk = self.lines[start:start + PAGE_SIZE]
        body = "\n".join(chunk) if chunk else "No whitelisted or Pro users found."
        emb = discord.Embed(title="Pro / whitelisted users",
                            description=body, colour=0xF1C40F)
        emb.set_footer(text=f"Page {self.page + 1}/{self.pages} • "
                            f"{len(self.lines)} total")
        return emb

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This list isn't for you — run /check yourself.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, _b: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _b: discord.ui.Button):
        self.page = min(self.pages - 1, self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)


@tree.command(name="check",
              description="Check a user's access, or list all Pro/whitelisted users")
@app_commands.describe(user="Leave blank to list everyone with Pro access")
async def check_cmd(interaction: discord.Interaction,
                    user: discord.User | None = None):
    if user is not None:
        entry = interaction.client.whitelist.get(user.id)
        member = interaction.guild.get_member(user.id) if interaction.guild else None
        has_role = bool(member and any(
            r.id == config.PRO_ROLE_ID for r in member.roles))
        emb = discord.Embed(title=f"Access for {user}", colour=0x3498DB)
        emb.add_field(name="Pro role", value="Yes" if has_role else "No")
        if entry:
            emb.add_field(name="Whitelisted", value="Yes")
            emb.add_field(name="Expires", value=_fmt_expiry(entry), inline=False)
            if entry.get("note"):
                emb.add_field(name="Note", value=entry["note"], inline=False)
        else:
            emb.add_field(name="Whitelisted", value="No")
        access = "Pro" if (has_role or entry) else "Free"
        emb.set_footer(text=f"Effective tier: {access}")
        await interaction.response.send_message(embed=emb, ephemeral=True)
        return

    lines = _gather_access_lines(interaction)
    view = CheckPaginator(lines, interaction.user.id)
    await interaction.response.send_message(
        embed=view.embed(), view=view, ephemeral=True)


def main() -> None:
    if not config.TOKEN:
        raise SystemExit(
            "Set the DISCORD_TOKEN environment variable to run the bot.")
    client.run(config.TOKEN)


if __name__ == "__main__":
    main()
