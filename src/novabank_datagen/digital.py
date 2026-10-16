"""Fonte 2: App mobile (cadastro do app + eventos de navegação em JSON Lines).

Problemas realistas:
- o app tem cadastro próprio: nome de exibição, e-mail que pode ser diferente
  do core e CPF muitas vezes ausente (entity resolution difícil);
- eventos que chegam atrasados (gravados em partições de dias seguintes);
- linhas de JSON truncadas (precisam ir para quarentena).
"""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from novabank_datagen.config import GeneratorConfig
from novabank_datagen.dirt import Dirtifier
from novabank_datagen.people import Person, make_email, poisson, random_datetime
from novabank_datagen.reference import APP_EVENT_TYPES

USUARIOS_FIELDS = ["app_user_id", "nome_exibicao", "email", "telefone", "cpf", "criado_em"]

_EVENT_NAMES = [e for e, _ in APP_EVENT_TYPES]
_EVENT_WEIGHTS = [w for _, w in APP_EVENT_TYPES]


@dataclass
class AppData:
    usuarios: list[dict] = field(default_factory=list)
    events_by_day: dict[str, list[str]] = field(default_factory=dict)
    entity_map: list[tuple[str, str, str]] = field(default_factory=list)
    n_events: int = 0
    n_late: int = 0
    n_malformed: int = 0


def _display_name(rng: random.Random, person: Person, dirt: Dirtifier) -> str:
    style = rng.choices(("first", "first_last", "full"), weights=(30, 50, 20))[0]
    if style == "first":
        name = person.first_name
    elif style == "first_last":
        name = f"{person.first_name} {person.last_name.split()[-1]}"
    else:
        name = person.full_name
    return dirt.maybe_typo(name)


def _uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def build_app(
    people: list[Person], cfg: GeneratorConfig, rng: random.Random, dirt: Dirtifier
) -> AppData:
    data = AppData()
    seq = 0
    for person in people:
        window = person.active_window(cfg)
        if window is None or rng.random() >= cfg.app_adoption_rate:
            continue
        seq += 1
        app_user_id = f"U{seq:07d}"
        email = person.email
        if rng.random() < cfg.dirt.app_alt_email_rate:
            email = make_email(rng, person.first_name, person.last_name)
        created = random_datetime(rng, person.customer_since, person.customer_since
                                  + timedelta(days=60))
        data.usuarios.append({
            "app_user_id": app_user_id,
            "nome_exibicao": _display_name(rng, person, dirt),
            "email": email.lower(),
            "telefone": dirt.phone_variant(person.phone),
            "cpf": "" if rng.random() < cfg.dirt.app_cpf_missing_rate else person.cpf,
            "criado_em": created.isoformat(timespec="seconds"),
        })
        data.entity_map.append(("app_usuarios", app_user_id, person.person_id))

        devices = [_uuid(rng) for _ in range(rng.choice((1, 1, 1, 2)))]
        os_name = rng.choice(("android", "android", "ios"))
        for month_start, month_end in cfg.month_ranges():
            start, end = max(month_start, window[0]), min(month_end, window[1])
            if start >= end:
                continue
            fraction = (end - start).days / (month_end - month_start).days
            for _ in range(poisson(rng, (2 + 28 * person.engagement) * fraction)):
                ts = random_datetime(rng, start, end)
                event_type = rng.choices(_EVENT_NAMES, weights=_EVENT_WEIGHTS)[0]
                props: dict[str, object] = {}
                if event_type in ("pix_iniciado", "pix_concluido"):
                    props["valor"] = round(rng.lognormvariate(4.5, 1.0), 2)
                if event_type.startswith("view_"):
                    props["tempo_tela_seg"] = rng.randint(2, 240)
                event = {
                    "event_id": _uuid(rng),
                    "app_user_id": app_user_id,
                    "device_id": rng.choice(devices),
                    "event_type": event_type,
                    "event_ts": ts.isoformat(timespec="seconds"),
                    "os": os_name,
                    "app_version": rng.choice(("5.2.0", "5.2.1", "5.3.0")),
                    "properties": props,
                }
                line = json.dumps(event, ensure_ascii=False)
                partition_day = ts.date()
                if rng.random() < cfg.dirt.late_event_rate:
                    partition_day += timedelta(days=rng.randint(1, 3))
                    data.n_late += 1
                if rng.random() < cfg.dirt.malformed_json_rate:
                    line = line[: len(line) // 2]
                    data.n_malformed += 1
                data.events_by_day.setdefault(partition_day.isoformat(), []).append(line)
                data.n_events += 1
    return data
