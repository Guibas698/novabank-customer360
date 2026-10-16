"""DAG do Apache Airflow: Execução automatizada do Direito ao Esquecimento (Art. 18 LGPD)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from customer360.governance.lgpd import execute_right_to_be_forgotten

try:
    from airflow import DAG
    from airflow.operators.python import PythonOperator
except ImportError:
    DAG = None  # type: ignore[assignment]
    PythonOperator = None  # type: ignore[assignment]


def run_lgpd_erasure_job(**context) -> dict:
    conf = (context.get("dag_run") and context["dag_run"].conf) or {}
    identifier = conf.get("identifier")
    reason = conf.get("reason", "Solicitação via Portal de Privacidade DPO (Art. 18 LGPD)")
    lakehouse_dir = Path(conf.get("lakehouse_dir", "/opt/airflow/data/lakehouse"))
    if not identifier:
        raise ValueError("Parâmetro 'identifier' (CPF ou master_customer_id) é obrigatório.")
    return execute_right_to_be_forgotten(lakehouse_dir, identifier, reason)


if DAG is not None:
    with DAG(
        dag_id="novabank_lgpd_right_to_be_forgotten",
        description="Expurgo/anonimização em Bronze, Silver e Gold com certificado SHA-256",
        schedule_interval=None,
        start_date=datetime(2026, 1, 1),
        catchup=False,
        tags=["lgpd", "governance", "security"],
    ) as dag:
        PythonOperator(
            task_id="execute_erasure_and_emit_certificate",
            python_callable=run_lgpd_erasure_job,
        )
