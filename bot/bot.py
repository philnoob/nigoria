"""Nigoria obfuscator Discord bot.

Commands:
  /panel-free   (admin only) posts the Free obfuscator panel
  /panel-pro    (admin only) posts the Pro obfuscator panel
  /whitelist    (staff) grant a user Pro access + role
  /unwhitelist  (staff) remove a user
  /check        (staff) inspect access / list Pro+whitelisted users
  /quota        (any user) show remaining Free obfuscations today

Run with the DISCORD_TOKEN environment variable set.
"""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import tasks

from . import config
from .settings import SettingsStore
from .panels import (
    PanelView, build_panel_embed, is_pro_member, has_pro_access, _deliver,
)
from .usage import UsageTracker
from .whitelist import WhitelistStore


class NigoriaBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        # Member roles -> detect Pro membership; message content -> receive
        # files uploaded through the panel's upload button.
        intents.members = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.usage = UsageTracker(config.USAGE_FILE, config.FREE_DAILY_LIMIT)
        self.whitelist = WhitelistStore(config.WHITELIST_FILE)
        self.settings = SettingsStore(config.SETTINGS_FILE)

    async def setup_hook(self) -> None:
        # Register persistent panel views so buttons keep working after restart.
        self.add_view(PanelView("free"))
        self.add_view(PanelView("pro"))
        self.expiry_task.start()
        if config.GUILD_ID:
            guild = discord.Object(id=int(config.GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        print(f"Logged in as {self.user} (id {self.user.id})")

    @tasks.loop(minutes=5)
    async def expiry_task(self):
        """Strip the Pro role from users whose timed whitelist has expired."""
        expired = self.whitelist.pop_expired()
        if not expired:
            return
        for user_id, entry in expired:
            if not entry.get("granted_role"):
                continue  # never touch a role we didn't assign
            for guild in self.guilds:
                role = guild.get_role(config.PRO_ROLE_ID)
                member = guild.get_member(user_id)
                if role is None or member is None:
                    continue
                if role in member.roles:
                    try:
                        await member.remove_roles(
                            role, reason="whitelist expired")
                        print(f"Removed Pro role from {user_id} (expired)")
                    except discord.HTTPException:
                        pass

    @expiry_task.before_loop
    async def _before_expiry(self):
        await self.wait_until_ready()


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
        await interaction.response.send_message("not for you.", ephemeral=True)
        return
    await interaction.response.send_message("posted.", ephemeral=True)
    await interaction.channel.send(
        embed=build_panel_embed("free"), view=PanelView("free"))


@tree.command(name="panel-pro", description="Post the Pro obfuscator panel")
async def panel_pro(interaction: discord.Interaction):
    if not _admin_only(interaction):
        await interaction.response.send_message("not for you.", ephemeral=True)
        return
    await interaction.response.send_message("posted.", ephemeral=True)
    await interaction.channel.send(
        embed=build_panel_embed("pro"), view=PanelView("pro"))
    interaction.client.settings.set("pro_panel_channel", interaction.channel.id)


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


def _fmt_expiry(entry: dict) -> str:
    exp = entry.get("expires_at")
    if not exp:
        return "Lifetime"
    try:
        from datetime import datetime
        return datetime.fromisoformat(exp).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return "Lifetime"


async def _grant_pro_and_announce(interaction: discord.Interaction,
                                 user: discord.User, entry: dict) -> str:
    """Best-effort: give the user the Pro role and ping them in the Pro channel.

    Returns a short status string for the staff confirmation.
    """
    guild = interaction.guild
    gave_role = False
    role_problem = None
    if guild is not None:
        role = guild.get_role(config.PRO_ROLE_ID)
        member = guild.get_member(user.id)
        if member is None:
            try:
                member = await guild.fetch_member(user.id)
            except discord.HTTPException:
                member = None
        if role is None:
            role_problem = "pro role not found"
        elif member is None:
            role_problem = "user isn't in the server"
        else:
            try:
                await member.add_roles(role, reason="whitelisted")
                gave_role = True
                interaction.client.whitelist.mark_granted_role(user.id, True)
            except discord.Forbidden:
                role_problem = "missing permission / role too high"
            except discord.HTTPException:
                role_problem = "discord error assigning role"

    # Figure out where the Pro panel lives.
    chan_id = (config.PRO_PANEL_CHANNEL_ID
               or interaction.client.settings.get("pro_panel_channel"))
    chan_id = int(chan_id) if chan_id else None
    target = guild.get_channel(chan_id) if (guild and chan_id) else None
    where = target.mention if target else "the pro panel"

    if gave_role:
        text = (f"{user.mention} just got whitelisted — you've been given the "
                f"pro role, head to {where} to obfuscate.")
    else:
        text = (f"{user.mention} just got whitelisted — head to {where}. "
                f"staff, please give them the pro role.")

    announce_channel = target or interaction.channel
    try:
        await announce_channel.send(
            text, allowed_mentions=discord.AllowedMentions(users=True))
    except discord.HTTPException:
        pass

    status = "gave pro role" if gave_role else f"role not set ({role_problem})"
    return status


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
            "you can't manage the whitelist.", ephemeral=True)
        return
    if days is not None and days < 0:
        await interaction.response.send_message(
            "days can't be negative — leave it blank for lifetime.",
            ephemeral=True)
        return
    entry = interaction.client.whitelist.add(
        user.id, note, days, interaction.user.id)
    await interaction.response.defer(ephemeral=True, thinking=True)
    status = await _grant_pro_and_announce(interaction, user, entry)
    await interaction.followup.send(
        f"whitelisted {user.mention} — {status}. expires: "
        f"**{_fmt_expiry(entry)}**" + (f" · note: {note}" if note else ""),
        ephemeral=True)


@tree.command(name="unwhitelist", description="Remove a user from the whitelist")
@app_commands.describe(user="The user to remove")
async def unwhitelist_cmd(interaction: discord.Interaction, user: discord.User):
    if not _is_staff(interaction):
        await interaction.response.send_message(
            "you can't manage the whitelist.", ephemeral=True)
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
