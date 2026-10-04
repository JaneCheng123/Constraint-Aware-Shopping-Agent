"""Check local prerequisites without loading the environment or calling an API."""

import argparse
import importlib.util
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def missing_prerequisites(num_products=1000):
    missing = []
    for module in ("openai", "gym", "bs4", "flask", "numpy", "torch", "pyserini", "spacy", "thefuzz", "cleantext", "pandas", "rich"):
        if importlib.util.find_spec(module) is None:
            missing.append("Python module: " + module)
    if not os.getenv("DEEPSEEK_API_KEY"):
        missing.append("DEEPSEEK_API_KEY environment variable")
    for filename in ("data/items_shuffle_1000.json", "data/items_ins_v2_1000.json", "data/items_human_ins.json"):
        if not (ROOT / filename).is_file():
            missing.append(filename)
    index = {100: "indexes_100", 1000: "indexes_1k", 100000: "indexes_100k"}.get(num_products, "indexes")
    path = ROOT / "search_engine" / index
    if not path.is_dir() or not any(path.iterdir()):
        missing.append(str(path.relative_to(ROOT)))
    return missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--num-products", type=int, default=1000)
    args = parser.parse_args()
    missing = missing_prerequisites(args.num_products)
    if missing:
        print("Live WebShop run is not ready. Missing:")
        for item in missing:
            print("-", item)
        raise SystemExit(1)
    print("Local files/modules/key are present. Java, spaCy model and API connectivity are verified by the live run.")


if __name__ == "__main__":
    main()
