"""Camada Gold: Modelagem Dimensional, Mart Customer 360, Sentimento de NPS, ML de Churn e KPIs Executivos.

Constrói:
- `dim_cliente.jsonl`
- `fct_transacoes.jsonl`
- `fct_atendimentos.jsonl`
- `fct_nps.jsonl` (enriquecido com análise de sentimento e tópicos em PT-BR)
- `mart_customer_360.jsonl` (visão 360º completa + score e explicabilidade de propensão a Churn)
- `kpis_executivos.json` (indicadores corporativos globais e por segmento)
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from customer360.ingestion.bronze import _write_jsonl, read_jsonl
from novabank_datagen.dirt import strip_accents

_POS_WORDS = {
    "melhor", "rapido", "facil", "excelente", "resolveram", "otimo",
    "recomendo", "pratico", "bom", "elogiar", "sem tarifas", "sem anuidade",
}
_NEG_WORDS = {
    "trava", "nao consigo", "demorado", "esperando", "indevida", "bloqueado",
    "baixo", "ninguem resolve", "pessima", "cancelar", "robo", "problema", "reclamar",
}
_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "app": ("app", "celular", "trava"),
    "pix": ("pix",),
    "atendimento": ("atendimento", "chamado", "chat", "robo", "esperando"),
    "tarifas": ("tarifa", "taxas", "anuidade", "cobraram"),
    "cartao_limite": ("cartao", "limite", "bloqueado"),
}


def analyze_nps_sentiment(comment: str | None, score: int) -> dict[str, Any]:
    """Analisa sentimento e extrai tópicos de comentários livres de NPS em português."""
    text = strip_accents((comment or "").lower())
    if not text.strip():
        polarity = round((score - 5.0) / 5.0, 2)
        label = "POSITIVO" if score >= 9 else ("NEGATIVO" if score <= 6 else "NEUTRO")
        return {"sentiment_score": polarity, "sentiment_label": label, "topics": []}

    pos_hits = sum(1 for w in _POS_WORDS if w in text)
    neg_hits = sum(1 for w in _NEG_WORDS if w in text)
    lexical_score = (pos_hits - neg_hits) / max(1, pos_hits + neg_hits)
    nps_prior = (score - 5.0) / 5.0
    combined = max(-1.0, min(1.0, round(0.65 * lexical_score + 0.35 * nps_prior, 3)))

    if combined >= 0.25:
        label = "POSITIVO"
    elif combined <= -0.25:
        label = "NEGATIVO"
    else:
        label = "NEUTRO"

    topics = [
        topic
        for topic, kws in _TOPIC_KEYWORDS.items()
        if any(kw in text for kw in kws)
    ]
    return {"sentiment_score": combined, "sentiment_label": label, "topics": topics}


def _hours_between(dt_start: str | None, dt_end: str | None) -> float | None:
    if not dt_start or not dt_end:
        return None
    try:
        t1 = datetime.fromisoformat(dt_start)
        t2 = datetime.fromisoformat(dt_end)
        diff = (t2 - t1).total_seconds() / 3600.0
        return round(max(0.0, diff), 2)
    except ValueError:
        return None


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, x))))


def score_churn_propensity(profile: dict[str, Any]) -> tuple[float, str, list[str]]:
    """Calcula probabilidade de churn, faixa de risco e principais fatores explicativos (XAI)."""
    logit = -2.15
    factors: list[str] = []

    if profile.get("teve_chamado_encerramento"):
        logit += 2.6
        factors.append("Chamado recente de encerramento no SAC")

    sac_count = profile.get("qtd_chamados_sac", 0)
    if sac_count >= 3:
        logit += 1.25
        factors.append(f"Alto volume de chamados no SAC ({sac_count})")
    elif sac_count >= 1:
        logit += 0.45

    unresolved = profile.get("qtd_chamados_abertos", 0)
    if unresolved >= 1:
        logit += 0.65
        factors.append("Chamado sem solução no primeiro contato")

    nps_nota = profile.get("ultima_nota_nps")
    if nps_nota is not None:
        if nps_nota <= 6:
            logit += 1.35
            factors.append(f"Detrator na pesquisa NPS (nota {nps_nota})")
        elif nps_nota >= 9:
            logit -= 0.95

    app_events = profile.get("qtd_eventos_app", 0)
    if app_events == 0:
        logit += 0.55
        factors.append("Sem uso recente do aplicativo")
    elif app_events >= 25:
        logit -= 0.50

    tx_count = profile.get("qtd_transacoes", 0)
    if tx_count <= 3:
        logit += 0.50
        factors.append("Baixa movimentação transacional")
    elif tx_count >= 20:
        logit -= 0.40

    prob = round(_sigmoid(logit), 4)
    if prob >= 0.70:
        tier = "CRITICO"
    elif prob >= 0.45:
        tier = "ALTO"
    elif prob >= 0.20:
        tier = "MEDIO"
    else:
        tier = "BAIXO"

    if not factors:
        factors.append("Comportamento estável e engajamento saudável")
    return prob, tier, factors


def compute_roc_auc(y_true: list[int], y_score: list[float]) -> float:
    """Calcula a área sob a curva ROC (ROC-AUC) por estatística de Mann-Whitney U."""
    pairs = sorted(zip(y_score, y_true), key=lambda x: x[0])
    n_pos = sum(y_true)
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5

    rank_sum_pos = 0.0
    for idx, (_, label) in enumerate(pairs, start=1):
        if label == 1:
            rank_sum_pos += idx

    u_stat = rank_sum_pos - (n_pos * (n_pos + 1)) / 2.0
    return round(u_stat / (n_pos * n_neg), 4)


def build_gold_layer(
    silver_dir: Path | str,
    gold_dir: Path | str,
) -> dict[str, Any]:
    """Constrói todas as tabelas dimensionais, o Mart Customer 360 e os KPIs executivos."""
    s_path = Path(silver_dir)
    g_path = Path(gold_dir)
    g_path.mkdir(parents=True, exist_ok=True)

    golden_customers = read_jsonl(s_path / "golden_customers.jsonl")
    crosswalk = read_jsonl(s_path / "entity_crosswalk.jsonl")
    contas = read_jsonl(s_path / "contas.jsonl")
    transacoes = read_jsonl(s_path / "transacoes.jsonl")
    app_events = read_jsonl(s_path / "app_events.jsonl")
    sac_rows = read_jsonl(s_path / "sac_atendimentos.jsonl")
    nps_rows = read_jsonl(s_path / "nps_respostas.jsonl")

    # Mapas de resolução para master_customer_id
    cw_map: dict[tuple[str, str], str] = {
        (r["source"], r["source_record_id"]): r["master_customer_id"]
        for r in crosswalk
    }

    conta_to_master: dict[str, str] = {}
    contas_by_master: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in contas:
        mid = cw_map.get(("core_clientes", c["id_cliente"]))
        if mid:
            conta_to_master[c["id_conta"]] = mid
            contas_by_master[mid].append(c)

    # 1. Fato Transações
    fct_transacoes: list[dict[str, Any]] = []
    tx_by_master: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tx in transacoes:
        mid = conta_to_master.get(tx["id_conta"])
        if not mid:
            continue
        enriched_tx = {**tx, "master_customer_id": mid}
        fct_transacoes.append(enriched_tx)
        tx_by_master[mid].append(enriched_tx)
    _write_jsonl(g_path / "fct_transacoes.jsonl", fct_transacoes)

    # 2. Eventos do App por master_customer_id
    events_by_master: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in app_events:
        mid = cw_map.get(("app_usuarios", ev.get("app_user_id") or ""))
        if mid:
            events_by_master[mid].append(ev)

    # 3. Fato Atendimentos SAC
    fct_atendimentos: list[dict[str, Any]] = []
    sac_by_master: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in sac_rows:
        mid = cw_map.get(("sac_atendimentos", s["protocolo"]))
        dur = _hours_between(s.get("dt_abertura"), s.get("dt_fechamento"))
        enriched_sac = {
            **s,
            "master_customer_id": mid,
            "tempo_resolucao_horas": dur,
        }
        fct_atendimentos.append(enriched_sac)
        if mid:
            sac_by_master[mid].append(enriched_sac)
    _write_jsonl(g_path / "fct_atendimentos.jsonl", fct_atendimentos)

    # 4. Fato NPS com Análise de Sentimento
    fct_nps: list[dict[str, Any]] = []
    nps_by_master: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in nps_rows:
        mid = cw_map.get(("nps_respostas", n["resposta_id"]))
        sent = analyze_nps_sentiment(n.get("comentario"), int(n.get("nota", 5)))
        enriched_nps = {
            **n,
            "master_customer_id": mid,
            **sent,
        }
        fct_nps.append(enriched_nps)
        if mid:
            nps_by_master[mid].append(enriched_nps)
    _write_jsonl(g_path / "fct_nps.jsonl", fct_nps)

    # 5. Dimensão Cliente + Mart Customer 360
    mart_rows: list[dict[str, Any]] = []
    y_true_churn: list[int] = []
    y_score_churn: list[float] = []

    for cust in golden_customers:
        mid = cust["master_customer_id"]
        c_contas = contas_by_master.get(mid, [])
        c_tx = tx_by_master.get(mid, [])
        c_ev = events_by_master.get(mid, [])
        c_sac = sac_by_master.get(mid, [])
        c_nps = sorted(nps_by_master.get(mid, []), key=lambda x: x.get("respondido_em") or "")

        tipos_produto = sorted({c["tipo"] for c in c_contas})
        limite_total = round(sum(float(c.get("limite_credito") or 0.0) for c in c_contas), 2)

        tpv_total = round(sum(float(t["valor"]) for t in c_tx if t["tipo"] != "SALARIO"), 2)
        pix_tx = [t for t in c_tx if t["tipo"] in ("PIX_OUT", "PIX_IN")]
        cartao_tx = [t for t in c_tx if t["tipo"] == "CREDITO"]
        valor_pix = round(sum(float(t["valor"]) for t in pix_tx), 2)
        valor_cartao = round(sum(float(t["valor"]) for t in cartao_tx), 2)

        sac_resolvidos = [s for s in c_sac if s.get("status") == "RESOLVIDO"]
        sac_fcr = [s for s in sac_resolvidos if s.get("resolvido_1o_contato") == "S"]
        fcr_rate = round((len(sac_fcr) / len(sac_resolvidos)) * 100.0, 1) if sac_resolvidos else None
        tempos = [s["tempo_resolucao_horas"] for s in sac_resolvidos if s.get("tempo_resolucao_horas") is not None]
        tempo_medio_sac = round(sum(tempos) / len(tempos), 2) if tempos else None
        teve_encerramento = any("encerramento" in (s.get("motivo") or "").lower() for s in c_sac)
        chamados_abertos = sum(1 for s in c_sac if s.get("status") != "RESOLVIDO")

        last_nps = c_nps[-1] if c_nps else None
        is_churned = cust.get("status") == "ENCERRADO"

        # Estimativa de CLV (margem de intercâmbio + float sobre TPV anualizado)
        renda = float(cust.get("renda_mensal") or 0.0)
        clv_estimado = round((tpv_total * 0.035 + renda * 0.15) * (0.25 if is_churned else 2.4), 2)

        row_360: dict[str, Any] = {
            **cust,
            "qtd_contas": len(c_contas),
            "produtos_contratados": tipos_produto,
            "qtd_produtos": len(tipos_produto),
            "limite_credito_total": limite_total,
            "qtd_transacoes": len(c_tx),
            "tpv_total": tpv_total,
            "qtd_pix": len(pix_tx),
            "valor_total_pix": valor_pix,
            "valor_gasto_cartao": valor_cartao,
            "qtd_eventos_app": len(c_ev),
            "qtd_chamados_sac": len(c_sac),
            "qtd_chamados_abertos": chamados_abertos,
            "taxa_fcr_sac_pct": fcr_rate,
            "tempo_medio_resolucao_sac_h": tempo_medio_sac,
            "teve_chamado_encerramento": teve_encerramento,
            "ultima_nota_nps": last_nps["nota"] if last_nps else None,
            "categoria_nps": last_nps["categoria_nps"] if last_nps else "SEM_RESPOSTA",
            "sentimento_nps": last_nps["sentiment_label"] if last_nps else None,
            "ultimo_comentario_nps": last_nps["comentario"] if last_nps else None,
            "topicos_nps": last_nps["topics"] if last_nps else [],
            "clv_estimado": clv_estimado,
            "is_churned": is_churned,
        }

        prob, tier, factors = score_churn_propensity(row_360)
        row_360["churn_probability"] = prob
        row_360["churn_risk_tier"] = tier
        row_360["churn_top_factors"] = factors

        y_true_churn.append(1 if is_churned else 0)
        y_score_churn.append(prob)
        mart_rows.append(row_360)

    _write_jsonl(g_path / "dim_cliente.jsonl", golden_customers)
    _write_jsonl(g_path / "mart_customer_360.jsonl", mart_rows)

    # 6. Cálculo de KPIs Executivos Globais e por Segmento
    total_clientes = len(mart_rows)
    churned_total = sum(1 for r in mart_rows if r["is_churned"])
    churn_rate_pct = round((churned_total / total_clientes) * 100.0, 2) if total_clientes else 0.0

    nps_validos = [r for r in fct_nps if 0 <= int(r.get("nota", -1)) <= 10]
    promotores = sum(1 for r in nps_validos if r["categoria_nps"] == "PROMOTOR")
    detratores = sum(1 for r in nps_validos if r["categoria_nps"] == "DETRATOR")
    nps_score = (
        round(((promotores - detratores) / len(nps_validos)) * 100.0, 1)
        if nps_validos
        else 0.0
    )

    sac_resolvidos_all = [s for s in fct_atendimentos if s.get("status") == "RESOLVIDO"]
    sac_fcr_all = [s for s in sac_resolvidos_all if s.get("resolvido_1o_contato") == "S"]
    fcr_global_pct = (
        round((len(sac_fcr_all) / len(sac_resolvidos_all)) * 100.0, 2)
        if sac_resolvidos_all
        else 0.0
    )
    all_tempos = [
        s["tempo_resolucao_horas"]
        for s in sac_resolvidos_all
        if s.get("tempo_resolucao_horas") is not None
    ]
    tma_horas = round(sum(all_tempos) / len(all_tempos), 2) if all_tempos else 0.0

    tpv_global = round(sum(r["tpv_total"] for r in mart_rows), 2)
    clv_medio = round(sum(r["clv_estimado"] for r in mart_rows) / total_clientes, 2) if total_clientes else 0.0
    cross_sell_index = round(sum(r["qtd_produtos"] for r in mart_rows) / total_clientes, 2) if total_clientes else 0.0
    roc_auc = compute_roc_auc(y_true_churn, y_score_churn)

    by_segment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in mart_rows:
        by_segment[r["segmento"]].append(r)

    segment_kpis: dict[str, Any] = {}
    for seg, rows in sorted(by_segment.items()):
        n_seg = len(rows)
        seg_churn = sum(1 for r in rows if r["is_churned"])
        seg_nps = [r["ultima_nota_nps"] for r in rows if r["ultima_nota_nps"] is not None]
        seg_prom = sum(1 for v in seg_nps if v >= 9)
        seg_det = sum(1 for v in seg_nps if v <= 6)
        segment_kpis[seg] = {
            "clientes": n_seg,
            "churn_rate_pct": round((seg_churn / n_seg) * 100.0, 2) if n_seg else 0.0,
            "nps_score": round(((seg_prom - seg_det) / len(seg_nps)) * 100.0, 1) if seg_nps else 0.0,
            "clv_medio": round(sum(r["clv_estimado"] for r in rows) / n_seg, 2) if n_seg else 0.0,
            "tpv_total": round(sum(r["tpv_total"] for r in rows), 2),
            "cross_sell_index": round(sum(r["qtd_produtos"] for r in rows) / n_seg, 2) if n_seg else 0.0,
        }

    kpis = {
        "total_clientes_unicos": total_clientes,
        "churn_rate_pct": churn_rate_pct,
        "nps_score": nps_score,
        "clv_medio_brl": clv_medio,
        "tpv_total_brl": tpv_global,
        "fcr_sac_pct": fcr_global_pct,
        "tempo_medio_resolucao_sac_horas": tma_horas,
        "indice_cross_sell": cross_sell_index,
        "churn_model_roc_auc": roc_auc,
        "clientes_risco_critico_ou_alto": sum(
            1 for r in mart_rows if r["churn_risk_tier"] in ("CRITICO", "ALTO")
        ),
        "por_segmento": segment_kpis,
    }
    (g_path / "kpis_executivos.json").write_text(
        json.dumps(kpis, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return kpis
