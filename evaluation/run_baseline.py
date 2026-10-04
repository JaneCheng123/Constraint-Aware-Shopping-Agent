"""Compatibility baseline launcher using the common budget and runner."""

import sys
from evaluation.run_experiment import main


if __name__ == "__main__":
    arguments = sys.argv[1:] or ["--output-dir", "results/baseline-cz-v1"]
    main(["--configs", "baseline"] + arguments)
