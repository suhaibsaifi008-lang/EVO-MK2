import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mk2.kernel import main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-voice", action="store_true")
    args = ap.parse_args()
    main(voice=not args.no_voice)
