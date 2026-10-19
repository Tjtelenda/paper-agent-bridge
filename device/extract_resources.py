#!/usr/bin/env python3
"""Read supported ARM64 Qt resources locally; never execute or distribute firmware."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zlib

FIRMWARE_SHA256 = "0d4bfba6daf8f61138996af95b494d70da11654d4861a98bffe894a425b8caba"


def extract(binary, output):
    if output.exists():
        raise ValueError("Choose a new private output directory")
    if any((parent / ".git").exists() for parent in [output.resolve(), *output.resolve().parents]):
        raise ValueError("Firmware extraction must stay outside Git working trees")
    if binary.stat().st_size > 64000000:
        raise ValueError("Firmware input exceeds size limit")
    data = binary.read_bytes()
    if hashlib.sha256(data).hexdigest() != FIRMWARE_SHA256:
        raise ValueError("Unsupported firmware; offsets require a new compatibility review")
    # Address/file offsets are equal in the inspected executable's relevant segments.
    triples = set()
    for position in range(0x197180, 0xE7D0C4, 4):
        instruction = struct.unpack_from("<I", data, position)[0]
        if instruction & 0x7C000000 != 0x14000000:
            continue
        immediate = instruction & 0x3FFFFFF
        if immediate & 0x2000000:
            immediate -= 0x4000000
        if position + immediate * 4 != 0x18F6D0:
            continue
        registers = {}
        for pc in range(position - 56, position, 4):
            instruction = struct.unpack_from("<I", data, pc)[0]
            target, source = instruction & 31, (instruction >> 5) & 31
            if instruction & 0x9F000000 == 0x90000000:
                value = ((instruction >> 29) & 3) | (((instruction >> 5) & 0x7FFFF) << 2)
                if value & 0x100000:
                    value -= 0x200000
                registers[target] = (pc & ~4095) + (value << 12)
            elif instruction & 0x7F000000 == 0x11000000:
                value = ((instruction >> 10) & 4095) << (12 if instruction & (1 << 22) else 0)
                if source in registers:
                    registers[target] = registers[source] + value
                else:
                    registers.pop(target, None)
        if all(register in registers for register in (1, 2, 3)):
            triples.add(tuple(registers[register] for register in (1, 2, 3)))

    resources = {}
    for tree, names, bodies in sorted(triples):
        seen = set()

        def walk(node, path):
            if node in seen or not 0 <= node < 30000:
                raise ValueError("Invalid Qt resource tree")
            seen.add(node)
            offset = tree + node * 22
            name_offset, flags = struct.unpack_from(">IH", data, offset)
            if node:
                length = struct.unpack_from(">H", data, names + name_offset)[0]
                if not 0 < length < 500:
                    raise ValueError("Invalid resource name length")
                name = data[names + name_offset + 6:names + name_offset + 6 + length * 2].decode("utf-16-be")
                if name in (".", "..") or any(character in name for character in ("/", "\\", "\0")):
                    raise ValueError("Unsafe resource path")
                path = path / name
            if flags & 2:
                count, first = struct.unpack_from(">II", data, offset + 6)
                if count >= 30000:
                    raise ValueError("Resource directory is too large")
                for child in range(first, first + count):
                    walk(child, path)
            else:
                body_offset = struct.unpack_from(">I", data, offset + 10)[0]
                size = struct.unpack_from(">I", data, bodies + body_offset)[0]
                if not 0 <= size < 10000000:
                    raise ValueError("Resource is too large")
                body = data[bodies + body_offset + 4:bodies + body_offset + 4 + size]
                if len(body) != size:
                    raise ValueError("Truncated resource")
                if flags & 1:
                    body = zlib.decompress(body[4:])
                elif flags & 4:
                    body = subprocess.run(["zstd", "-d", "-q", "--stdout"], input=body,
                                          capture_output=True, check=True).stdout
                if path in resources and resources[path] != body:
                    raise ValueError("Conflicting resource paths")
                resources[path] = body

        walk(0, Path())

    if len(triples) != 96 or len(resources) != 1272:
        raise ValueError("Resource inventory differs from the inspected firmware")
    output.mkdir(mode=0o700)
    (output / "xochitl").write_bytes(data)
    (output / "xochitl").chmod(0o600)
    for path, body in resources.items():
        target = output / "resources" / path
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(body)
        target.chmod(0o600)
    (output / "resource-manifest.json").write_text(json.dumps(
        {"sha256": FIRMWARE_SHA256, "trees": len(triples), "resources": len(resources)}, indent=2) + "\n")
    return len(resources)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    print("Extracted resources:", extract(arguments.binary, arguments.output))
