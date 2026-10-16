"""Fonte 3: SAC legado (exportação CSV mensal, latin-1, separador ';').

Problemas realistas:
- encoding latin-1 e cabeçalho em caixa alta;
- CPF em vários formatos (inclusive sem zeros à esquerda) ou ausente;
- datas em 5 formatos diferentes na mesma coluna;
- linhas exportadas em duplicidade.

O volume de chamados é maior para clientes insatisfeitos, e quem dá churn
costuma abrir um chamado de encerramento pouco antes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from novabank_datagen.config import GeneratorConfig
from novabank_datagen.dirt import Dirtifier
from novabank_datagen.people import Person, poisson, random_datetime
from novabank_datagen.reference import SAC_CANAIS, SAC_MOTIVO_ENCERRAMENTO, SAC_MOTIVOS

ATENDIMENTOS_FIELDS = [
    "PROTOCOLO", "CPF_CLIENTE", "NOME_CLIENTE", "TELEFONE", "DT_ABERTURA", "DT_FECHAMENTO",
    "CANAL", "MOTIVO", "STATUS", "RESOLVIDO_1O_CONTATO", "NOTA_ATENDIMENTO",
]

_CANAIS = [c for c, _ in SAC_CANAIS]
_CANAL_WEIGHTS = [w for _, w in SAC_CANAIS]


@dataclass
class SacData:
    rows_by_month: dict[str, list[dict]] = field(default_factory=dict)
    entity_map: list[tuple[str, str, str]] = field(default_factory=list)
    n_rows: int = 0
    n_duplicates: int = 0


def _ticket(
    protocolo: str, person: Person, opened: datetime, motivo: str, rng: random.Random,
    cfg: GeneratorConfig, dirt: Dirtifier,
) -> dict:
    s = person.satisfaction
    status = rng.choices(("RESOLVIDO", "ABERTO", "CANCELADO"), weights=(80, 12, 8))[0]
    closed = ""
    fcr = ""
    nota = ""
    if status == "RESOLVIDO":
        hours = rng.expovariate(1 / (4 + 60 * (1 - s)))
        closed = dirt.datetime_variant(opened + timedelta(hours=hours))
        fcr = "S" if rng.random() < 0.3 + 0.6 * s else "N"
        nota = str(max(1, min(5, round(1 + 4 * s + rng.gauss(0, 0.8)))))
        nota = dirt.maybe_null(nota, rate=0.3) or ""
    cpf = "" if rng.random() < cfg.dirt.sac_cpf_missing_rate else dirt.cpf_variant(person.cpf)
    return {
        "PROTOCOLO": protocolo,
        "CPF_CLIENTE": cpf,
        "NOME_CLIENTE": dirt.name_variant(person.full_name),
        "TELEFONE": dirt.maybe_null(dirt.phone_variant(person.phone)) or "",
        "DT_ABERTURA": dirt.datetime_variant(opened),
        "DT_FECHAMENTO": closed,
        "CANAL": rng.choices(_CANAIS, weights=_CANAL_WEIGHTS)[0],
        "MOTIVO": motivo,
        "STATUS": status,
        "RESOLVIDO_1O_CONTATO": fcr,
        "NOTA_ATENDIMENTO": nota,
    }


def build_sac(
    people: list[Person], cfg: GeneratorConfig, rng: random.Random, dirt: Dirtifier
) -> SacData:
    data = SacData()
    seq = 0
    period_factor = cfg.months / 3

    def add(row: dict, opened: datetime, person: Person) -> None:
        month_key = opened.strftime("%Y_%m")
        bucket = data.rows_by_month.setdefault(month_key, [])
        bucket.append(row)
        data.entity_map.append(("sac_atendimentos", row["PROTOCOLO"], person.person_id))
        data.n_rows += 1
        if rng.random() < cfg.dirt.sac_duplicate_row_rate:
            bucket.append(dict(row))
            data.n_duplicates += 1

    for person in people:
        window = person.active_window(cfg)
        if window is None:
            continue
        lam = (0.2 + 2.5 * (1 - person.satisfaction) ** 2) * period_factor
        for _ in range(poisson(rng, lam)):
            seq += 1
            opened = random_datetime(rng, window[0], window[1])
            protocolo = f"{opened:%Y%m%d}{seq:06d}"
            add(_ticket(protocolo, person, opened, rng.choice(SAC_MOTIVOS), rng, cfg, dirt),
                opened, person)

        if person.churn_date and person.churn_date < cfg.end_date and rng.random() < 0.6:
            seq += 1
            opened_day = max(window[0], person.churn_date - timedelta(days=rng.randint(1, 10)))
            opened = datetime.combine(opened_day, time(rng.randrange(8, 20), rng.randrange(60)))
            protocolo = f"{opened:%Y%m%d}{seq:06d}"
            add(_ticket(protocolo, person, opened, SAC_MOTIVO_ENCERRAMENTO, rng, cfg, dirt),
                opened, person)

    for rows in data.rows_by_month.values():
        rows.sort(key=lambda r: r["PROTOCOLO"])
    return data
