"""Orquestrador ponta a ponta do Pipeline Medallion Customer 360 + Linhagem OpenLineage."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from customer360.analytics.gold import build_gold_layer
from customer360.entity_resolution.matcher import resolve_entities
from customer360.governance.lgpd import scan_lakehouse_catalog
from customer360.ingestion.bronze import ingest_to_bronze
from customer360.transformation.silver import transform_bronze_to_silver
from novabank_datagen.config import GeneratorConfig
from novabank_datagen.generator import generate


def build_openlineage_graph() -> dict[str, Any]:
    """Gera o grafo de linhagem de dados no padrão OpenLineage (Bronze -> Silver -> Gold -> Consumo)."""
    return {
        "specVersion": "1.0.5",
        "producer": "https://github.com/Guibas698/novabank-customer360",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nodes": [
            {"id": "raw.core_banking", "layer": "source", "type": "postgres_cdc"},
            {"id": "raw.app_mobile", "layer": "source", "type": "jsonl_events"},
            {"id": "raw.sac_legado", "layer": "source", "type": "csv_latin1"},
            {"id": "raw.nps_surveys", "layer": "source", "type": "jsonl_survey"},
            {"id": "bronze.core_clientes", "layer": "bronze", "type": "dataset"},
            {"id": "bronze.core_contas", "layer": "bronze", "type": "dataset"},
            {"id": "bronze.core_transacoes", "layer": "bronze", "type": "dataset"},
            {"id": "bronze.app_usuarios", "layer": "bronze", "type": "dataset"},
            {"id": "bronze.app_events", "layer": "bronze", "type": "dataset"},
            {"id": "bronze.sac_atendimentos", "layer": "bronze", "type": "dataset"},
            {"id": "bronze.nps_respostas", "layer": "bronze", "type": "dataset"},
            {"id": "silver.clientes_scd2", "layer": "silver", "type": "scd2_dimension"},
            {"id": "silver.golden_customers", "layer": "silver", "type": "entity_resolution"},
            {"id": "silver.entity_crosswalk", "layer": "silver", "type": "crosswalk"},
            {"id": "quarantine.rejected_records", "layer": "quarantine", "type": "dlq"},
            {"id": "gold.dim_cliente", "layer": "gold", "type": "star_dimension"},
            {"id": "gold.fct_transacoes", "layer": "gold", "type": "star_fact"},
            {"id": "gold.fct_atendimentos", "layer": "gold", "type": "star_fact"},
            {"id": "gold.fct_nps", "layer": "gold", "type": "star_fact"},
            {"id": "gold.mart_customer_360", "layer": "gold", "type": "data_product"},
            {"id": "gold.kpis_executivos", "layer": "gold", "type": "kpi_mart"},
        ],
        "edges": [
            {"from": "raw.core_banking", "to": "bronze.core_clientes", "job": "ingest_to_bronze"},
            {"from": "raw.core_banking", "to": "bronze.core_contas", "job": "ingest_to_bronze"},
            {"from": "raw.core_banking", "to": "bronze.core_transacoes", "job": "ingest_to_bronze"},
            {"from": "raw.app_mobile", "to": "bronze.app_usuarios", "job": "ingest_to_bronze"},
            {"from": "raw.app_mobile", "to": "bronze.app_events", "job": "ingest_to_bronze"},
            {"from": "raw.sac_legado", "to": "bronze.sac_atendimentos", "job": "ingest_to_bronze"},
            {"from": "raw.nps_surveys", "to": "bronze.nps_respostas", "job": "ingest_to_bronze"},
            {"from": "bronze.core_clientes", "to": "silver.clientes_scd2", "job": "transform_bronze_to_silver"},
            {"from": "bronze.app_events", "to": "quarantine.rejected_records", "job": "expectation_suite_filter"},
            {"from": "silver.clientes_scd2", "to": "silver.golden_customers", "job": "scala_entity_resolution"},
            {"from": "bronze.app_usuarios", "to": "silver.golden_customers", "job": "scala_entity_resolution"},
            {"from": "bronze.sac_atendimentos", "to": "silver.golden_customers", "job": "scala_entity_resolution"},
            {"from": "bronze.nps_respostas", "to": "silver.golden_customers", "job": "scala_entity_resolution"},
            {"from": "silver.golden_customers", "to": "gold.dim_cliente", "job": "dbt_build_gold"},
            {"from": "silver.golden_customers", "to": "gold.mart_customer_360", "job": "dbt_build_gold"},
            {"from": "gold.mart_customer_360", "to": "gold.kpis_executivos", "job": "compute_executive_kpis"},
        ],
    }


def run_full_pipeline(
    base_dir: Path | str,
    n_customers: int = 400,
    months: int = 3,
    seed: int = 42,
) -> dict[str, Any]:
    """Executa o pipeline completo: Geração -> Bronze -> Silver/Quarentena -> Entity Resolution -> Gold -> Governança."""
    root = Path(base_dir)
    raw_dir = root / "raw"
    lh_dir = root / "lakehouse"
    bronze_dir = lh_dir / "bronze"
    silver_dir = lh_dir / "silver"
    quarantine_dir = lh_dir / "quarantine"
    gold_dir = lh_dir / "gold"
    gov_dir = lh_dir / "governance"

    cfg = GeneratorConfig(seed=seed, n_customers=n_customers, months=months)
    gen_summary = generate(cfg, raw_dir)

    bronze_res = ingest_to_bronze(raw_dir, bronze_dir, incremental=False)
    silver_reports = transform_bronze_to_silver(bronze_dir, silver_dir, quarantine_dir)
    er_metrics = resolve_entities(silver_dir, ground_truth_dir=raw_dir / "_ground_truth")
    kpis = build_gold_layer(silver_dir, gold_dir)
    catalog = scan_lakehouse_catalog(lh_dir)

    lineage = build_openlineage_graph()
    gov_dir.mkdir(parents=True, exist_ok=True)
    (gov_dir / "openlineage_graph.json").write_text(
        json.dumps(lineage, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "generation": asdict(gen_summary),
        "bronze_batch_id": bronze_res.batch_id,
        "silver_quality": {k: v.pass_rate_pct for k, v in silver_reports.items()},
        "entity_resolution": asdict(er_metrics),
        "kpis": kpis,
        "catalog_tables_scanned": len(catalog["tables"]),
    }
