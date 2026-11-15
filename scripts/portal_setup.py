"""Prepare local portal secrets/journal, without account login or trading."""
import secrets
from config import ROOT, load_config
from scripts.docker_setup import prepare


def main():
    prepare(load_config(), require_live=False)
    path = ROOT / ".docker" / "portal.token"
    if not path.exists():
        with path.open("x", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(48))
    if len(path.read_text(encoding="utf-8").strip()) < 32:
        raise ValueError("Invalid portal token")
    print("Login portal ready; account credentials are entered in the browser.")


if __name__ == "__main__":
    main()
