"""Interface de linha de comando para o gerador de dados sintéticos do NovaBank."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from novabank_datagen.config import GeneratorConfig
from novabank_datagen.generator import generate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="novabank-datagen",
        description="Gera dados sintéticos multi-fonte do ecossistema NovaBank.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("data/raw"),
        help="Diretório de saída dos arquivos brutos (padrão: data/raw)",
    )
    parser.add_argument(
        "--customers",
        "-n",
        type=int,
        default=1000,
        help="Quantidade de pessoas únicas no ground truth (padrão: 1000)",
    )
    parser.add_argument(
        "--months",
        "-m",
        type=int,
        default=3,
        help="Quantidade de meses simulados (padrão: 3)",
    )
    parser.add_argument(
        "--seed",
        "-s",
        type=int,
        default=42,
        help="Seed determinística (padrão: 42)",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default="2026-01-01",
        help="Data inicial no formato YYYY-MM-01 (padrão: 2026-01-01)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = GeneratorConfig(
        seed=args.seed,
        n_customers=args.customers,
        start_date=date.fromisoformat(args.start_date),
        months=args.months,
    )
    summary = generate(cfg, args.output)
    print(json.dumps(asdict(summary), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
