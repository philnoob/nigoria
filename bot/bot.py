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
    PanelView, build_panel_embed, is_pro_member, _deliver,
)
from .usage import UsageTracker


class NigoriaBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        # Member roles are needed to detect Pro membership.
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.usage = UsageTracker(config.USAGE_FILE, config.FREE_DAILY_LIMIT)

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
    if is_pro_member(interaction.user):
        await interaction.response.send_message(
            "You have the Pro role — unlimited obfuscations.", ephemeral=True)
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
    tier = "pro" if is_pro_member(interaction.user) else "free"

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


def main() -> None:
    if not config.TOKEN:
        raise SystemExit(
            "Set the DISCORD_TOKEN environment variable to run the bot.")
    client.run(config.TOKEN)


if __name__ == "__main__":
    main()
