"""DAG do Apache Airflow: Orquestração diária do Lakehouse Customer 360 (Medallion Architecture).

Fluxo de tarefas:
1. `ingest_bronze_sources`: Ingestão incremental por watermark e detecção de encoding.
2. `transform_silver_and_quarantine`: Limpeza, padronização, SCD Tipo 2 e validação de expectativas.
3. `scala_entity_resolution`: Job de Resolução de Entidades (Jaro-Winkler + Union-Find) e cálculo de BCubed F1.
4. `dbt_build_gold_marts`: Construção de `dim_cliente`, fatos, sentimento de NPS e `mart_customer_360` + score de Churn.
5. `scan_pii_and_openlineage`: Atualização do catálogo de governança LGPD e grafo de linhagem.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from customer360.analytics.gold import build_gold_layer
from customer360.entity_resolution.matcher import resolve_entities
from customer360.governance.lgpd import scan_lakehouse_catalog
from customer360.ingestion.bronze import ingest_to_bronze
from customer360.transformation.silver import transform_bronze_to_silver

try:
    from airflow import DAG
    from airflow.operators.python import PythonOperator
except ImportError:  # Permite importar e testar as funções sem Airflow instalado localmente
    DAG = None  # type: ignore[assignment]
    PythonOperator = None  # type: ignore[assignment]

BASE_DATA_DIR = Path("/opt/airflow/data")

DEFAULT_ARGS = {
    "owner": "data-platform-novabank",
    "depends_on_past": False,
    "email_on_failure": True,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "sla": timedelta(minutes=45),
}


def task_ingest_bronze(base_dir: str = str(BASE_DATA_DIR)) -> dict:
    root = Path(base_dir)
    res = ingest_to_bronze(root / "raw", root / "lakehouse" / "bronze", incremental=True)
    return {"batch_id": res.batch_id, "ingested_at": res.ingested_at}


def task_transform_silver(base_dir: str = str(BASE_DATA_DIR)) -> dict:
    root = Path(base_dir)
    lh = root / "lakehouse"
    reports = transform_bronze_to_silver(lh / "bronze", lh / "silver", lh / "quarantine")
    return {k: v.pass_rate_pct for k, v in reports.items()}


def task_entity_resolution(base_dir: str = str(BASE_DATA_DIR)) -> dict:
    root = Path(base_dir)
    metrics = resolve_entities(
        root / "lakehouse" / "silver",
        ground_truth_dir=root / "raw" / "_ground_truth",
    )
    if metrics.f1_score < 0.85:
        raise RuntimeError(f"Alerta de qualidade no Entity Resolution: F1={metrics.f1_score}")
    return {"f1_score": metrics.f1_score, "total_clusters": metrics.total_clusters}


def task_build_gold(base_dir: str = str(BASE_DATA_DIR)) -> dict:
    root = Path(base_dir)
    lh = root / "lakehouse"
    return build_gold_layer(lh / "silver", lh / "gold")


def task_governance_scan(base_dir: str = str(BASE_DATA_DIR)) -> dict:
    root = Path(base_dir)
    cat = scan_lakehouse_catalog(root / "lakehouse")
    return {"tables_cataloged": len(cat["tables"])}


if DAG is not None:
    with DAG(
        dag_id="novabank_customer360_medallion_daily",
        default_args=DEFAULT_ARGS,
        description="Pipeline diário Bronze -> Silver -> Entity Resolution -> Gold -> Governança LGPD",
        schedule_interval="0 4 * * *",
        start_date=datetime(2026, 1, 1),
        catchup=False,
        tags=["customer360", "lakehouse", "lgpd", "medallion"],
    ) as dag:
        t1 = PythonOperator(task_id="ingest_bronze_sources", python_callable=task_ingest_bronze)
        t2 = PythonOperator(task_id="transform_silver_and_quarantine", python_callable=task_transform_silver)
        t3 = PythonOperator(task_id="scala_entity_resolution", python_callable=task_entity_resolution)
        t4 = PythonOperator(task_id="dbt_build_gold_marts", python_callable=task_build_gold)
        t5 = PythonOperator(task_id="scan_pii_and_openlineage", python_callable=task_governance_scan)

        t1 >> t2 >> t3 >> t4 >> t5
