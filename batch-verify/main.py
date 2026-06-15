import argparse
import logging
import sys
from pathlib import Path

from verifier import BatchVerifier, load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="批量多线程身份核验（接口可配置）")
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="配置文件路径，默认与本脚本同目录下的 config.yaml",
    )
    parser.add_argument(
        "-i",
        "--input",
        help="覆盖配置中的 TXT 输入路径",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        help="覆盖配置中的线程数",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    base_dir = Path(__file__).resolve().parent
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = base_dir / config_path

    config = load_config(config_path)
    if args.input:
        config["input"]["txt_path"] = args.input
    if args.workers:
        config["concurrency"]["workers"] = args.workers

    try:
        verifier = BatchVerifier(config, base_dir=base_dir)
        out_path = verifier.run()
        print(f"完成，结果文件: {out_path.resolve()}")
    except Exception as exc:
        logging.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
