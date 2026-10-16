"""Camada Silver: Padronização, limpeza, deduplicação, SCD Tipo 2 e Quarentena.

Trata toda a 'sujeira' realista injetada nas fontes:
1. Recupera CPFs que perderam zeros à esquerda (`int_cast` em planilhas) e valida dígitos.
2. Padroniza nomes (remoção de espaços extras, normalização unicode e caixa alta).
3. Faz parse de datas e timestamps em 6 formatos legados distintos para ISO-8601.
4. Deduplica exportações duplicadas do SAC (`PROTOCOLO`) e eventos de app (`event_id`).
5. Constrói a dimensão histórica `silver_clientes_scd2` (Slowly Changing Dimension Tipo 2)
   com `effective_from`, `effective_to`, `is_current` e `version_num`.
6. Avalia as suítes de qualidade e envia registros inválidos para `quarantine/`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from customer360.ingestion.bronze import _write_jsonl, read_jsonl
from customer360.quality.expectations import (
    ExpectationSuite,
    SuiteEvaluationResult,
    expect_in_set,
    expect_no_corrupt_record,
    expect_not_null,
    expect_number_between,
    expect_valid_cpf,
)
from novabank_datagen.dirt import DATE_FORMATS, DATETIME_FORMATS, strip_accents
from novabank_datagen.documents import is_valid_cpf, only_digits
from novabank_datagen.nps import nps_category

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(name: str | None) -> str:
    """Padroniza nome: remove acentos, colapsa espaços e converte para maiúsculas."""
    if not name:
        return ""
    cleaned = _WHITESPACE_RE.sub(" ", strip_accents(str(name)).strip()).upper()
    return cleaned


def normalize_cpf(raw_cpf: str | None) -> str | None:
    """Normaliza CPF para 11 dígitos, recuperando zeros à esquerda perdidos em cast numérico."""
    if raw_cpf is None:
        return None
    digits = only_digits(str(raw_cpf))
    if not digits:
        return None
    if len(digits) < 11:
        digits = digits.zfill(11)
    if len(digits) == 11 and is_valid_cpf(digits):
        return digits
    return digits  # mantido para a regra de qualidade barrar se for inválido


def normalize_phone(raw_phone: str | None) -> str | None:
    """Normaliza telefone celular brasileiro (remove +55 se presente)."""
    if not raw_phone:
        return None
    digits = only_digits(str(raw_phone))
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    return digits if digits else None


def normalize_cep(raw_cep: str | None) -> str | None:
    if not raw_cep:
        return None
    digits = only_digits(str(raw_cep))
    return digits.zfill(8) if digits else None


def normalize_email(raw_email: str | None) -> str | None:
    if not raw_email:
        return None
    cleaned = str(raw_email).strip().lower()
    return cleaned if "@" in cleaned else None


def parse_multi_date(raw_val: str | None) -> str | None:
    """Converte data em qualquer um dos formatos legados para YYYY-MM-DD."""
    if not raw_val or not str(raw_val).strip():
        return None
    text = str(raw_val).strip()
    for fmt in (*DATE_FORMATS, *DATETIME_FORMATS):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def parse_multi_datetime(raw_val: str | None) -> str | None:
    """Converte datetime em qualquer formato legado para ISO-8601 `YYYY-MM-DDTHH:MM:SS`."""
    if not raw_val or not str(raw_val).strip():
        return None
    text = str(raw_val).strip()
    for fmt in (*DATETIME_FORMATS, *DATE_FORMATS):
        try:
            return datetime.strptime(text, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).isoformat(timespec="seconds")
    except ValueError:
        return None


def build_scd2_clientes(version_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Constrói dimensão SCD Tipo 2 (`effective_from`, `effective_to`, `is_current`, `version_num`)."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in version_rows:
        cid = row["id_cliente"]
        grouped.setdefault(cid, []).append(row)

    scd2_rows: list[dict[str, Any]] = []
    for cid, versions in sorted(grouped.items()):
        versions.sort(key=lambda r: r.get("updated_at") or "")
        total_v = len(versions)
        for idx, ver in enumerate(versions):
            eff_from = ver.get("updated_at")
            is_current = idx == (total_v - 1)
            eff_to = None if is_current else versions[idx + 1].get("updated_at")
            scd2_rows.append(
                {
                    **ver,
                    "version_num": idx + 1,
                    "effective_from": eff_from,
                    "effective_to": eff_to,
                    "is_current": is_current,
                }
            )
    return scd2_rows


def transform_bronze_to_silver(
    bronze_dir: Path | str,
    silver_dir: Path | str,
    quarantine_dir: Path | str,
) -> dict[str, SuiteEvaluationResult]:
    """Executa toda a transformação Bronze -> Silver com validação e quarentena."""
    b_path = Path(bronze_dir)
    s_path = Path(silver_dir)
    q_path = Path(quarantine_dir)
    s_path.mkdir(parents=True, exist_ok=True)
    q_path.mkdir(parents=True, exist_ok=True)

    reports: dict[str, SuiteEvaluationResult] = {}

    # 1. Core Clientes + SCD Tipo 2
    raw_clientes = read_jsonl(b_path / "core_clientes.jsonl")
    std_clientes: list[dict[str, Any]] = []
    for r in raw_clientes:
        renda_str = str(r.get("renda_mensal") or "").strip()
        renda_val = float(renda_str) if renda_str else None
        std_clientes.append(
            {
                "id_cliente": r.get("id_cliente"),
                "cpf": normalize_cpf(r.get("cpf")),
                "nome_completo": normalize_name(r.get("nome_completo")),
                "data_nascimento": parse_multi_date(r.get("data_nascimento")),
                "sexo": (r.get("sexo") or "").upper(),
                "email": normalize_email(r.get("email")),
                "telefone": normalize_phone(r.get("telefone")),
                "cep": normalize_cep(r.get("cep")),
                "logradouro": normalize_name(r.get("logradouro")),
                "numero": str(r.get("numero") or "").strip(),
                "cidade": normalize_name(r.get("cidade")),
                "uf": (r.get("uf") or "").upper().strip(),
                "renda_mensal": renda_val,
                "segmento": (r.get("segmento") or "MASSA").upper().strip() or "MASSA",
                "status": (r.get("status") or "ATIVO").upper().strip(),
                "data_abertura": parse_multi_date(r.get("data_abertura")),
                "updated_at": parse_multi_datetime(r.get("updated_at")),
                "_batch_id": r.get("_batch_id"),
            }
        )

    clientes_suite = ExpectationSuite(
        "silver_core_clientes_suite",
        [
            expect_not_null("id_cliente"),
            expect_valid_cpf("cpf", allow_empty=False),
            expect_not_null("nome_completo"),
            expect_in_set("status", {"ATIVO", "ENCERRADO", "INATIVO"}),
            expect_number_between("renda_mensal", 0.0, 1_000_000.0, allow_none=True),
            expect_not_null("email", severity="WARNING"),
            expect_not_null("telefone", severity="WARNING"),
        ],
    )
    valid_cli, quar_cli, rep_cli = clientes_suite.evaluate(std_clientes)
    scd2_cli = build_scd2_clientes(valid_cli)
    current_cli = [r for r in scd2_cli if r["is_current"]]
    _write_jsonl(s_path / "clientes_scd2.jsonl", scd2_cli)
    _write_jsonl(s_path / "clientes_current.jsonl", current_cli)
    _write_jsonl(q_path / "core_clientes_quarantine.jsonl", quar_cli)
    reports["core_clientes"] = rep_cli

    # 2. Core Contas
    raw_contas = read_jsonl(b_path / "core_contas.jsonl")
    std_contas = [
        {
            "id_conta": r.get("id_conta"),
            "id_cliente": r.get("id_cliente"),
            "tipo": (r.get("tipo") or "").upper(),
            "status": (r.get("status") or "").upper(),
            "data_abertura": parse_multi_date(r.get("data_abertura")),
            "limite_credito": float(r["limite_credito"]) if r.get("limite_credito") else 0.0,
        }
        for r in raw_contas
    ]
    contas_suite = ExpectationSuite(
        "silver_core_contas_suite",
        [
            expect_not_null("id_conta"),
            expect_not_null("id_cliente"),
            expect_in_set("tipo", {"CORRENTE", "POUPANCA", "CARTAO_CREDITO"}),
        ],
    )
    valid_contas, quar_contas, rep_contas = contas_suite.evaluate(std_contas)
    _write_jsonl(s_path / "contas.jsonl", valid_contas)
    _write_jsonl(q_path / "core_contas_quarantine.jsonl", quar_contas)
    reports["core_contas"] = rep_contas

    # 3. Core Transações
    raw_tx = read_jsonl(b_path / "core_transacoes.jsonl")
    std_tx = [
        {
            "id_transacao": r.get("id_transacao"),
            "id_conta": r.get("id_conta"),
            "data_hora": parse_multi_datetime(r.get("data_hora")),
            "tipo": (r.get("tipo") or "").upper(),
            "valor": float(r.get("valor") or 0.0),
            "canal": (r.get("canal") or "").upper(),
            "descricao": r.get("descricao") or "",
        }
        for r in raw_tx
    ]
    tx_suite = ExpectationSuite(
        "silver_core_transacoes_suite",
        [
            expect_not_null("id_transacao"),
            expect_not_null("id_conta"),
            expect_not_null("data_hora"),
            expect_number_between("valor", 0.01, 5_000_000.0),
        ],
    )
    valid_tx, quar_tx, rep_tx = tx_suite.evaluate(std_tx)
    _write_jsonl(s_path / "transacoes.jsonl", valid_tx)
    _write_jsonl(q_path / "core_transacoes_quarantine.jsonl", quar_tx)
    reports["core_transacoes"] = rep_tx

    # 4. App Usuários
    raw_app_u = read_jsonl(b_path / "app_usuarios.jsonl")
    std_app_u = [
        {
            "app_user_id": r.get("app_user_id"),
            "nome_exibicao": normalize_name(r.get("nome_exibicao")),
            "email": normalize_email(r.get("email")),
            "telefone": normalize_phone(r.get("telefone")),
            "cpf": normalize_cpf(r.get("cpf")),
            "criado_em": parse_multi_datetime(r.get("criado_em")),
        }
        for r in raw_app_u
    ]
    app_u_suite = ExpectationSuite(
        "silver_app_usuarios_suite",
        [
            expect_not_null("app_user_id"),
            expect_valid_cpf("cpf", allow_empty=True),
            expect_not_null("email", severity="WARNING"),
        ],
    )
    valid_app_u, quar_app_u, rep_app_u = app_u_suite.evaluate(std_app_u)
    _write_jsonl(s_path / "app_usuarios.jsonl", valid_app_u)
    _write_jsonl(q_path / "app_usuarios_quarantine.jsonl", quar_app_u)
    reports["app_usuarios"] = rep_app_u

    # 5. App Events (captura JSON truncado na quarentena e deduplica por event_id)
    raw_events = read_jsonl(b_path / "app_events.jsonl")
    seen_events: set[str] = set()
    std_events: list[dict[str, Any]] = []
    for r in raw_events:
        eid = r.get("event_id")
        if eid and eid in seen_events:
            continue
        if eid:
            seen_events.add(eid)
        std_events.append(
            {
                "event_id": eid,
                "app_user_id": r.get("app_user_id"),
                "device_id": r.get("device_id"),
                "event_type": r.get("event_type"),
                "event_ts": parse_multi_datetime(r.get("event_ts")),
                "partition_dt": r.get("_partition_dt"),
                "os": r.get("os"),
                "app_version": r.get("app_version"),
                "properties": r.get("properties") or {},
                "_corrupt_record": r.get("_corrupt_record"),
                "_source_file": r.get("_source_file"),
            }
        )
    events_suite = ExpectationSuite(
        "silver_app_events_suite",
        [
            expect_no_corrupt_record(),
            expect_not_null("event_id"),
            expect_not_null("app_user_id"),
            expect_not_null("event_ts"),
        ],
    )
    valid_ev, quar_ev, rep_ev = events_suite.evaluate(std_events)
    _write_jsonl(s_path / "app_events.jsonl", valid_ev)
    _write_jsonl(q_path / "app_events_quarantine.jsonl", quar_ev)
    reports["app_events"] = rep_ev

    # 6. SAC Atendimentos (deduplica protocolo e padroniza 6 formatos de data e CPF)
    raw_sac = read_jsonl(b_path / "sac_atendimentos.jsonl")
    seen_protocols: set[str] = set()
    std_sac: list[dict[str, Any]] = []
    for r in raw_sac:
        proto = str(r.get("PROTOCOLO") or "").strip()
        if proto and proto in seen_protocols:
            continue
        if proto:
            seen_protocols.add(proto)
        nota_raw = str(r.get("NOTA_ATENDIMENTO") or "").strip()
        std_sac.append(
            {
                "protocolo": proto,
                "cpf": normalize_cpf(r.get("CPF_CLIENTE")),
                "nome_cliente": normalize_name(r.get("NOME_CLIENTE")),
                "telefone": normalize_phone(r.get("TELEFONE")),
                "dt_abertura": parse_multi_datetime(r.get("DT_ABERTURA")),
                "dt_fechamento": parse_multi_datetime(r.get("DT_FECHAMENTO")),
                "canal": (r.get("CANAL") or "").upper().strip(),
                "motivo": (r.get("MOTIVO") or "").strip(),
                "status": (r.get("STATUS") or "").upper().strip(),
                "resolvido_1o_contato": (r.get("RESOLVIDO_1O_CONTATO") or "").upper().strip(),
                "nota_atendimento": int(nota_raw) if nota_raw.isdigit() else None,
            }
        )
    sac_suite = ExpectationSuite(
        "silver_sac_atendimentos_suite",
        [
            expect_not_null("protocolo"),
            expect_valid_cpf("cpf", allow_empty=True),
            expect_not_null("dt_abertura"),
            expect_in_set("status", {"RESOLVIDO", "ABERTO", "CANCELADO"}),
        ],
    )
    valid_sac, quar_sac, rep_sac = sac_suite.evaluate(std_sac)
    _write_jsonl(s_path / "sac_atendimentos.jsonl", valid_sac)
    _write_jsonl(q_path / "sac_atendimentos_quarantine.jsonl", quar_sac)
    reports["sac_atendimentos"] = rep_sac

    # 7. Pesquisas NPS
    raw_nps = read_jsonl(b_path / "nps_respostas.jsonl")
    std_nps: list[dict[str, Any]] = []
    for r in raw_nps:
        nota_val = r.get("nota")
        nota_int = int(nota_val) if nota_val is not None else -1
        std_nps.append(
            {
                "resposta_id": r.get("resposta_id"),
                "pesquisa": r.get("pesquisa"),
                "respondido_em": parse_multi_datetime(r.get("respondido_em")),
                "nome": normalize_name(r.get("nome")),
                "email": normalize_email(r.get("email")),
                "telefone": normalize_phone(r.get("telefone")),
                "nota": nota_int,
                "categoria_nps": nps_category(nota_int) if 0 <= nota_int <= 10 else "INVALIDO",
                "comentario": (r.get("comentario") or "").strip(),
                "_corrupt_record": r.get("_corrupt_record"),
            }
        )
    nps_suite = ExpectationSuite(
        "silver_nps_respostas_suite",
        [
            expect_no_corrupt_record(),
            expect_not_null("resposta_id"),
            expect_number_between("nota", 0, 10),
        ],
    )
    valid_nps, quar_nps, rep_nps = nps_suite.evaluate(std_nps)
    _write_jsonl(s_path / "nps_respostas.jsonl", valid_nps)
    _write_jsonl(q_path / "nps_respostas_quarantine.jsonl", quar_nps)
    reports["nps_respostas"] = rep_nps

    # Salva relatório consolidado de qualidade
    report_payload = {
        k: {
            "suite_name": v.suite_name,
            "evaluated_at": v.evaluated_at,
            "total_rows": v.total_rows,
            "valid_rows_count": v.valid_rows_count,
            "quarantined_rows_count": v.quarantined_rows_count,
            "warning_rows_count": v.warning_rows_count,
            "pass_rate_pct": v.pass_rate_pct,
            "rules": v.rules,
        }
        for k, v in reports.items()
    }
    (s_path / "_quality_report.json").write_text(
        json.dumps(report_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return reports
