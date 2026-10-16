"""Gerador de dados sintéticos do universo NovaBank.

Gera um ecossistema consistente (e propositalmente sujo) de fontes de dados
de um banco digital fictício: core banking, app, SAC legado e pesquisas NPS,
junto com o gabarito (ground truth) de quem é quem.
"""

from novabank_datagen.config import DirtConfig, GeneratorConfig
from novabank_datagen.generator import GenerationSummary, generate

__all__ = ["DirtConfig", "GeneratorConfig", "GenerationSummary", "generate"]
__version__ = "0.1.0"
