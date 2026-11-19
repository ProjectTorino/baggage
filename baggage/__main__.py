"""Allow ``python -m baggage``."""

from baggage.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
