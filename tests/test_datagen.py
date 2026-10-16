"""Testes unitários e de propriedades do gerador de dados sintéticos do NovaBank."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from novabank_datagen.config import GeneratorConfig
from novabank_datagen.documents import format_cpf, generate_cpf, is_valid_cpf
from novabank_datagen.generator import generate


def test_cpf_generation_and_validation() -> None:
    import random

    rng = random.Random(123)
    for _ in range(50):
        cpf = generate_cpf(rng)
        assert len(cpf) == 11
        assert is_valid_cpf(cpf)
        assert is_valid_cpf(format_cpf(cpf))
    assert not is_valid_cpf("111.111.111-11")
    assert not is_valid_cpf("12345678900")


def test_deterministic_generation(tmp_path: Path) -> None:
    cfg = GeneratorConfig(seed=99, n_customers=80, start_date=date(2026, 1, 1), months=2)
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"

    sum1 = generate(cfg, out1)
    sum2 = generate(cfg, out2)

    assert sum1 == sum2
    assert sum1.n_people_ground_truth == 80
    assert sum1.n_core_distinct_client_ids >= 80  # inclui duplicatas legadas
    assert (out1 / "core" / "clientes_versions.csv").read_bytes() == (
        out2 / "core" / "clientes_versions.csv"
    ).read_bytes()

    # Verifica que o arquivo SAC foi gravado em latin-1 e separador ';'
    sac_files = list((out1 / "sac").glob("atendimentos_*.csv"))
    assert len(sac_files) >= 1
    raw_bytes = sac_files[0].read_bytes()
    decoded = raw_bytes.decode("latin-1")
    assert "PROTOCOLO;CPF_CLIENTE;NOME_CLIENTE" in decoded

    # Verifica gabarito ground truth
    summary_json = json.loads((out1 / "_ground_truth" / "summary.json").read_text(encoding="utf-8"))
    assert summary_json["n_people_ground_truth"] == 80
