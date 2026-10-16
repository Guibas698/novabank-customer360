"""Funções de 'sujeira' realista: erros de digitação, formatos variados, nulos."""

from __future__ import annotations

import random
import unicodedata
from datetime import date, datetime
from typing import TypeVar

from novabank_datagen.documents import format_cpf, format_phone, only_digits

T = TypeVar("T")

KEYBOARD_NEIGHBORS: dict[str, str] = {
    "a": "qwsz", "b": "vghn", "c": "xdfv", "d": "serfcx", "e": "wsdr", "f": "drtgvc",
    "g": "ftyhbv", "h": "gyujnb", "i": "ujko", "j": "huikmn", "k": "jiolm", "l": "kop",
    "m": "njk", "n": "bhjm", "o": "iklp", "p": "ol", "q": "wa", "r": "edft", "s": "awedxz",
    "t": "rfgy", "u": "yhji", "v": "cfgb", "w": "qase", "x": "zsdc", "y": "tghu", "z": "asx",
}

# Formatos que aparecem nas fontes legadas (a camada silver precisa entender todos).
DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y%m%d", "%d/%m/%y", "%d.%m.%Y",
)
DATETIME_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%Y%m%d%H%M%S", "%d/%m/%y %H:%M",
)


def strip_accents(text: str) -> str:
    """Remove acentos: 'João' -> 'Joao'."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


class Dirtifier:
    """Aplica sujeira de forma determinística (a partir de um `random.Random`)."""

    def __init__(self, rng: random.Random, typo_rate: float, null_rate: float) -> None:
        self.rng = rng
        self.typo_rate = typo_rate
        self.null_rate = null_rate

    def typo(self, text: str) -> str:
        """Introduz exatamente um erro de digitação (preserva o 1º caractere)."""
        if len(text) < 4:
            return text
        i = self.rng.randrange(1, len(text) - 1)
        op = self.rng.choice(("swap", "drop", "replace", "double"))
        if op == "swap":
            return text[:i] + text[i + 1] + text[i] + text[i + 2:]
        if op == "drop":
            return text[:i] + text[i + 1:]
        if op == "double":
            return text[:i] + text[i] + text[i:]
        char = text[i]
        neighbors = KEYBOARD_NEIGHBORS.get(strip_accents(char).lower())
        if not neighbors:
            return text[:i] + text[i + 1:]
        new_char = self.rng.choice(neighbors)
        new_char = new_char.upper() if char.isupper() else new_char
        return text[:i] + new_char + text[i + 1:]

    def maybe_typo(self, text: str) -> str:
        return self.typo(text) if self.rng.random() < self.typo_rate else text

    def maybe_null(self, value: T, rate: float | None = None) -> T | None:
        threshold = self.null_rate if rate is None else rate
        return None if self.rng.random() < threshold else value

    def name_variant(self, full_name: str, force_typo: bool = False) -> str:
        """Mesma pessoa, digitada de outro jeito em outro sistema."""
        style = self.rng.choices(
            ("original", "upper", "lower", "upper_no_accents", "no_accents"),
            weights=(30, 30, 10, 20, 10),
        )[0]
        variants = {
            "original": full_name,
            "upper": full_name.upper(),
            "lower": full_name.lower(),
            "upper_no_accents": strip_accents(full_name).upper(),
            "no_accents": strip_accents(full_name),
        }
        name = variants[style]
        return self.typo(name) if force_typo else self.maybe_typo(name)

    def cpf_variant(self, cpf: str) -> str:
        """Formatos de CPF vistos em sistemas legados (inclui o bug do zero à esquerda)."""
        style = self.rng.choices(
            ("formatted", "digits", "int_cast", "trailing_space"), weights=(40, 35, 15, 10)
        )[0]
        digits = only_digits(cpf)
        if style == "formatted":
            return format_cpf(digits)
        if style == "int_cast":
            return str(int(digits))  # planilha que converteu CPF para número
        if style == "trailing_space":
            return f" {digits} "
        return digits

    def phone_variant(self, phone11: str) -> str:
        d = only_digits(phone11)
        style = self.rng.choices(
            ("formatted", "digits", "e164", "no_ddd", "spaces"), weights=(35, 30, 15, 10, 10)
        )[0]
        if style == "formatted":
            return format_phone(d)
        if style == "e164":
            return f"+55 {d[:2]} {d[2:7]}-{d[7:]}"
        if style == "no_ddd":
            return d[2:]
        if style == "spaces":
            return f"{d[:2]} {d[2:7]} {d[7:]}"
        return d

    def date_variant(self, d: date) -> str:
        return d.strftime(self.rng.choice(DATE_FORMATS))

    def datetime_variant(self, dt: datetime) -> str:
        return dt.strftime(self.rng.choice(DATETIME_FORMATS))
