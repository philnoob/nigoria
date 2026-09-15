"""Discord UI: the Free and Pro obfuscator panels.

An admin posts a panel. Anyone clicks "Open" to get their own private config
(option picker + paste / upload buttons). Results come back privately as a
`.lua` file.
"""

from __future__ import annotations

import asyncio
import io

import discord

from . import config
from .obfuscation_service import (
    FREE_OPTIONS, PRO_OPTIONS, default_keys, obfuscate_script,
)


def _catalogue(tier: str):
    return PRO_OPTIONS if tier == "pro" else FREE_OPTIONS


def is_pro_member(user: discord.abc.User) -> bool:
    member = user if isinstance(user, discord.Member) else None
    if member is None:
        return False
    return any(r.id == config.PRO_ROLE_ID for r in member.roles)


def has_pro_access(interaction: discord.Interaction) -> bool:
    """Pro if the user has the Pro role OR an active whitelist entry."""
    if is_pro_member(interaction.user):
        return True
    store = getattr(interaction.client, "whitelist", None)
    return bool(store and store.is_whitelisted(interaction.user.id))


async def _deliver(interaction: discord.Interaction, tier: str,
                   keys: set[str], source: str) -> None:
    """Run obfuscation off the event loop and reply privately with the file."""
    if not source.strip():
        await interaction.followup.send("that script's empty.", ephemeral=True)
        return
    if len(source) > config.MAX_SCRIPT_CHARS:
        await interaction.followup.send(
            f"too big — {len(source)} chars, max is {config.MAX_SCRIPT_CHARS}.",
            ephemeral=True)
        return

    if tier == "free":
        allowed, remaining = interaction.client.usage.try_consume(interaction.user.id)
        if not allowed:
            await interaction.followup.send(
                f"you're out of free runs for today ({config.FREE_DAILY_LIMIT}/day). "
                "resets at midnight UTC — or grab pro.", ephemeral=True)
            return
    else:
        remaining = None

    try:
        result = await asyncio.to_thread(obfuscate_script, tier, keys, source)
    except Exception as exc:
        await interaction.followup.send(
            f"couldn't obfuscate that: `{type(exc).__name__}: {exc}`",
            ephemeral=True)
        return

    note = ""
    if remaining is not None:
        note = f"  ({remaining} left today)"
    extra = ""
    if result.warnings:
        extra = "\n" + " ".join(result.warnings)[:400]
    msg = (f"done — {result.input_size:,} → {result.output_size:,} chars "
           f"in {result.duration:.2f}s{note}{extra}")

    data = result.output.encode("utf-8")
    file = discord.File(io.BytesIO(data), filename="obfuscated.lua")
    await interaction.followup.send(msg, file=file, ephemeral=True)


class ScriptModal(discord.ui.Modal):
    def __init__(self, tier: str, keys: set[str]):
        super().__init__(title="obfuscator")
        self.tier = tier
        self.keys = keys
        self.script = discord.ui.TextInput(
            label="paste your script",
            style=discord.TextStyle.paragraph,
            placeholder="paste here (or use the upload button for bigger files)",
            required=True,
            max_length=4000,
        )
        self.add_item(self.script)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _deliver(interaction, self.tier, self.keys, str(self.script.value))


class OptionSelect(discord.ui.Select):
    def __init__(self, tier: str, selected: set[str]):
        self.tier = tier
        self.selected = selected
        options = [
            discord.SelectOption(label=label, value=key, default=(key in selected))
            for key, label, _ in _catalogue(tier)
        ]
        super().__init__(placeholder="options", min_values=0,
                         max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction):
        self.selected.clear()
        self.selected.update(self.values)
        for opt in self.options:
            opt.default = opt.value in self.selected
        await interaction.response.edit_message(view=self.view)


