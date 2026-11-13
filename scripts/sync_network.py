"""Pull approved public content only; never execute downloaded code."""
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from .site_builder import validate_items
except ImportError:
    from build_site import validate_items

MAX_BYTES = 1_000_000


def fetch(source, opener=urlopen):
    if not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", source):
        raise ValueError("Invalid trusted platform repository")
    request = Request(f"https://raw.githubusercontent.com/{source}/main/published/network.json",
                      headers={"User-Agent": "build-guild-weekly-sync", "Cache-Control": "no-cache"})
    with opener(request, timeout=45) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("Approved network feed exceeds size limit")
    return validate_items(json.loads(payload.decode("utf-8")))


def sync(root, opener=urlopen):
    identity = json.loads((root / ".build-guild-platform.json").read_text(encoding="utf-8"))
    content = fetch(identity["source"], opener)
    # Validation completes before replacing anything; failure leaves the old file untouched.
    destination = root / "data" / "network.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(content, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return content


if __name__ == "__main__":
    sync(Path(__file__).resolve().parents[1])
