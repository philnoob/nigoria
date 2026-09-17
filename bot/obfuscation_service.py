"""Bridges Discord option selections to the obfuscator pipeline."""

from __future__ import annotations

import time
from dataclasses import dataclass

from obfuscator.pipeline import Obfuscator, Options, HEADER_FREE, HEADER_PRO


# The option catalogues shown in each panel. Each entry:
#   (key, label, default_selected)
FREE_OPTIONS = [
    ("base",       "Base Obfuscation", True),
    ("cflow",      "Control Flow",     True),
    ("vmcomp",     "VM Compression",   True),
    ("antitamper", "Anti Tamper (mid)", True),
    ("opt",        "Optimization (optional)", False),
]

PRO_OPTIONS = [
    ("base",       "Good Obfuscation",        True),
    ("static",     "Static Environment",      True),
    ("globals",    "Hardcore Globals",        True),
    ("antitamper", "Anti Tamper",             True),
    ("intense",    "Intense VM Structure",    True),
    ("advcomp",    "Advanced VM Compression", True),
    ("cflow",      "Control Flow",            True),
    ("virt",       "Virtualization",          True),
    ("opt",        "Optimizations",           False),
]


def default_keys(tier: str) -> set[str]:
    catalogue = PRO_OPTIONS if tier == "pro" else FREE_OPTIONS
    return {k for k, _label, default in catalogue if default}


def build_options(tier: str, keys: set[str], seed: int | None = None) -> Options:
    opt = Options(
        base_obfuscation="base" in keys,
        control_flow="cflow" in keys,
        static_environment="static" in keys,
        hardcore_globals="globals" in keys,
        advanced_vm_compression="advcomp" in keys,
        intense_vm_structure="intense" in keys,
        virtualization="virt" in keys,
        optimizations="opt" in keys,
        vm_compression="vmcomp" in keys,
        number_intensity=3 if tier == "pro" else 2,
        junk_code=True,
        junk_intensity=2 if tier == "pro" else 1,
        seed=seed,
    )
    pro = tier == "pro"
    opt.header = HEADER_PRO if pro else HEADER_FREE
    if "antitamper" in keys:
        opt.anti_tamper = 2 if pro else 1
    if pro and "virt" in keys:
        opt.real_vm = True
    if pro:
        # Pro always includes a compressed base loader beneath the
        # advanced/intense layers.
        opt.vm_compression = True
    return opt


@dataclass
class ObfuscationResult:
    output: str
    input_size: int
    output_size: int
    duration: float
    warnings: list


def obfuscate_script(tier: str, keys: set[str], source: str,
                     seed: int | None = None) -> ObfuscationResult:
    opt = build_options(tier, keys, seed=seed)
    obf = Obfuscator(opt)
    start = time.time()
    out = obf.obfuscate(source)
    return ObfuscationResult(
        output=out,
        input_size=len(source),
        output_size=len(out),
        duration=time.time() - start,
        warnings=list(obf.warnings),
    )
