"""Motor de Qualidade de Dados e Quarentena (estilo Great Expectations / Soda Core).

Permite definir suítes de expectativas declarativas por tabela/fonte:
- `expect_not_null`
- `expect_valid_cpf_or_empty`
- `expect_in_set`
- `expect_number_between`
- `expect_no_corrupt_record`
- `expect_valid_iso_date`

Registros que violam regras críticas (`severity="CRITICAL"`) são roteados para a
camada de **Quarentena** com os motivos detalhados da rejeição (`_failed_expectations`),
enquanto avisos (`severity="WARNING"`) são anotados sem descartar a linha.
Nada é descartado em silêncio!
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from novabank_datagen.documents import is_valid_cpf, only_digits


@dataclass(frozen=True)
class ExpectationRule:
    """Regra individual de validação de qualidade de dados."""

    name: str
    column: str
    description: str
    check: Callable[[dict[str, Any]], bool]
    severity: str = "CRITICAL"  # CRITICAL -> envia para quarentena | WARNING -> apenas alerta


@dataclass
class RuleStats:
    """Estatísticas de execução de uma regra de qualidade."""

    rule_name: str
    column: str
    severity: str
    evaluated: int = 0
    passed: int = 0
    failed: int = 0

    @property
    def pass_rate(self) -> float:
        return round((self.passed / self.evaluated) * 100.0, 2) if self.evaluated > 0 else 100.0


@dataclass
class SuiteEvaluationResult:
    """Resultado da avaliação de uma suíte de expectativas sobre um dataset."""

    suite_name: str
    evaluated_at: str
    total_rows: int
    valid_rows_count: int
    quarantined_rows_count: int
    warning_rows_count: int
    pass_rate_pct: float
    rules: list[dict[str, Any]] = field(default_factory=list)


class ExpectationSuite:
    """Suíte de expectativas para validação e separação Silver vs Quarentena."""

    def __init__(self, suite_name: str, rules: list[ExpectationRule]) -> None:
        self.suite_name = suite_name
        self.rules = rules

    def evaluate(
        self, rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], SuiteEvaluationResult]:
        """Avalia cada registro e separa em `(valid_rows, quarantine_rows, report)`."""
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        stats_map = {
            r.name: RuleStats(rule_name=r.name, column=r.column, severity=r.severity)
            for r in self.rules
        }
        valid_rows: list[dict[str, Any]] = []
        quarantine_rows: list[dict[str, Any]] = []
        warning_count = 0

        for row in rows:
            critical_failures: list[str] = []
            warnings: list[str] = []
            for rule in self.rules:
                st = stats_map[rule.name]
                st.evaluated += 1
                try:
                    ok = bool(rule.check(row))
                except Exception:
                    ok = False
                if ok:
                    st.passed += 1
                else:
                    st.failed += 1
                    if rule.severity == "CRITICAL":
                        critical_failures.append(f"{rule.name}({rule.column})")
                    else:
                        warnings.append(f"{rule.name}({rule.column})")

            if warnings:
                warning_count += 1

            if critical_failures:
                quarantine_rows.append(
                    {
                        **row,
                        "_failed_expectations": critical_failures,
                        "_quality_warnings": warnings,
                        "_quarantined_at": now_iso,
                        "_suite_name": self.suite_name,
                    }
                )
            else:
                valid_rows.append(
                    {
                        **row,
                        "_quality_warnings": warnings,
                        "_validated_at": now_iso,
                    }
                )

        total = len(rows)
        pass_rate = round((len(valid_rows) / total) * 100.0, 2) if total > 0 else 100.0
        report = SuiteEvaluationResult(
            suite_name=self.suite_name,
            evaluated_at=now_iso,
            total_rows=total,
            valid_rows_count=len(valid_rows),
            quarantined_rows_count=len(quarantine_rows),
            warning_rows_count=warning_count,
            pass_rate_pct=pass_rate,
            rules=[
                {**asdict(st), "pass_rate": st.pass_rate}
                for st in stats_map.values()
            ],
        )
        return valid_rows, quarantine_rows, report


# Construtor de regras reutilizáveis
def expect_no_corrupt_record() -> ExpectationRule:
    return ExpectationRule(
        name="expect_no_corrupt_record",
        column="_corrupt_record",
        description="A linha JSON não deve estar truncada ou malformada.",
        check=lambda r: not r.get("_corrupt_record"),
        severity="CRITICAL",
    )


def expect_not_null(column: str, severity: str = "CRITICAL") -> ExpectationRule:
    return ExpectationRule(
        name=f"expect_{column}_not_null",
        column=column,
        description=f"A coluna {column} não deve ser nula ou vazia.",
        check=lambda r: r.get(column) is not None and str(r.get(column)).strip() != "",
        severity=severity,
    )


def expect_valid_cpf(column: str = "cpf", allow_empty: bool = False, severity: str = "CRITICAL") -> ExpectationRule:
    def _check(r: dict[str, Any]) -> bool:
        val = r.get(column)
        if val is None or str(val).strip() == "":
            return allow_empty
        return is_valid_cpf(only_digits(str(val)).zfill(11))

    return ExpectationRule(
        name=f"expect_{column}_valid_cpf",
        column=column,
        description=f"A coluna {column} deve conter um CPF válido pelos dígitos verificadores.",
        check=_check,
        severity=severity,
    )


def expect_in_set(column: str, allowed: set[str], severity: str = "CRITICAL") -> ExpectationRule:
    return ExpectationRule(
        name=f"expect_{column}_in_set",
        column=column,
        description=f"A coluna {column} deve pertencer ao domínio {sorted(allowed)}.",
        check=lambda r: str(r.get(column, "")).upper() in allowed,
        severity=severity,
    )


def expect_number_between(
    column: str, min_val: float, max_val: float, allow_none: bool = False, severity: str = "CRITICAL"
) -> ExpectationRule:
    def _check(r: dict[str, Any]) -> bool:
        val = r.get(column)
        if val is None or str(val).strip() == "":
            return allow_none
        num = float(val)
        return min_val <= num <= max_val

    return ExpectationRule(
        name=f"expect_{column}_between_{min_val}_{max_val}",
        column=column,
        description=f"A coluna {column} deve estar entre {min_val} e {max_val}.",
        check=_check,
        severity=severity,
    )
