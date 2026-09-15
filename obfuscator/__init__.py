"""Nigoria Lua obfuscator package.

A layered Lua/Luau source obfuscator that produces output which still runs
under Roblox executors (loadstring-based loaders). See ``pipeline.py`` for the
high level entry point and ``README.md`` for the feature list.
"""

from .pipeline import Obfuscator, Options, obfuscate

__all__ = ["Obfuscator", "Options", "obfuscate"]
__version__ = "1.0.0"
