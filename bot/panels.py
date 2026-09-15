"""Discord UI: the Free and Pro obfuscator panels.

Flow:
  * An admin runs /panel-free or /panel-pro which posts a public panel with a
    persistent "Configure & Obfuscate" button.
  * Any user clicks it. Pro requires the Pro role; Free checks the daily limit.
    The bot replies with an *ephemeral* per-user configuration view: a
    multi-select of that tier's options plus an Obfuscate button.
  * Obfuscate opens a modal to paste the script (up to 4000 chars — larger
    scripts use the /obfuscate file command). The result comes back as an
    ephemeral .lua attachment.
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


async def _deliver(interaction: discord.Interaction, tier: str,
                   keys: set[str], source: str) -> None:
    """Run obfuscation off the event loop and send the result ephemerally."""
    if not source.strip():
        await interaction.followup.send("The script was empty.", ephemeral=True)
        return
    if len(source) > config.MAX_SCRIPT_CHARS:
        await interaction.followup.send(
            f"Script too large ({len(source)} chars, limit "
            f"{config.MAX_SCRIPT_CHARS}).", ephemeral=True)
        return

    # Enforce the Free daily limit at the point of use.
    if tier == "free":
        allowed, remaining = interaction.client.usage.try_consume(interaction.user.id)
        if not allowed:
            await interaction.followup.send(
                f"You've reached the Free limit of {config.FREE_DAILY_LIMIT} "
                "obfuscations today. Resets at UTC midnight, or upgrade to Pro.",
                ephemeral=True)
            return
    else:
        remaining = None

    try:
        result = await asyncio.to_thread(obfuscate_script, tier, keys, source)
    except Exception as exc:  # never crash the interaction
        await interaction.followup.send(
            f"Obfuscation failed: `{type(exc).__name__}: {exc}`", ephemeral=True)
        return

    selected = ", ".join(
        label for key, label, _ in _catalogue(tier) if key in keys) or "none"
    embed = discord.Embed(
        title=f"{tier.title()} obfuscation complete",
        colour=0xF1C40F if tier == "pro" else 0x2ECC71,
    )
    embed.add_field(name="Options", value=selected, inline=False)
    embed.add_field(name="Input", value=f"{result.input_size:,} chars")
    embed.add_field(name="Output", value=f"{result.output_size:,} chars")
    embed.add_field(name="Time", value=f"{result.duration:.2f}s")
    if remaining is not None:
        embed.set_footer(text=f"Free uses left today: {remaining}")
    if result.warnings:
        embed.add_field(name="Notes", value="; ".join(result.warnings)[:1000],
                        inline=False)

    data = result.output.encode("utf-8")
    file = discord.File(io.BytesIO(data), filename="obfuscated.lua")
    await interaction.followup.send(embed=embed, file=file, ephemeral=True)


class ScriptModal(discord.ui.Modal):
    def __init__(self, tier: str, keys: set[str]):
        super().__init__(title=f"{tier.title()} Obfuscator")
        self.tier = tier
        self.keys = keys
        self.script = discord.ui.TextInput(
            label="Paste your Lua script",
            style=discord.TextStyle.paragraph,
            placeholder="-- your script here (max 4000 chars; use /obfuscate for files)",
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
        super().__init__(
            placeholder="Select obfuscation options",
            min_values=0,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        self.selected.clear()
        self.selected.update(self.values)
        # Reflect selection back so the menu shows current state.
        for opt in self.options:
            opt.default = opt.value in self.selected
        await interaction.response.edit_message(view=self.view)


class ObfuscateButton(discord.ui.Button):
    def __init__(self, tier: str, selected: set[str]):
        super().__init__(label="Obfuscate", style=discord.ButtonStyle.success,
                         emoji="🔒")
        self.tier = tier
        self.selected = selected

    async def callback(self, interaction: discord.Interaction):
        keys = set(self.selected) or default_keys(self.tier)
        await interaction.response.send_modal(ScriptModal(self.tier, keys))


class ConfigView(discord.ui.View):
    """Ephemeral, per-user configuration view."""

    def __init__(self, tier: str):
        super().__init__(timeout=600)
        selected = default_keys(tier)
        self.add_item(OptionSelect(tier, selected))
        self.add_item(ObfuscateButton(tier, selected))


class PanelView(discord.ui.View):
    """Persistent public panel with a single entry button."""

    def __init__(self, tier: str):
        super().__init__(timeout=None)
        self.tier = tier
        style = (discord.ButtonStyle.primary if tier == "pro"
                 else discord.ButtonStyle.secondary)
        button = discord.ui.Button(
            label="Configure & Obfuscate",
            style=style,
            emoji="⚙️",
            custom_id=f"nigoria:panel:{tier}",
        )
        button.callback = self._open
        self.add_item(button)

    async def _open(self, interaction: discord.Interaction):
        tier = self.tier
        if tier == "pro" and not is_pro_member(interaction.user):
            await interaction.response.send_message(
                "This is the **Pro** panel. You need the Pro role to use it.",
                ephemeral=True)
            return
        if tier == "free":
            remaining = interaction.client.usage.remaining(interaction.user.id)
            if remaining <= 0:
                await interaction.response.send_message(
                    f"You've used all {config.FREE_DAILY_LIMIT} Free obfuscations "
                    "for today (resets at UTC midnight).", ephemeral=True)
                return
            header = (f"**Free obfuscator** — {remaining}/"
                      f"{config.FREE_DAILY_LIMIT} uses left today.\n"
                      "Pick your options, then press Obfuscate.")
        else:
            header = ("**Pro obfuscator** — pick your options, then press "
                      "Obfuscate.\nScripts over 4000 chars: use `/obfuscate` "
                      "with a file attachment.")
        await interaction.response.send_message(
            header, view=ConfigView(tier), ephemeral=True)


def build_panel_embed(tier: str) -> discord.Embed:
    if tier == "pro":
        embed = discord.Embed(
            title="🔒 Nigoria — Pro Obfuscator",
            description="Premium protection for your Lua scripts. "
                        "Requires the Pro role.",
            colour=0xF1C40F,
        )
    else:
        embed = discord.Embed(
            title="🔓 Nigoria — Free Obfuscator",
            description=f"Free tier — {config.FREE_DAILY_LIMIT} obfuscations "
                        "per day.",
            colour=0x2ECC71,
        )
    feats = "\n".join(f"• {label}" for _k, label, _d in _catalogue(tier))
    embed.add_field(name="Available options", value=feats, inline=False)
    embed.set_footer(text="Nigoria • Skid Optimzation v1.0")
    return embed
