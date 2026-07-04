import sys
from pathlib import Path

from runner import run_cli


if __name__ == '__main__':
    sys.exit(run_cli(Path(__file__).resolve().parent))
