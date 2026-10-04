"""Compatibility ReAct launcher; historical main results are a separate experiment."""

import sys
from evaluation.run_experiment import main


if __name__ == "__main__":
    arguments = sys.argv[1:] or ["--output-dir", "results/react-cz-v1"]
    main(["--configs", "baseline"] + arguments)
