"""Camada Bronze: Ingestão multi-fonte com watermark incremental e metadados de auditoria.

Responsabilidades:
1. Carga incremental por watermark (`updated_at` / `data_hora`) para tabelas do Core Banking.
2. Detecção automática de encoding (`utf-8` vs `latin-1`) para arquivos CSV legados do SAC.
3. Ingestão tolerante a falhas de JSON Lines (App Events e NPS), capturando registros
   corrompidos na coluna `_corrupt_record` sem abortar o lote.
4. Enriquecimento de todas as linhas com metadados técnicos:
   `_ingested_at`, `_source`, `_batch_id`, `_file_hash`, `_source_file`, `_row_num`.
"""

from __future__ import annotations

import csv
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class SourceIngestionStats:
    """Métricas de ingestão de uma fonte específica."""

    source: str
    files_read: int = 0
    rows_ingested: int = 0
    corrupt_rows: int = 0
    watermark_before: str | None = None
    watermark_after: str | None = None


@dataclass
class BronzeIngestionResult:
    """Resultado consolidado de um lote de ingestão na camada Bronze."""

    batch_id: str
    ingested_at: str
    sources: dict[str, SourceIngestionStats] = field(default_factory=dict)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _read_text_auto_encoding(path: Path) -> tuple[str, str]:
    """Lê arquivo texto detectando automaticamente UTF-8 ou ISO-8859-1 (latin-1)."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


class WatermarkStore:
    """Armazena o estado de watermark incremental de cada fonte."""

    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self._state: dict[str, str] = {}
        if state_file.exists():
            self._state = json.loads(state_file.read_text(encoding="utf-8"))

    def get(self, source: str) -> str | None:
        return self._state.get(source)

    def set(self, source: str, value: str) -> None:
        self._state[source] = value
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self._state, indent=2), encoding="utf-8")


def ingest_to_bronze(
    raw_dir: Path | str,
    bronze_dir: Path | str,
    incremental: bool = True,
) -> BronzeIngestionResult:
    """Executa a ingestão de todas as fontes de `raw_dir` para `bronze_dir`."""
    raw_path = Path(raw_dir)
    bronze_path = Path(bronze_dir)
    bronze_path.mkdir(parents=True, exist_ok=True)

    batch_id = f"batch_{uuid.uuid4().hex[:10]}"
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    watermarks = WatermarkStore(bronze_path / "_metadata" / "watermarks.json")
    result = BronzeIngestionResult(batch_id=batch_id, ingested_at=now_iso)

    # 1. Core Banking (CSV UTF-8 com watermark incremental quando aplicável)
    core_configs = [
        ("core_clientes", raw_path / "core" / "clientes_versions.csv", "updated_at"),
        ("core_contas", raw_path / "core" / "contas.csv", "data_abertura"),
        ("core_transacoes", raw_path / "core" / "transacoes.csv", "data_hora"),
    ]
    for source_name, csv_file, wm_col in core_configs:
        stats = SourceIngestionStats(source=source_name)
        if csv_file.exists():
            stats.files_read = 1
            prev_wm = watermarks.get(source_name) if incremental else None
            stats.watermark_before = prev_wm
            max_wm = prev_wm
            file_hash = _file_sha256(csv_file)

            existing = read_jsonl(bronze_path / f"{source_name}.jsonl") if prev_wm else []
            new_rows: list[dict[str, Any]] = []
            text, _ = _read_text_auto_encoding(csv_file)
            reader = csv.DictReader(text.splitlines(), delimiter=",")
            for idx, row in enumerate(reader, start=1):
                wm_val = row.get(wm_col, "")
                if prev_wm and wm_val and wm_val <= prev_wm:
                    continue
                if wm_val and (max_wm is None or wm_val > max_wm):
                    max_wm = wm_val
                enriched = {
                    **row,
                    "_ingested_at": now_iso,
                    "_source": source_name,
                    "_batch_id": batch_id,
                    "_file_hash": file_hash,
                    "_source_file": csv_file.name,
                    "_row_num": idx,
                }
                new_rows.append(enriched)

            all_rows = existing + new_rows
            _write_jsonl(bronze_path / f"{source_name}.jsonl", all_rows)
            stats.rows_ingested = len(new_rows)
            stats.watermark_after = max_wm
            if max_wm:
                watermarks.set(source_name, max_wm)
        result.sources[source_name] = stats

    # 2. App Mobile - Usuários (CSV)
    app_users_file = raw_path / "app" / "usuarios.csv"
    app_users_stats = SourceIngestionStats(source="app_usuarios")
    if app_users_file.exists():
        app_users_stats.files_read = 1
        file_hash = _file_sha256(app_users_file)
        text, _ = _read_text_auto_encoding(app_users_file)
        rows = [
            {
                **row,
                "_ingested_at": now_iso,
                "_source": "app_usuarios",
                "_batch_id": batch_id,
                "_file_hash": file_hash,
                "_source_file": app_users_file.name,
                "_row_num": idx,
            }
            for idx, row in enumerate(csv.DictReader(text.splitlines()), start=1)
        ]
        _write_jsonl(bronze_path / "app_usuarios.jsonl", rows)
        app_users_stats.rows_ingested = len(rows)
    result.sources["app_usuarios"] = app_users_stats

    # 3. App Mobile - Eventos (JSONL particionado por dt=YYYY-MM-DD, com linhas corrompidas)
    app_events_stats = SourceIngestionStats(source="app_events")
    event_files = sorted((raw_path / "app" / "events").glob("dt=*/events.jsonl"))
    event_rows: list[dict[str, Any]] = []
    for ev_file in event_files:
        app_events_stats.files_read += 1
        file_hash = _file_sha256(ev_file)
        partition_dt = ev_file.parent.name.replace("dt=", "")
        text, _ = _read_text_auto_encoding(ev_file)
        for idx, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
                parsed["_corrupt_record"] = None
            except json.JSONDecodeError:
                parsed = {"_corrupt_record": line}
                app_events_stats.corrupt_rows += 1
            parsed.update(
                _partition_dt=partition_dt,
                _ingested_at=now_iso,
                _source="app_events",
                _batch_id=batch_id,
                _file_hash=file_hash,
                _source_file=f"dt={partition_dt}/{ev_file.name}",
                _row_num=idx,
            )
            event_rows.append(parsed)
            app_events_stats.rows_ingested += 1
    _write_jsonl(bronze_path / "app_events.jsonl", event_rows)
    result.sources["app_events"] = app_events_stats

    # 4. SAC Legado (CSV em latin-1 e separador ';')
    sac_stats = SourceIngestionStats(source="sac_atendimentos")
    sac_files = sorted((raw_path / "sac").glob("atendimentos_*.csv"))
    sac_rows: list[dict[str, Any]] = []
    for sac_file in sac_files:
        sac_stats.files_read += 1
        file_hash = _file_sha256(sac_file)
        text, detected_enc = _read_text_auto_encoding(sac_file)
        reader = csv.DictReader(text.splitlines(), delimiter=";")
        for idx, row in enumerate(reader, start=1):
            sac_rows.append(
                {
                    **row,
                    "_detected_encoding": detected_enc,
                    "_ingested_at": now_iso,
                    "_source": "sac_atendimentos",
                    "_batch_id": batch_id,
                    "_file_hash": file_hash,
                    "_source_file": sac_file.name,
                    "_row_num": idx,
                }
            )
            sac_stats.rows_ingested += 1
    _write_jsonl(bronze_path / "sac_atendimentos.jsonl", sac_rows)
    result.sources["sac_atendimentos"] = sac_stats

    # 5. Pesquisas NPS (JSONL mensal)
    nps_stats = SourceIngestionStats(source="nps_respostas")
    nps_files = sorted((raw_path / "nps").glob("respostas_*.jsonl"))
    nps_rows: list[dict[str, Any]] = []
    for nps_file in nps_files:
        nps_stats.files_read += 1
        file_hash = _file_sha256(nps_file)
        text, _ = _read_text_auto_encoding(nps_file)
        for idx, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
                parsed["_corrupt_record"] = None
            except json.JSONDecodeError:
                parsed = {"_corrupt_record": line}
                nps_stats.corrupt_rows += 1
            parsed.update(
                _ingested_at=now_iso,
                _source="nps_respostas",
                _batch_id=batch_id,
                _file_hash=file_hash,
                _source_file=nps_file.name,
                _row_num=idx,
            )
            nps_rows.append(parsed)
            nps_stats.rows_ingested += 1
    _write_jsonl(bronze_path / "nps_respostas.jsonl", nps_rows)
    result.sources["nps_respostas"] = nps_stats

    # 6. Referência IBGE
    ibge_file = raw_path / "gov" / "municipios_ibge.json"
    if ibge_file.exists():
        ibge_data = json.loads(ibge_file.read_text(encoding="utf-8"))
        _write_jsonl(bronze_path / "gov_municipios_ibge.jsonl", ibge_data)

    # Salva manifesto da execução Bronze
    manifest_path = bronze_path / "_metadata" / "last_ingestion.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "batch_id": result.batch_id,
                "ingested_at": result.ingested_at,
                "sources": {k: asdict(v) for k, v in result.sources.items()},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return result
