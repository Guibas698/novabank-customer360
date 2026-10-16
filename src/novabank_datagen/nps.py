"""Fonte 4: Pesquisas NPS (ferramenta externa de survey, JSON Lines mensal).

A ferramenta de pesquisa não tem CPF: o respondente só é identificado por
e-mail, telefone e um nome às vezes incompleto, o que torna a ligação com o
cliente um problema de entity resolution difuso.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

from novabank_datagen.config import GeneratorConfig
from novabank_datagen.dirt import Dirtifier
from novabank_datagen.people import Person, make_email, random_datetime
from novabank_datagen.reference import (
    NPS_COMENTARIOS_DETRATOR,
    NPS_COMENTARIOS_NEUTRO,
    NPS_COMENTARIOS_PROMOTOR,
)


@dataclass
class NpsData:
    lines_by_month: dict[str, list[str]] = field(default_factory=dict)
    entity_map: list[tuple[str, str, str]] = field(default_factory=list)
    n_rows: int = 0


def nps_category(score: int) -> str:
    if score <= 6:
        return "DETRATOR"
    if score <= 8:
        return "NEUTRO"
    return "PROMOTOR"


def _comment(rng: random.Random, score: int) -> str:
    if rng.random() < 0.3:
        return ""
    pool = {
        "DETRATOR": NPS_COMENTARIOS_DETRATOR,
        "NEUTRO": NPS_COMENTARIOS_NEUTRO,
        "PROMOTOR": NPS_COMENTARIOS_PROMOTOR,
    }[nps_category(score)]
    return rng.choice(pool)


def build_nps(
    people: list[Person], cfg: GeneratorConfig, rng: random.Random, dirt: Dirtifier
) -> NpsData:
    data = NpsData()
    seq = 0
    for person in people:
        window = person.active_window(cfg)
        if window is None or rng.random() >= cfg.nps_response_rate:
            continue
        seq += 1
        resposta_id = f"NPS{seq:07d}"
        answered = random_datetime(rng, window[0], window[1])
        score = max(0, min(10, round(person.satisfaction * 10 + rng.gauss(0.3, 1.5))))

        email_roll = rng.random()
        if email_roll < 0.8:
            email: str | None = person.email
        elif email_roll < 0.9:
            email = make_email(rng, person.first_name, person.last_name)
        else:
            email = None
        nome = person.first_name if rng.random() < 0.6 else person.full_name
        record = {
            "resposta_id": resposta_id,
            "pesquisa": f"NPS_RELACIONAMENTO_{answered:%Y_%m}",
            "respondido_em": answered.isoformat(timespec="seconds"),
            "nome": dirt.maybe_typo(nome),
            "email": email,
            "telefone": person.phone if rng.random() < 0.5 else None,
            "nota": score,
            "comentario": _comment(rng, score),
        }
        data.lines_by_month.setdefault(f"{answered:%Y_%m}", []).append(
            json.dumps(record, ensure_ascii=False)
        )
        data.entity_map.append(("nps_respostas", resposta_id, person.person_id))
        data.n_rows += 1
    return data
