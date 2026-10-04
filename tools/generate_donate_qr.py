from __future__ import annotations

from pathlib import Path

import qrcode

WALLETS = {
    "bitcoin": "1HKSxwF6cvgzcpkwjihGJEhfHa9XM2ecrr",
    "ethereum": "0x57364b806b31D33F3cd9d9476d97094A01Ad537b",
    "monero": "4AZdr1RGD7VKfV7Et8Y5PyYwtZeQ5LhUahKPCae7zcZpCW7DgHAs6pXZrvCfH3EmTj6K5vispceUw9cY6GUtzJizRAiTr2L",
}

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "src" / "camero" / "web" / "static" / "donate"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for key, address in WALLETS.items():
        img = qrcode.make(address)
        out_path = OUTPUT_DIR / f"{key}.png"
        img.save(out_path)
        print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
