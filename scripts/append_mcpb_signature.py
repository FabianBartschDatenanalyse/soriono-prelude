from __future__ import annotations

import argparse
import struct
from pathlib import Path

HEADER = b"MCPB_SIG_V1"
FOOTER = b"MCPB_SIG_END"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcpb", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    args = parser.parse_args()
    content = args.mcpb.read_bytes()
    signature = args.signature.read_bytes()
    if HEADER in content[-1_000_000:]:
        raise SystemExit("MCPB already contains a signature block")
    if not signature:
        raise SystemExit("CMS signature is empty")
    block = HEADER + struct.pack("<I", len(signature)) + signature + FOOTER
    args.mcpb.write_bytes(content + block)
    print(f"Appended {len(signature)} signature bytes to {args.mcpb}")


if __name__ == "__main__":
    main()
