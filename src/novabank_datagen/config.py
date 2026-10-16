"""Configuração do gerador de dados."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


def add_months(d: date, months: int) -> date:
    """Soma meses a uma data que cai no dia 1."""
    year_offset, month_index = divmod(d.month - 1 + months, 12)
    return date(d.year + year_offset, month_index + 1, 1)


def _check_rate(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} deve estar entre 0 e 1, recebido {value}")


@dataclass(frozen=True)
class DirtConfig:
    """Quanto de 'sujeira' realista injetar em cada fonte."""

    duplicate_core_rate: float = 0.03
    typo_rate: float = 0.15
    null_rate: float = 0.04
    app_cpf_missing_rate: float = 0.40
    app_alt_email_rate: float = 0.20
    sac_cpf_missing_rate: float = 0.10
    sac_duplicate_row_rate: float = 0.01
    late_event_rate: float = 0.02
    malformed_json_rate: float = 0.001

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            _check_rate(name, value)


@dataclass(frozen=True)
class GeneratorConfig:
    """Parâmetros do universo gerado."""

    seed: int = 42
    n_customers: int = 5000
    start_date: date = date(2026, 1, 1)
    months: int = 3
    app_adoption_rate: float = 0.70
    nps_response_rate: float = 0.35
    base_churn_rate: float = 0.08
    dirt: DirtConfig = field(default_factory=DirtConfig)

    def __post_init__(self) -> None:
        if self.n_customers <= 0:
            raise ValueError("n_customers deve ser positivo")
        if not 1 <= self.months <= 24:
            raise ValueError("months deve estar entre 1 e 24")
        if self.start_date.day != 1:
            raise ValueError("start_date deve ser o primeiro dia de um mês")
        _check_rate("app_adoption_rate", self.app_adoption_rate)
        _check_rate("nps_response_rate", self.nps_response_rate)
        _check_rate("base_churn_rate", self.base_churn_rate)

    @property
    def end_date(self) -> date:
        """Data final (exclusiva) do período simulado."""
        return add_months(self.start_date, self.months)

    def month_ranges(self) -> list[tuple[date, date]]:
        """Lista de (início, fim exclusivo) de cada mês simulado."""
        return [
            (add_months(self.start_date, i), add_months(self.start_date, i + 1))
            for i in range(self.months)
        ]
