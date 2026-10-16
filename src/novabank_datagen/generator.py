"""Orquestrador da geração de dados sintéticos do universo NovaBank.

Gera e persiste em disco:
- Core Banking (`core/clientes_versions.csv`, `core/contas.csv`, `core/transacoes.csv`)
- App Mobile (`app/usuarios.csv`, `app/events/dt=YYYY-MM-DD/events.jsonl`)
- SAC Legado (`sac/atendimentos_YYYY_MM.csv` em encoding latin-1 e separador `;`)
- Pesquisas NPS (`nps/respostas_YYYY_MM.jsonl`)
- Tabela IBGE (`gov/municipios_ibge.json`)
- Gabarito Ground Truth (`_ground_truth/pessoas.csv`, `_ground_truth/entity_map.csv`, `summary.json`)
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from faker import Faker

from novabank_datagen.config import GeneratorConfig
from novabank_datagen.core import CLIENTES_FIELDS, CONTAS_FIELDS, TRANSACOES_FIELDS, build_core
from novabank_datagen.digital import USUARIOS_FIELDS, build_app
from novabank_datagen.dirt import Dirtifier
from novabank_datagen.nps import build_nps
from novabank_datagen.people import generate_people
from novabank_datagen.reference import MUNICIPIOS
from novabank_datagen.sac import ATENDIMENTOS_FIELDS, build_sac


@dataclass(frozen=True)
class GenerationSummary:
    """Resumo estatístico do lote de dados gerado."""

    seed: int
    n_people_ground_truth: int
    n_churned_people: int
    n_core_client_versions: int
    n_core_distinct_client_ids: int
    n_core_accounts: int
    n_core_transactions: int
    n_app_users: int
    n_app_events: int
    n_app_late_events: int
    n_app_malformed_events: int
    n_sac_rows: int
    n_sac_duplicate_rows: int
    n_nps_responses: int
    n_entity_links: int


def _write_csv(
    path: Path,
    fieldnames: list[str],
    rows: list[dict],
    encoding: str = "utf-8",
    delimiter: str = ",",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=encoding, newline="", errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def generate(cfg: GeneratorConfig, output_dir: Path | str) -> GenerationSummary:
    """Executa a geração completa e determinística do ecossistema NovaBank."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    rng = random.Random(cfg.seed)
    fake = Faker("pt_BR")
    fake.seed_instance(cfg.seed)
    dirt = Dirtifier(rng=rng, typo_rate=cfg.dirt.typo_rate, null_rate=cfg.dirt.null_rate)

    # 1. Ground truth de pessoas
    people = generate_people(cfg, rng, fake)

    # 2. Fontes operacionais e legadas
    core = build_core(people, cfg, rng, fake, dirt)
    app = build_app(people, cfg, rng, dirt)
    sac = build_sac(people, cfg, rng, dirt)
    nps = build_nps(people, cfg, rng, dirt)

    # 3. Persistência Core Banking (UTF-8, vírgula)
    _write_csv(out / "core" / "clientes_versions.csv", CLIENTES_FIELDS, core.clientes_versions)
    _write_csv(out / "core" / "contas.csv", CONTAS_FIELDS, core.contas)
    _write_csv(out / "core" / "transacoes.csv", TRANSACOES_FIELDS, core.transacoes)

    # 4. Persistência App Mobile (CSV + JSONL particionado por data)
    _write_csv(out / "app" / "usuarios.csv", USUARIOS_FIELDS, app.usuarios)
    for day_str, lines in sorted(app.events_by_day.items()):
        part_file = out / "app" / "events" / f"dt={day_str}" / "events.jsonl"
        part_file.parent.mkdir(parents=True, exist_ok=True)
        part_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 5. Persistência SAC Legado (ISO-8859-1 / latin-1, separador ';')
    for month_key, rows in sorted(sac.rows_by_month.items()):
        sac_file = out / "sac" / f"atendimentos_{month_key}.csv"
        _write_csv(
            sac_file,
            ATENDIMENTOS_FIELDS,
            rows,
            encoding="latin-1",
            delimiter=";",
        )

    # 6. Persistência NPS (JSONL mensal)
    for month_key, lines in sorted(nps.lines_by_month.items()):
        nps_file = out / "nps" / f"respostas_{month_key}.jsonl"
        nps_file.parent.mkdir(parents=True, exist_ok=True)
        nps_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 7. Dados públicos de referência (IBGE)
    gov_file = out / "gov" / "municipios_ibge.json"
    gov_file.parent.mkdir(parents=True, exist_ok=True)
    gov_payload = [asdict(m) for m in MUNICIPIOS]
    gov_file.write_text(json.dumps(gov_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 8. Gabarito (Ground Truth) para avaliação de Entity Resolution, Qualidade e Churn
    gt_people_rows = [
        {
            "person_id": p.person_id,
            "cpf": p.cpf,
            "full_name": p.full_name,
            "sexo": p.sexo,
            "birth_date": p.birth_date.isoformat(),
            "email": p.email,
            "phone": p.phone,
            "cep": p.address.cep,
            "ibge": p.address.municipio.ibge,
            "cidade": p.address.municipio.nome,
            "uf": p.address.municipio.uf,
            "renda_mensal": f"{p.renda_mensal:.2f}",
            "segmento": p.segmento,
            "customer_since": p.customer_since.isoformat(),
            "satisfaction": f"{p.satisfaction:.4f}",
            "engagement": f"{p.engagement:.4f}",
            "churn_date": p.churn_date.isoformat() if p.churn_date else "",
            "primary_core_id": core.primary_client_id[p.person_id],
        }
        for p in people
    ]
    _write_csv(
        out / "_ground_truth" / "pessoas.csv",
        list(gt_people_rows[0].keys()),
        gt_people_rows,
    )

    all_links = core.entity_map + app.entity_map + sac.entity_map + nps.entity_map
    entity_rows = [
        {"source": src, "source_record_id": rec_id, "person_id": pid}
        for src, rec_id, pid in all_links
    ]
    _write_csv(
        out / "_ground_truth" / "entity_map.csv",
        ["source", "source_record_id", "person_id"],
        entity_rows,
    )

    distinct_core_ids = len({r["id_cliente"] for r in core.clientes_versions})
    churned_count = sum(
        1 for p in people if p.churn_date is not None and p.churn_date < cfg.end_date
    )

    summary = GenerationSummary(
        seed=cfg.seed,
        n_people_ground_truth=len(people),
        n_churned_people=churned_count,
        n_core_client_versions=len(core.clientes_versions),
        n_core_distinct_client_ids=distinct_core_ids,
        n_core_accounts=len(core.contas),
        n_core_transactions=len(core.transacoes),
        n_app_users=len(app.usuarios),
        n_app_events=app.n_events,
        n_app_late_events=app.n_late,
        n_app_malformed_events=app.n_malformed,
        n_sac_rows=sac.n_rows,
        n_sac_duplicate_rows=sac.n_duplicates,
        n_nps_responses=nps.n_rows,
        n_entity_links=len(all_links),
    )

    summary_file = out / "_ground_truth" / "summary.json"
    summary_file.write_text(json.dumps(asdict(summary), indent=2), encoding="utf-8")
    return summary
