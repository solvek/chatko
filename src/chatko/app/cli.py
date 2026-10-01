"""The `chatko` command line. `run` and `check-config` come with the wiring (roadmap S14, S15)."""

import argparse
from collections.abc import Sequence

from chatko import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chatko", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    return 0
