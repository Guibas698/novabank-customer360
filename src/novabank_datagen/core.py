"""Fonte 1: Core Banking (Postgres).

É a fonte mais 'limpa', mas tem:
- histórico de versões por cliente (`updated_at`) para carga incremental e SCD2;
- duplicatas herdadas de uma migração de sistema legado;
- contas e transações com volume proporcional ao perfil do cliente.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from faker import Faker

from novabank_datagen.config import GeneratorConfig
from novabank_datagen.dirt import Dirtifier
from novabank_datagen.documents import format_cep, format_cpf, format_phone
from novabank_datagen.people import (
    Person,
    make_address,
    make_email,
    pick_domain,
    poisson,
    random_date,
    random_datetime,
    segment_for_income,
)
from novabank_datagen.reference import BOLETOS, MERCHANTS_CREDITO, MERCHANTS_DEBITO

CLIENTES_FIELDS = [
    "id_cliente", "cpf", "nome_completo", "data_nascimento", "sexo", "email", "telefone",
    "cep", "logradouro", "numero", "cidade", "uf", "renda_mensal", "segmento", "status",
    "data_abertura", "updated_at",
]
CONTAS_FIELDS = ["id_conta", "id_cliente", "tipo", "status", "data_abertura", "limite_credito"]
TRANSACOES_FIELDS = [
    "id_transacao", "id_conta", "data_hora", "tipo", "valor", "canal", "descricao",
]

TS_FMT = "%Y-%m-%d %H:%M:%S"


@dataclass
class CoreData:
    clientes_versions: list[dict] = field(default_factory=list)
    contas: list[dict] = field(default_factory=list)
    transacoes: list[dict] = field(default_factory=list)
    entity_map: list[tuple[str, str, str]] = field(default_factory=list)
    primary_client_id: dict[str, str] = field(default_factory=dict)


def _client_row(id_cliente: str, person: Person, data_abertura: date) -> dict:
    addr = person.address
    return {
        "id_cliente": id_cliente,
        "cpf": format_cpf(person.cpf),
        "nome_completo": person.full_name.upper(),
        "data_nascimento": person.birth_date.isoformat(),
        "sexo": person.sexo,
        "email": person.email,
        "telefone": format_phone(person.phone),
        "cep": format_cep(addr.cep),
        "logradouro": addr.logradouro.upper(),
        "numero": addr.numero,
        "cidade": addr.municipio.nome.upper(),
        "uf": addr.municipio.uf,
        "renda_mensal": f"{person.renda_mensal:.2f}",
        "segmento": person.segmento,
        "status": "ATIVO",
        "data_abertura": data_abertura.isoformat(),
        "updated_at": "",
    }


def _versions_for(
    id_cliente: str, person: Person, cfg: GeneratorConfig, rng: random.Random,
    fake: Faker, dirt: Dirtifier,
) -> list[dict]:
    state = _client_row(id_cliente, person, person.customer_since)
    state["email"] = dirt.maybe_null(state["email"]) or ""
    state["telefone"] = dirt.maybe_null(state["telefone"]) or ""
    created = datetime.combine(person.customer_since, time(rng.randrange(8, 20), rng.randrange(60)))
    versions = [{**state, "updated_at": created.strftime(TS_FMT)}]

    window = person.active_window(cfg)
    if window is None:
        return versions

    for month_start, month_end in cfg.month_ranges():
        start, end = max(month_start, window[0]), min(month_end, window[1])
        if start >= end:
            continue
        changed = False
        if rng.random() < 0.04:  # mudança de endereço
            addr = make_address(rng, fake)
            state.update(
                cep=format_cep(addr.cep), logradouro=addr.logradouro.upper(),
                numero=addr.numero, cidade=addr.municipio.nome.upper(), uf=addr.municipio.uf,
            )
            changed = True
        if rng.random() < 0.03:  # atualização de renda
            renda = round(float(state["renda_mensal"]) * rng.uniform(0.8, 1.4), 2)
            state.update(renda_mensal=f"{renda:.2f}", segmento=segment_for_income(renda))
            changed = True
        if rng.random() < 0.02:  # troca de e-mail
            current_domain = state["email"].split("@")[-1] if state["email"] else None
            state["email"] = make_email(
                rng, person.first_name, person.last_name, pick_domain(rng, current_domain)
            )
            changed = True
        if changed:
            ts = random_datetime(rng, start, end)
            versions.append({**state, "updated_at": ts.strftime(TS_FMT)})

    if person.churn_date and person.churn_date < cfg.end_date:
        ts = datetime.combine(person.churn_date, time(rng.randrange(9, 18), rng.randrange(60)))
        state["status"] = "ENCERRADO"
        versions.append({**state, "updated_at": ts.strftime(TS_FMT)})

    versions.sort(key=lambda v: v["updated_at"])
    return versions


def _legacy_duplicate(
    id_cliente: str, person: Person, rng: random.Random, dirt: Dirtifier
) -> dict:
    """Cadastro duplicado vindo da migração do sistema legado (2015-2017)."""
    opened = random_date(rng, date(2015, 1, 1), date(2018, 1, 1))
    row = _client_row(id_cliente, person, opened)
    row["nome_completo"] = dirt.name_variant(person.full_name, force_typo=True).upper()
    if rng.random() < 0.5:
        row["email"] = make_email(rng, person.first_name, person.last_name)
    if rng.random() < 0.4:
        row["telefone"] = ""
    if rng.random() < 0.5:
        row["renda_mensal"] = ""
        row["segmento"] = ""
    row["updated_at"] = datetime.combine(opened, time(3, 0)).strftime(TS_FMT)
    return row


def _transaction_value(rng: random.Random, tipo: str, renda: float) -> float:
    scale = max(renda, 800.0)
    if tipo == "SALARIO":
        value = renda * rng.uniform(0.95, 1.05)
    elif tipo == "PIX_OUT":
        value = rng.lognormvariate(0, 0.9) * scale * 0.04
    elif tipo == "PIX_IN":
        value = rng.lognormvariate(0, 1.0) * 120
    elif tipo == "BOLETO":
        value = scale * rng.uniform(0.04, 0.3)
    else:  # DEBITO / CREDITO
        value = rng.lognormvariate(0, 0.8) * 45 * (scale / 3000) ** 0.5
    return round(max(value, 1.0), 2)


def _description(rng: random.Random, tipo: str, payee_names: list[str]) -> str:
    if tipo == "PIX_OUT":
        return f"PIX ENVIADO - {rng.choice(payee_names).upper()}"
    if tipo == "PIX_IN":
        return f"PIX RECEBIDO - {rng.choice(payee_names).upper()}"
    if tipo == "DEBITO":
        return rng.choice(MERCHANTS_DEBITO)
    if tipo == "CREDITO":
        return rng.choice(MERCHANTS_CREDITO)
    if tipo == "BOLETO":
        return f"PAGAMENTO BOLETO - {rng.choice(BOLETOS)}"
    return "CREDITO SALARIO"


def build_core(
    people: list[Person], cfg: GeneratorConfig, rng: random.Random, fake: Faker, dirt: Dirtifier
) -> CoreData:
    data = CoreData()
    payee_names = [fake.name() for _ in range(400)]
    client_seq = account_seq = tx_seq = 0

    for person in people:
        client_seq += 1
        id_cliente = f"C{client_seq:07d}"
        data.primary_client_id[person.person_id] = id_cliente
        data.clientes_versions.extend(_versions_for(id_cliente, person, cfg, rng, fake, dirt))
        data.entity_map.append(("core_clientes", id_cliente, person.person_id))

        churned = person.churn_date is not None and person.churn_date < cfg.end_date
        account_status = "ENCERRADA" if churned else "ATIVA"

        accounts: list[tuple[str, str]] = []
        for tipo, prob in (("CORRENTE", 1.0), ("POUPANCA", 0.4), ("CARTAO_CREDITO", 0.6)):
            if rng.random() >= prob:
                continue
            account_seq += 1
            id_conta = f"A{account_seq:08d}"
            limite = (
                f"{round(person.renda_mensal * rng.uniform(0.5, 3.0), -1):.2f}"
                if tipo == "CARTAO_CREDITO" else ""
            )
            data.contas.append({
                "id_conta": id_conta, "id_cliente": id_cliente, "tipo": tipo,
                "status": account_status, "data_abertura": person.customer_since.isoformat(),
                "limite_credito": limite,
            })
            accounts.append((id_conta, tipo))

        if rng.random() < cfg.dirt.duplicate_core_rate:
            client_seq += 1
            dup_id = f"C{client_seq:07d}"
            data.clientes_versions.append(_legacy_duplicate(dup_id, person, rng, dirt))
            data.entity_map.append(("core_clientes", dup_id, person.person_id))
            account_seq += 1
            data.contas.append({
                "id_conta": f"A{account_seq:08d}", "id_cliente": dup_id, "tipo": "CORRENTE",
                "status": "INATIVA", "data_abertura": "2016-01-01", "limite_credito": "",
            })

        window = person.active_window(cfg)
        if window is None:
            continue
        eng = person.engagement
        for month_start, month_end in cfg.month_ranges():
            start, end = max(month_start, window[0]), min(month_end, window[1])
            if start >= end:
                continue
            fraction = (end - start).days / (month_end - month_start).days
            for id_conta, tipo_conta in accounts:
                if tipo_conta == "CORRENTE":
                    plan = {
                        "PIX_OUT": 1 + 7 * eng, "PIX_IN": 3.0, "DEBITO": 1 + 8 * eng,
                        "BOLETO": 2.0,
                    }
                    salary_day = month_start + timedelta(days=4)
                    if start <= salary_day < end:
                        tx_seq += 1
                        ts = datetime.combine(salary_day, time(6, rng.randrange(60)))
                        data.transacoes.append(_tx(tx_seq, id_conta, ts, "SALARIO", rng, person,
                                                   payee_names))
                elif tipo_conta == "CARTAO_CREDITO":
                    plan = {"CREDITO": 2 + 10 * eng}
                else:
                    plan = {"PIX_IN": 0.5}
                for tipo_tx, lam in plan.items():
                    for _ in range(poisson(rng, lam * fraction)):
                        tx_seq += 1
                        ts = random_datetime(rng, start, end)
                        data.transacoes.append(_tx(tx_seq, id_conta, ts, tipo_tx, rng, person,
                                                   payee_names))
    data.transacoes.sort(key=lambda t: (t["data_hora"], t["id_transacao"]))
    return data


def _tx(
    seq: int, id_conta: str, ts: datetime, tipo: str, rng: random.Random, person: Person,
    payee_names: list[str],
) -> dict:
    canal = "POS" if tipo in ("DEBITO", "CREDITO") else rng.choices(
        ("APP", "INTERNET_BANKING", "AGENCIA"), weights=(85, 12, 3)
    )[0]
    if tipo == "SALARIO":
        canal = "TED"
    return {
        "id_transacao": f"T{seq:010d}",
        "id_conta": id_conta,
        "data_hora": ts.strftime(TS_FMT),
        "tipo": tipo,
        "valor": f"{_transaction_value(rng, tipo, person.renda_mensal):.2f}",
        "canal": canal,
        "descricao": _description(rng, tipo, payee_names),
    }
