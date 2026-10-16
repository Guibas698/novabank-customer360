"""Plataforma Customer 360 Lakehouse do NovaBank.

Inclui:
- Ingestão multi-fonte para camada Bronze com watermark incremental e metadados
- Validação de qualidade de dados e Quarentena (estilo Great Expectations)
- Padronização Silver e histórico SCD Tipo 2
- Entity Resolution (Jaro-Winkler + Levenshtein + Union-Find) com avaliação contra Ground Truth
- Motor de Governança e LGPD (classificação de PII, pseudonimização HMAC, mascaramento RBAC e Direito ao Esquecimento)
- Camada Gold, análise de sentimento de NPS, propensão a Churn e KPIs executivos
- API FastAPI (Data Product) e Dashboard executivo
"""

__version__ = "0.1.0"
