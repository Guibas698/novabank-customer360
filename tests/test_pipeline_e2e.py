"""Testes de integração ponta a ponta do Lakehouse Customer 360.

Valida:
1. Ingestão Bronze (incluindo carga incremental por watermark e detecção de linhas corrompidas)
2. Transformação Silver, Quarentena e dimensão histórica SCD Tipo 2
3. Resolução de Entidades (Jaro-Winkler + Union-Find) com F1-Score >= 0.90 contra o Ground Truth
4. Camada Gold (Mart Customer 360, análise de sentimento de NPS, ROC-AUC do modelo de Churn e KPIs)
5. Governança e Motor LGPD (Catálogo PII, Mascaramento RBAC e Direito ao Esquecimento com certificado SHA-256)
6. Endpoints da API FastAPI e Dashboard
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from customer360.api.app import create_app
from customer360.entity_resolution.matcher import jaro_winkler_similarity, levenshtein_similarity
from customer360.governance.lgpd import apply_rbac_masking, execute_right_to_be_forgotten
from customer360.ingestion.bronze import read_jsonl
from customer360.pipeline import run_full_pipeline


def test_similarity_metrics() -> None:
    assert jaro_winkler_similarity("JOAO DA SILVA", "JOAO DA SYLVA") > 0.92
    assert levenshtein_similarity("MARIA OLIVEIRA", "MARIA OLIVERA") > 0.90
    assert jaro_winkler_similarity("CARLOS", "FERNANDA") < 0.60


def test_end_to_end_lakehouse_pipeline_and_lgpd(tmp_path: Path) -> None:
    result = run_full_pipeline(tmp_path, n_customers=150, months=3, seed=42)

    # 1. Qualidade na camada Silver (> 95% de aprovação nos dados padronizados)
    assert result["silver_quality"]["core_clientes"] == 100.0
    assert result["silver_quality"]["sac_atendimentos"] == 100.0

    # 2. Entity Resolution avaliado contra o Ground Truth
    er = result["entity_resolution"]
    assert er["total_clusters"] == 150
    assert er["core_duplicates_merged"] >= 1
    assert er["f1_score"] >= 0.90
    assert er["cluster_purity"] >= 0.92

    # 3. SCD Tipo 2 gerado corretamente
    lh_dir = tmp_path / "lakehouse"
    scd2_rows = read_jsonl(lh_dir / "silver" / "clientes_scd2.jsonl")
    current_rows = [r for r in scd2_rows if r["is_current"]]
    historical_rows = [r for r in scd2_rows if not r["is_current"]]
    assert len(current_rows) >= 150
    assert len(historical_rows) >= 1
    assert all(r["effective_to"] is not None for r in historical_rows)

    # 4. KPIs e Modelo de Churn na camada Gold
    kpis = result["kpis"]
    assert kpis["total_clientes_unicos"] == 150
    assert kpis["churn_model_roc_auc"] >= 0.70
    assert kpis["tpv_total_brl"] > 0

    # 5. RBAC Masking e Direito ao Esquecimento (LGPD)
    mart_rows = read_jsonl(lh_dir / "gold" / "mart_customer_360.jsonl")
    sample_customer = mart_rows[0]
    master_id = sample_customer["master_customer_id"]
    original_cpf = sample_customer["cpf"]

    masked_analista = apply_rbac_masking(sample_customer, role="ANALISTA")
    assert masked_analista["cpf"].startswith("***.")
    assert "***" in masked_analista["email"]

    masked_dpo = apply_rbac_masking(sample_customer, role="DPO")
    assert masked_dpo["cpf"] == original_cpf

    cert = execute_right_to_be_forgotten(lh_dir, master_id)
    assert cert["master_customer_id"] == master_id
    assert cert["total_records_scrubbed"] >= 3
    assert cert["verification_status"] == "VERIFIED_NO_PLAINTEXT_PII_REMAINING"
    assert len(cert["audit_sha256_signature"]) == 64

    # Verifica que o CPF original não existe mais no Mart Gold após o expurgo
    updated_mart = read_jsonl(lh_dir / "gold" / "mart_customer_360.jsonl")
    scrubbed_row = next(r for r in updated_mart if r["master_customer_id"] == master_id)
    assert scrubbed_row["cpf"] != original_cpf
    assert scrubbed_row["nome_completo"] == "TITULAR ANONIMIZADO (LGPD)"

    # 6. Testa a API FastAPI e o Dashboard
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/kpis").json()["total_clientes_unicos"] == 150
    assert client.get("/api/v1/entity-resolution/metrics").json()["f1_score"] >= 0.90
    assert client.get("/api/v1/quality/report").status_code == 200
    assert client.get("/api/v1/governance/catalog").status_code == 200
    assert client.get("/dashboard").status_code == 200

    target_2 = updated_mart[1]["master_customer_id"]
    resp_360 = client.get(f"/api/v1/customers/{target_2}/360?role=ANALISTA")
    assert resp_360.status_code == 200
    assert resp_360.json()["cpf"].startswith("***.")

    resp_forget = client.post(
        "/api/v1/governance/lgpd/forget",
        json={"identifier": target_2, "reason": "Teste E2E Art. 18 LGPD"},
    )
    assert resp_forget.status_code == 200
    assert resp_forget.json()["master_customer_id"] == target_2