class PasteButton(discord.ui.Button):
    def __init__(self, tier: str, selected: set[str]):
        super().__init__(label="paste", style=discord.ButtonStyle.success)
        self.tier = tier
        self.selected = selected

    async def callback(self, interaction: discord.Interaction):
        keys = set(self.selected) or default_keys(self.tier)
        await interaction.response.send_modal(ScriptModal(self.tier, keys))


class UploadButton(discord.ui.Button):
    def __init__(self, tier: str, selected: set[str]):
        super().__init__(label="upload file", style=discord.ButtonStyle.primary)
        self.tier = tier
        self.selected = selected

    async def callback(self, interaction: discord.Interaction):
        keys = set(self.selected) or default_keys(self.tier)
        await interaction.response.send_message(
            "drop your `.lua` file in this channel now (you've got 60s).",
            ephemeral=True)

        def check(m: discord.Message) -> bool:
            return (m.author.id == interaction.user.id
                    and m.channel.id == interaction.channel.id
                    and bool(m.attachments))

        try:
            message = await interaction.client.wait_for(
                "message", check=check, timeout=60)
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "no file showed up in time.", ephemeral=True)
            return

        att = message.attachments[0]
        if not att.filename.lower().endswith((".lua", ".luau", ".txt")):
            await interaction.followup.send(
                "needs to be a .lua / .luau / .txt file.", ephemeral=True)
            return
        if att.size > config.MAX_SCRIPT_CHARS * 4:
            await interaction.followup.send("that file's too big.", ephemeral=True)
            return

        try:
            raw = await att.read()
            source = raw.decode("utf-8", "replace")
        except Exception as exc:
            await interaction.followup.send(
                f"couldn't read that file: `{exc}`", ephemeral=True)
            return

        # Keep the channel clean — remove the upload once we've read it.
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        await _deliver(interaction, self.tier, keys, source)


class ConfigView(discord.ui.View):
    """Private, per-user configuration view."""

    def __init__(self, tier: str):
        super().__init__(timeout=600)
        selected = default_keys(tier)
        self.add_item(OptionSelect(tier, selected))
        self.add_item(PasteButton(tier, selected))
        self.add_item(UploadButton(tier, selected))


class PanelView(discord.ui.View):
    """Persistent public panel with a single entry button."""

    def __init__(self, tier: str):
        super().__init__(timeout=None)
        self.tier = tier
        style = (discord.ButtonStyle.primary if tier == "pro"
                 else discord.ButtonStyle.secondary)
        button = discord.ui.Button(
            label="open", style=style, custom_id=f"nigoria:panel:{tier}")
        button.callback = self._open
        self.add_item(button)

    async def _open(self, interaction: discord.Interaction):
        tier = self.tier
        if tier == "pro" and not has_pro_access(interaction):
            await interaction.response.send_message(
                "this one's pro only — you need the pro role for it.",
                ephemeral=True)
            return
        if tier == "free":
            remaining = interaction.client.usage.remaining(interaction.user.id)
            if remaining <= 0:
                await interaction.response.send_message(
                    f"you're out of free runs today ({config.FREE_DAILY_LIMIT}/day, "
                    "resets midnight UTC).", ephemeral=True)
                return
            header = (f"pick your options, then paste or upload your script. "
                      f"({remaining} runs left today)")
        else:
            header = "pick your options, then paste or upload your script."
        await interaction.response.send_message(
            header, view=ConfigView(tier), ephemeral=True)


def build_panel_embed(tier: str) -> discord.Embed:
    if tier == "pro":
        embed = discord.Embed(
            title="pro obfuscator",
            description="hit open, choose what you want, paste or upload your "
                        "script. pro only.",
            colour=0xE6B325,
        )
    else:
        embed = discord.Embed(
            title="free obfuscator",
            description=f"hit open, choose your options, paste or upload. "
                        f"{config.FREE_DAILY_LIMIT} runs a day.",
            colour=0x3BA55D,
        )
    feats = " · ".join(label for _k, label, _d in _catalogue(tier))
    embed.add_field(name="options", value=feats, inline=False)
    return embed
