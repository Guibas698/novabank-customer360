"""Dados de referência: municípios (códigos IBGE reais), domínios, textos."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Municipio:
    ibge: str
    nome: str
    uf: str
    ddd: str
    cep_ini: int  # 5 primeiros dígitos do CEP
    cep_fim: int
    peso: float  # peso relativo de população


MUNICIPIOS: tuple[Municipio, ...] = (
    Municipio("3550308", "São Paulo", "SP", "11", 1000, 5999, 12.4),
    Municipio("3304557", "Rio de Janeiro", "RJ", "21", 20000, 23799, 6.7),
    Municipio("5300108", "Brasília", "DF", "61", 70000, 72799, 3.0),
    Municipio("2927408", "Salvador", "BA", "71", 40000, 41999, 2.4),
    Municipio("2304400", "Fortaleza", "CE", "85", 60000, 61599, 2.7),
    Municipio("3106200", "Belo Horizonte", "MG", "31", 30000, 31999, 2.3),
    Municipio("1302603", "Manaus", "AM", "92", 69000, 69099, 2.2),
    Municipio("4106902", "Curitiba", "PR", "41", 80000, 82999, 1.9),
    Municipio("2611606", "Recife", "PE", "81", 50000, 52999, 1.5),
    Municipio("5208707", "Goiânia", "GO", "62", 74000, 74899, 1.5),
    Municipio("4314902", "Porto Alegre", "RS", "51", 90000, 91999, 1.3),
    Municipio("1501402", "Belém", "PA", "91", 66000, 66999, 1.3),
    Municipio("3518800", "Guarulhos", "SP", "11", 7000, 7399, 1.3),
    Municipio("3509502", "Campinas", "SP", "19", 13000, 13139, 1.2),
    Municipio("2111300", "São Luís", "MA", "98", 65000, 65109, 1.0),
    Municipio("2704302", "Maceió", "AL", "82", 57000, 57099, 1.0),
    Municipio("5002704", "Campo Grande", "MS", "67", 79000, 79129, 0.9),
    Municipio("2211001", "Teresina", "PI", "86", 64000, 64099, 0.9),
    Municipio("2507507", "João Pessoa", "PB", "83", 58000, 58099, 0.8),
    Municipio("2408102", "Natal", "RN", "84", 59000, 59099, 0.8),
    Municipio("5103403", "Cuiabá", "MT", "65", 78000, 78109, 0.6),
    Municipio("2800308", "Aracaju", "SE", "79", 49000, 49099, 0.6),
    Municipio("3543402", "Ribeirão Preto", "SP", "16", 14000, 14114, 0.7),
    Municipio("3170206", "Uberlândia", "MG", "34", 38400, 38419, 0.7),
    Municipio("4209102", "Joinville", "SC", "47", 89200, 89239, 0.6),
    Municipio("4113700", "Londrina", "PR", "43", 86000, 86099, 0.6),
    Municipio("3303302", "Niterói", "RJ", "21", 24000, 24399, 0.5),
    Municipio("3548500", "Santos", "SP", "13", 11000, 11099, 0.4),
    Municipio("4205407", "Florianópolis", "SC", "48", 88000, 88099, 0.5),
    Municipio("3205309", "Vitória", "ES", "27", 29000, 29099, 0.3),
)

EMAIL_DOMAINS: tuple[tuple[str, float], ...] = (
    ("gmail.com", 50),
    ("hotmail.com", 20),
    ("outlook.com", 10),
    ("yahoo.com.br", 8),
    ("uol.com.br", 5),
    ("bol.com.br", 4),
    ("icloud.com", 3),
)

MERCHANTS_DEBITO: tuple[str, ...] = (
    "SUPERMERCADO EXTRA", "PADARIA PAO QUENTE", "FARMACIA DROGASIL", "POSTO SHELL",
    "UBER *TRIP", "IFOOD *PEDIDO", "RESTAURANTE SABOR CASEIRO", "MERCADINHO DO BAIRRO",
    "ACOUGUE BOI GORDO", "LOJAS AMERICANAS",
)

MERCHANTS_CREDITO: tuple[str, ...] = (
    "MERCADO LIVRE", "AMAZON BR", "NETFLIX.COM", "SPOTIFY", "MAGAZINE LUIZA",
    "SHOPEE", "AIRBNB", "LATAM AIRLINES", "RENNER", "STEAM GAMES",
)

BOLETOS: tuple[str, ...] = (
    "CONTA DE LUZ - ENEL", "INTERNET FIBRA", "ALUGUEL", "CONDOMINIO",
    "CONTA DE AGUA - SABESP", "PLANO DE SAUDE", "MENSALIDADE ESCOLAR", "IPVA",
)

SAC_MOTIVOS: tuple[str, ...] = (
    "Contestação de compra", "Cartão bloqueado", "Problema no Pix", "Dúvida sobre fatura",
    "Aumento de limite", "Tarifa indevida", "Atualização cadastral", "App não funciona",
    "Golpe / Fraude",
)
SAC_MOTIVO_ENCERRAMENTO = "Encerramento de conta"

SAC_CANAIS: tuple[tuple[str, float], ...] = (
    ("TELEFONE", 40),
    ("CHAT", 35),
    ("EMAIL", 15),
    ("AGENCIA", 10),
)

APP_EVENT_TYPES: tuple[tuple[str, float], ...] = (
    ("login", 25),
    ("view_home", 20),
    ("view_extrato", 15),
    ("pix_iniciado", 8),
    ("pix_concluido", 7),
    ("pagamento_boleto", 4),
    ("view_cartao", 8),
    ("solicitar_limite", 1),
    ("chat_suporte", 2),
    ("logout", 10),
)

NPS_COMENTARIOS_DETRATOR: tuple[str, ...] = (
    "O app trava toda hora, não consigo fazer Pix.",
    "Atendimento demorado, fiquei 40 minutos esperando.",
    "Cobraram uma tarifa que eu não reconheço.",
    "Meu cartão foi bloqueado sem aviso.",
    "Limite muito baixo para o meu perfil.",
    "Já abri três chamados e ninguém resolve.",
    "Péssima experiência, vou cancelar minha conta.",
    "O chat só tem robô, não consigo falar com ninguém.",
)

NPS_COMENTARIOS_NEUTRO: tuple[str, ...] = (
    "O app é bom, mas poderia ter mais funcionalidades.",
    "Atende o básico.",
    "Tive um problema, mas resolveram depois de um tempo.",
    "As taxas poderiam ser menores.",
    "Nada a reclamar, nada a elogiar.",
    "O cashback é pouco.",
)

NPS_COMENTARIOS_PROMOTOR: tuple[str, ...] = (
    "Melhor banco que já usei!",
    "Pix rápido e app muito fácil de usar.",
    "Atendimento excelente, resolveram na hora.",
    "Sem tarifas e com ótimo rendimento.",
    "Recomendo para todos os meus amigos.",
    "O cartão sem anuidade é ótimo.",
    "Muito prático, faço tudo pelo celular.",
)
