"""FastAPI Customer 360 Data Product, Governança LGPD & Executive Dashboard."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from customer360.governance.lgpd import apply_rbac_masking, execute_right_to_be_forgotten
from customer360.ingestion.bronze import read_jsonl
from customer360.pipeline import run_full_pipeline

DATA_DIR = Path(os.getenv("CUSTOMER360_DATA_DIR", "data"))


def ensure_lakehouse_ready(data_dir: Path = DATA_DIR) -> Path:
    """Garante que o Lakehouse tenha dados processados ao subir a API."""
    lh_dir = data_dir / "lakehouse"
    if not (lh_dir / "gold" / "mart_customer_360.jsonl").exists():
        run_full_pipeline(data_dir, n_customers=300, months=3, seed=42)
    return lh_dir


class LgpdErasureRequest(BaseModel):
    identifier: str = Field(
        ...,
        description="CPF do titular ou master_customer_id (ex.: M0000001)",
    )
    reason: str = Field(
        default="Solicitação formal do titular - Art. 18, VI da LGPD",
        description="Base legal / justificativa do expurgo",
    )


def create_app(data_dir: Path | str | None = None) -> FastAPI:
    base_path = Path(data_dir) if data_dir else DATA_DIR

    app = FastAPI(
        title="NovaBank Customer 360 & Governance API",
        description=(
            "Data Product do Lakehouse Customer 360: visão unificada do cliente, "
            "métricas de Entity Resolution, Qualidade de Dados, Propensão a Churn e Motor LGPD."
        ),
        version="1.0.0",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy", "service": "novabank-customer360"}

    @app.get("/api/v1/kpis")
    def get_kpis() -> dict[str, Any]:
        lh = ensure_lakehouse_ready(base_path)
        kpi_file = lh / "gold" / "kpis_executivos.json"
        return json.loads(kpi_file.read_text(encoding="utf-8"))

    @app.get("/api/v1/quality/report")
    def get_quality_report() -> dict[str, Any]:
        lh = ensure_lakehouse_ready(base_path)
        q_file = lh / "silver" / "_quality_report.json"
        return json.loads(q_file.read_text(encoding="utf-8"))

    @app.get("/api/v1/entity-resolution/metrics")
    def get_er_metrics() -> dict[str, Any]:
        lh = ensure_lakehouse_ready(base_path)
        er_file = lh / "silver" / "_entity_resolution_metrics.json"
        return json.loads(er_file.read_text(encoding="utf-8"))

    @app.get("/api/v1/governance/catalog")
    def get_governance_catalog() -> dict[str, Any]:
        lh = ensure_lakehouse_ready(base_path)
        cat_file = lh / "governance" / "data_catalog_pii.json"
        lin_file = lh / "governance" / "openlineage_graph.json"
        return {
            "catalog": json.loads(cat_file.read_text(encoding="utf-8")),
            "lineage": json.loads(lin_file.read_text(encoding="utf-8")),
        }

    @app.get("/api/v1/customers")
    def list_customers(
        limit: int = Query(20, ge=1, le=200),
        segmento: str | None = None,
        risk_tier: str | None = None,
        role: str = Query("ANALISTA", description="ANALISTA | ATENDENTE | DPO"),
    ) -> list[dict[str, Any]]:
        lh = ensure_lakehouse_ready(base_path)
        rows = read_jsonl(lh / "gold" / "mart_customer_360.jsonl")
        if segmento:
            rows = [r for r in rows if r.get("segmento") == segmento.upper()]
        if risk_tier:
            rows = [r for r in rows if r.get("churn_risk_tier") == risk_tier.upper()]
        return [apply_rbac_masking(r, role=role) for r in rows[:limit]]

    @app.get("/api/v1/customers/{master_id}/360")
    def get_customer_360(
        master_id: str,
        role: str = Query("ANALISTA", description="Papel RBAC: ANALISTA, ATENDENTE ou DPO"),
    ) -> dict[str, Any]:
        lh = ensure_lakehouse_ready(base_path)
        rows = read_jsonl(lh / "gold" / "mart_customer_360.jsonl")
        for r in rows:
            if r["master_customer_id"] == master_id:
                scd2 = [
                    v
                    for v in read_jsonl(lh / "silver" / "clientes_scd2.jsonl")
                    if v["id_cliente"] in r.get("linked_core_ids", [])
                ]
                profile = apply_rbac_masking(r, role=role)
                profile["scd2_history_count"] = len(scd2)
                return profile
        raise HTTPException(status_code=404, detail=f"Cliente {master_id} não encontrado")

    @app.post("/api/v1/governance/lgpd/forget")
    def lgpd_forget(req: LgpdErasureRequest) -> dict[str, Any]:
        lh = ensure_lakehouse_ready(base_path)
        try:
            return execute_right_to_be_forgotten(lh, req.identifier, req.reason)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> str:
        lh = ensure_lakehouse_ready(base_path)
        kpis = json.loads((lh / "gold" / "kpis_executivos.json").read_text(encoding="utf-8"))
        er = json.loads((lh / "silver" / "_entity_resolution_metrics.json").read_text(encoding="utf-8"))
        return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"/>
<title>NovaBank Customer 360 — Lakehouse & Governança</title>
<style>
  body {{ background:#0b1120; color:#e2e8f0; font-family:Inter,system-ui,sans-serif; margin:0; padding:32px; }}
  h1 {{ margin:0 0 8px; font-size:28px; color:#38bdf8; }}
  .sub {{ color:#94a3b8; margin-bottom:24px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; margin-bottom:28px; }}
  .card {{ background:#1e293b; border:1px solid #334155; border-radius:12px; padding:18px; }}
  .val {{ font-size:26px; font-weight:700; color:#f8fafc; }}
  .lbl {{ font-size:13px; color:#94a3b8; margin-top:4px; }}
  .badge {{ display:inline-block; background:#0284c7; color:#fff; padding:4px 10px; border-radius:999px; font-size:12px; }}
</style>
</head>
<body>
  <h1>🏦 NovaBank Customer 360 Lakehouse</h1>
  <div class="sub">Medallion Architecture • Scala/Python Entity Resolution • SCD Tipo 2 • Motor LGPD • Churn ML</div>
  <div class="grid">
    <div class="card"><div class="val">{kpis['total_clientes_unicos']}</div><div class="lbl">Golden Records (Clientes Únicos)</div></div>
    <div class="card"><div class="val">{er['f1_score'] * 100:.1f}%</div><div class="lbl">Entity Resolution F1-Score (BCubed)</div></div>
    <div class="card"><div class="val">{kpis['nps_score']}</div><div class="lbl">NPS Global (-100 a +100)</div></div>
    <div class="card"><div class="val">{kpis['churn_rate_pct']}%</div><div class="lbl">Taxa de Churn</div></div>
    <div class="card"><div class="val">{kpis['churn_model_roc_auc']}</div><div class="lbl">ROC-AUC Modelo de Churn</div></div>
    <div class="card"><div class="val">R$ {kpis['clv_medio_brl']:,.2f}</div><div class="lbl">CLV Médio Estimado</div></div>
    <div class="card"><div class="val">{kpis['fcr_sac_pct']}%</div><div class="lbl">Resolução 1º Contato (FCR SAC)</div></div>
    <div class="card"><div class="val">{kpis['indice_cross_sell']}</div><div class="lbl">Índice de Cross-Sell (Produtos/Cliente)</div></div>
  </div>
  <p><span class="badge">Swagger OpenAPI</span> Acesse <a style="color:#38bdf8" href="/docs">/docs</a> para testar RBAC Masking e Direito ao Esquecimento (LGPD).</p>
</body>
</html>"""

    return app


app = create_app()
