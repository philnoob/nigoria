"""High-level obfuscation pipeline.

Ties the AST transforms and the packer together behind a single ``Options``
object, with feature presets for the Free and Pro tiers. If the source cannot
be fully parsed, the AST transforms are skipped and the raw source is still
packed, so obfuscation always produces runnable output.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import ast_nodes as A
from .generator import generate
from .namegen import NameGenerator, PreambleNamer
from .packer import pack
from .parser import ParseError, parse
from .lexer import LexError
from .transforms.controlflow import flatten
from .transforms.globalscope import harden_globals
from .transforms.numbers import obfuscate_numbers
from .transforms.optimize import optimize as optimize_pass
from .transforms.junk import inject_junk
from .transforms.rename import rename_locals
from .transforms.strings import StringNames, encrypt_strings

HEADER = "--[[ Skid Optimzation v1.0]]\n"
HEADER_FREE = "--[[ Skid Optimzation v1.5 Free]]\n"
HEADER_PRO = "--[[ Skid Optimzation v2.0 Pro]]\n"


@dataclass
class Options:
    # --- core feature switches (mirror the Discord panels) ---------------
    base_obfuscation: bool = False      # local renaming + number obfuscation
    control_flow: bool = False          # control-flow flattening
    static_environment: bool = False    # string encryption
    hardcore_globals: bool = False      # route globals through an env proxy
    vm_compression: bool = False        # LZW-compressed loader (1 layer)
    advanced_vm_compression: bool = False   # extra nested layer
    intense_vm_structure: bool = False      # extra nested layer + VM wrapper
    virtualization: bool = False        # opcode-dispatch VM wrapper
    optimizations: bool = False         # constant folding / dead code
    number_intensity: int = 1           # depth of numeric-literal obfuscation
    anti_tamper: int = 0                 # 0 none, 1 basic, 2 self-corrupting
    junk_code: bool = False              # inject dead/decoy code
    junk_intensity: int = 1              # how much junk per block
    extra_layers: int = 0                # additional nested loader layers
    header: str = HEADER                 # banner comment prepended to output
    seed: int | None = None
    warnings: list[str] = field(default_factory=list)

    # --- presets ----------------------------------------------------------
    @classmethod
    def free(cls, optimizations: bool = False, seed: int | None = None) -> "Options":
        return cls(
            base_obfuscation=True,
            control_flow=True,
            vm_compression=True,
            optimizations=optimizations,
            number_intensity=2,
            anti_tamper=1,
            junk_code=True,
            junk_intensity=1,
            header=HEADER_FREE,
            seed=seed,
        )

    @classmethod
    def pro(cls, virtualization: bool = True, optimizations: bool = False,
            seed: int | None = None) -> "Options":
        return cls(
            base_obfuscation=True,
            control_flow=True,
            static_environment=True,
            hardcore_globals=True,
            intense_vm_structure=True,
            advanced_vm_compression=True,
            vm_compression=True,
            virtualization=virtualization,
            optimizations=optimizations,
            number_intensity=3,
            anti_tamper=2,
            junk_code=True,
            junk_intensity=2,
            header=HEADER_PRO,
            seed=seed,
        )

    def layer_count(self) -> int:
        # One compression layer if any VM/compression option is on; the
        # "intense" option adds a single extra nested layer (Pro = 2 total).
        # "advanced" just keeps compression on and does not multiply size.
        any_pack = (self.vm_compression or self.advanced_vm_compression
                    or self.intense_vm_structure)
        layers = 1 if any_pack else 0
        layers += max(0, self.extra_layers)
        return min(layers, 2)


class Obfuscator:
    def __init__(self, options: Options):
        self.opt = options
        self.warnings: list[str] = []
        seed = options.seed
        self.rng = random.Random(seed)
        self.namegen = NameGenerator(seed)
        self.preamble_namer = PreambleNamer(
            None if seed is None else seed ^ 0x9E3779B9)
        self.state_namer = PreambleNamer(
            None if seed is None else seed ^ 0x1234567)

    def obfuscate(self, source: str) -> str:
        body, ok = self._transform_body(source)
        if not ok:
            self.warnings.append(
                "source could not be fully parsed; applied packing only")
        packed = self._pack(body)
        return self.opt.header + packed

    # --- AST stage --------------------------------------------------------
    def _transform_body(self, source: str) -> tuple[str, bool]:
        try:
            block = parse(source)
        except (ParseError, LexError) as exc:  # fall back to raw packing
            self.warnings.append(f"parse fallback: {exc}")
            return source, False

        preambles: list[str] = []

        if self.opt.optimizations:
            block = optimize_pass(block)

        # Renaming: required for correct control-flow flattening; a resolve-only
        # pass still runs so hardcore-globals can tell locals from globals.
        need_full_rename = self.opt.base_obfuscation or self.opt.control_flow
        need_resolve = need_full_rename or self.opt.hardcore_globals
        if need_resolve:
            block = rename_locals(block, self.namegen, rename=need_full_rename)

        if self.opt.hardcore_globals:
            env_name = self.preamble_namer.new()
            block, pre = harden_globals(block, env_name)
            if pre:
                preambles.append(pre)

        if self.opt.control_flow:
            block = flatten(block, self.rng, self.state_namer)

        if self.opt.base_obfuscation:
            block = obfuscate_numbers(block, self.rng,
                                      intensity=self.opt.number_intensity)

        if self.opt.static_environment:
            names = StringNames(
                decoder=self.preamble_namer.new(),
                pool=self.preamble_namer.new(),
                idx=self.preamble_namer.new(),
                cache=self.preamble_namer.new(),
                sb=self.preamble_namer.new(),
                sc=self.preamble_namer.new(),
            )
            block, pre = encrypt_strings(block, names)
            if pre:
                # The string decoder must be defined before the body uses it,
                # but it must come AFTER the globals env (its own keys are not
                # encrypted). Decoder preamble goes closest to the body.
                preambles.append(pre)

        if self.opt.junk_code:
            block = inject_junk(block, self.preamble_namer, self.rng,
                                intensity=self.opt.junk_intensity)

        core = generate(block, indent="")
        return "".join(preambles) + core, True

    # --- packing stage ----------------------------------------------------
    def _pack(self, body: str) -> str:
        layers = self.opt.layer_count()
        if layers == 0 and not self.opt.virtualization:
            return body
        if layers == 0:
            layers = 1  # virtualization still needs a loader to wrap
        seed = self.opt.seed
        return pack(
            body,
            rng=self.rng,
            layers=layers,
            virtualize=self.opt.virtualization or self.opt.intense_vm_structure,
            anti_tamper=self.opt.anti_tamper,
            junk=self.opt.junk_code,
            junk_intensity=self.opt.junk_intensity,
            seed=None if seed is None else seed ^ 0xABCDEF,
        )


def obfuscate(source: str, options: Options | None = None) -> str:
    opt = options or Options.free()
    obf = Obfuscator(opt)
    result = obf.obfuscate(source)
    opt.warnings = obf.warnings
    return result
