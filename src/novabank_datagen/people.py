"""Geração das pessoas 'reais' (ground truth) do universo NovaBank.

Cada pessoa tem atributos latentes (satisfação e engajamento) que não aparecem
em nenhuma fonte, mas que dirigem o comportamento observável: notas de NPS,
volume de chamados no SAC, uso do app e churn. Isso garante que os KPIs e o
modelo de churn tenham sinal real para encontrar.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from faker import Faker

from novabank_datagen.config import GeneratorConfig
from novabank_datagen.dirt import strip_accents
from novabank_datagen.documents import generate_cpf
from novabank_datagen.reference import EMAIL_DOMAINS, MUNICIPIOS, Municipio

HISTORY_START = date(2018, 1, 1)
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def segment_for_income(renda: float) -> str:
    if renda < 3000:
        return "MASSA"
    if renda < 8000:
        return "VAREJO_ALTO"
    if renda < 20000:
        return "PRIME"
    return "PRIVATE"


def random_date(rng: random.Random, start: date, end_exclusive: date) -> date:
    days = (end_exclusive - start).days
    if days <= 0:
        raise ValueError("Intervalo de datas vazio")
    return start + timedelta(days=rng.randrange(days))


def random_datetime(rng: random.Random, start: date, end_exclusive: date) -> datetime:
    """Datetime aleatório com perfil de horário comercial mais provável."""
    day = random_date(rng, start, end_exclusive)
    hour = rng.choices(range(24), weights=[1, 1, 1, 1, 1, 2, 4, 6, 8, 9, 9, 9,
                                           9, 9, 9, 9, 9, 9, 8, 8, 7, 5, 3, 2])[0]
    return datetime.combine(day, time(hour, rng.randrange(60), rng.randrange(60)))


def poisson(rng: random.Random, lam: float) -> int:
    """Amostra Poisson (Knuth para lambda pequeno, normal para lambda grande)."""
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def slug(text: str) -> str:
    return _NON_ALNUM.sub("", strip_accents(text).lower())


def pick_domain(rng: random.Random, exclude: str | None = None) -> str:
    domains = [d for d, _ in EMAIL_DOMAINS if d != exclude]
    weights = [w for d, w in EMAIL_DOMAINS if d != exclude]
    return rng.choices(domains, weights=weights)[0]


def make_email(rng: random.Random, first: str, last: str, domain: str | None = None) -> str:
    sep = rng.choice((".", "_", ""))
    suffix = str(rng.randrange(1, 999)) if rng.random() < 0.5 else ""
    last_token = slug(last.split()[-1]) if last else ""
    return f"{slug(first)}{sep}{last_token}{suffix}@{domain or pick_domain(rng)}"


def make_phone(rng: random.Random, ddd: str) -> str:
    return f"{ddd}9{rng.randrange(10**8):08d}"


@dataclass(frozen=True)
class Address:
    municipio: Municipio
    cep: str
    logradouro: str
    numero: str


def make_address(rng: random.Random, fake: Faker, municipio: Municipio | None = None) -> Address:
    if municipio is None:
        municipio = rng.choices(MUNICIPIOS, weights=[m.peso for m in MUNICIPIOS])[0]
    prefix = rng.randint(municipio.cep_ini, municipio.cep_fim)
    return Address(
        municipio=municipio,
        cep=f"{prefix:05d}{rng.randrange(1000):03d}",
        logradouro=fake.street_name(),
        numero=str(rng.randint(1, 3500)),
    )


@dataclass(frozen=True)
class Person:
    person_id: str
    cpf: str
    first_name: str
    last_name: str
    sexo: str
    birth_date: date
    email: str
    phone: str
    address: Address
    renda_mensal: float
    customer_since: date
    satisfaction: float
    engagement: float
    churn_date: date | None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def segmento(self) -> str:
        return segment_for_income(self.renda_mensal)

    def active_window(self, cfg: GeneratorConfig) -> tuple[date, date] | None:
        """Intervalo [início, fim) em que a pessoa é cliente dentro do período."""
        start = max(cfg.start_date, self.customer_since)
        end = min(cfg.end_date, self.churn_date) if self.churn_date else cfg.end_date
        return (start, end) if start < end else None


def generate_people(cfg: GeneratorConfig, rng: random.Random, fake: Faker) -> list[Person]:
    people: list[Person] = []
    used_cpfs: dict[str, None] = {}
    new_customer_window_end = cfg.end_date - timedelta(days=7)

    for n in range(cfg.n_customers):
        cpf = generate_cpf(rng)
        while cpf in used_cpfs:
            cpf = generate_cpf(rng)
        used_cpfs[cpf] = None

        sexo = rng.choice(("F", "M"))
        first = fake.first_name_female() if sexo == "F" else fake.first_name_male()
        last = f"{fake.last_name()} {fake.last_name()}" if rng.random() < 0.7 else fake.last_name()
        age_days = rng.randint(18 * 365, 80 * 365)
        address = make_address(rng, fake)
        renda = round(min(rng.lognormvariate(8.0, 0.75), 150_000.0), 2)

        if rng.random() < 0.92 or new_customer_window_end <= cfg.start_date:
            since = random_date(rng, HISTORY_START, cfg.start_date)
        else:
            since = random_date(rng, cfg.start_date, new_customer_window_end)

        satisfaction = rng.betavariate(4, 2)
        engagement = rng.betavariate(2, 2)

        churn_date: date | None = None
        churn_prob = min(0.9, cfg.base_churn_rate * (0.3 + 4.0 * (1 - satisfaction) ** 2))
        churn_from = max(cfg.start_date, since + timedelta(days=30))
        if rng.random() < churn_prob and churn_from < cfg.end_date:
            churn_date = random_date(rng, churn_from, cfg.end_date)

        people.append(
            Person(
                person_id=f"P{n + 1:07d}",
                cpf=cpf,
                first_name=first,
                last_name=last,
                sexo=sexo,
                birth_date=cfg.start_date - timedelta(days=age_days),
                email=make_email(rng, first, last),
                phone=make_phone(rng, address.municipio.ddd),
                address=address,
                renda_mensal=renda,
                customer_since=since,
                satisfaction=round(satisfaction, 4),
                engagement=round(engagement, 4),
                churn_date=churn_date,
            )
        )
    return people
