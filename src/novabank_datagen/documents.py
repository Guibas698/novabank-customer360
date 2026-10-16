"""Geração, validação e formatação de documentos brasileiros."""

from __future__ import annotations

import random
import re

_NON_DIGITS = re.compile(r"\D")


def only_digits(value: str | None) -> str:
    """Remove tudo que não for dígito."""
    return _NON_DIGITS.sub("", value or "")


def _check_digit(digits: str) -> str:
    weights = range(len(digits) + 1, 1, -1)
    total = sum(int(d) * w for d, w in zip(digits, weights))
    remainder = total % 11
    return "0" if remainder < 2 else str(11 - remainder)


def cpf_check_digits(base9: str) -> str:
    """Calcula os dois dígitos verificadores de um CPF."""
    if len(base9) != 9 or not base9.isdigit():
        raise ValueError("A base do CPF deve ter 9 dígitos")
    first = _check_digit(base9)
    return first + _check_digit(base9 + first)


def generate_cpf(rng: random.Random) -> str:
    """Gera um CPF válido (11 dígitos, sem formatação)."""
    while True:
        base = "".join(str(rng.randrange(10)) for _ in range(9))
        if len(set(base)) > 1:
            return base + cpf_check_digits(base)


def is_valid_cpf(value: str | None) -> bool:
    """Valida CPF pelos dígitos verificadores."""
    digits = only_digits(value)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    return cpf_check_digits(digits[:9]) == digits[9:]


def format_cpf(value: str) -> str:
    """Formata como 000.000.000-00 (completa zeros à esquerda)."""
    d = only_digits(value).zfill(11)
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def format_phone(phone11: str) -> str:
    """Formata celular de 11 dígitos como (11) 98765-4321."""
    d = only_digits(phone11)
    return f"({d[:2]}) {d[2:7]}-{d[7:]}"


def format_cep(cep8: str) -> str:
    """Formata CEP como 01234-567."""
    d = only_digits(cep8).zfill(8)
    return f"{d[:5]}-{d[5:]}"
