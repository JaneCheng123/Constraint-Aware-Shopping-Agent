"""Compatibility CLI; all evaluations now use the unified experiment runner."""

from evaluation.run_experiment import main as run_experiment


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--query-gate", choices=("on", "off"), default="off")
    parser.add_argument("--product-gate", choices=("on", "off"), default="off")
    parser.add_argument("--num-products", type=int, default=1000)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--dataset", default="evaluation/webshop_test_100.json")
    parser.add_argument("--model", default=None)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--annotations")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    config = ("full" if args.query_gate == args.product_gate == "on" else
              "query" if args.query_gate == "on" else "product" if args.product_gate == "on" else "baseline")
    command = ["--configs", config, "--dataset", args.dataset, "--output-dir", args.output_dir,
               "--num-products", str(args.num_products), "--max-steps", str(args.max_steps)]
    if args.model:
        command += ["--model", args.model]
    if args.annotations:
        command += ["--annotations", args.annotations]
    if args.resume:
        command += ["--resume"]
    return run_experiment(command)


if __name__ == "__main__":
    main()
