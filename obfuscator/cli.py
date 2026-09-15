"""Command-line interface for the Nigoria Lua obfuscator.

Usage:
    python -m obfuscator.cli input.lua -o out.lua --preset pro
    python -m obfuscator.cli input.lua --base --control-flow --vm-compression
"""

from __future__ import annotations

import argparse
import sys

from .pipeline import Obfuscator, Options


def build_options(ns: argparse.Namespace) -> Options:
    if ns.preset == "free":
        return Options.free(optimizations=ns.optimizations, seed=ns.seed)
    if ns.preset == "pro":
        return Options.pro(virtualization=ns.virtualization,
                           optimizations=ns.optimizations, seed=ns.seed)
    return Options(
        base_obfuscation=ns.base,
        control_flow=ns.control_flow,
        static_environment=ns.static_environment,
        hardcore_globals=ns.hardcore_globals,
        vm_compression=ns.vm_compression,
        advanced_vm_compression=ns.advanced_vm_compression,
        intense_vm_structure=ns.intense_vm_structure,
        virtualization=ns.virtualization,
        optimizations=ns.optimizations,
        seed=ns.seed,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Nigoria Lua obfuscator")
    p.add_argument("input", help="input .lua file (or - for stdin)")
    p.add_argument("-o", "--output", help="output file (default: stdout)")
    p.add_argument("--preset", choices=["free", "pro"], help="feature preset")
    p.add_argument("--base", action="store_true", help="base obfuscation")
    p.add_argument("--control-flow", action="store_true")
    p.add_argument("--static-environment", action="store_true")
    p.add_argument("--hardcore-globals", action="store_true")
    p.add_argument("--vm-compression", action="store_true")
    p.add_argument("--advanced-vm-compression", action="store_true")
    p.add_argument("--intense-vm-structure", action="store_true")
    p.add_argument("--virtualization", action="store_true")
    p.add_argument("--optimizations", action="store_true")
    p.add_argument("--seed", type=int, default=None)
    ns = p.parse_args(argv)

    src = sys.stdin.read() if ns.input == "-" else open(ns.input, encoding="utf-8").read()
    opt = build_options(ns)
    obf = Obfuscator(opt)
    out = obf.obfuscate(src)

    for w in obf.warnings:
        print(f"[warn] {w}", file=sys.stderr)
    if ns.output:
        open(ns.output, "w", encoding="utf-8").write(out)
        print(f"wrote {len(out)} bytes -> {ns.output}", file=sys.stderr)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
