"""Job PySpark para processamento distribuído Bronze -> Silver com Delta Lake e SCD Tipo 2.

Executa em cluster Spark (ou modo local `local[*]`):
- Padronização de CPF (recuperação de zeros à esquerda), nome, telefone e datas multi-formato
- Separação entre registros válidos (`silver`) e rejeitados (`quarantine`)
- Construção da dimensão `silver.clientes_scd2` usando `Window.partitionBy("id_cliente").orderBy("updated_at")`
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F


def build_spark(app_name: str = "NovaBank-BronzeToSilver") -> SparkSession:
    return (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )


def standardize_clientes_df(raw_df: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Padroniza colunas de clientes e separa válidos vs quarentena."""
    cleaned = (
        raw_df.withColumn("cpf_digits", F.regexp_replace(F.col("cpf"), r"[^0-9]", ""))
        .withColumn("cpf", F.lpad(F.col("cpf_digits"), 11, "0"))
        .withColumn("nome_completo", F.upper(F.trim(F.regexp_replace(F.col("nome_completo"), r"\s+", " "))))
        .withColumn("email", F.lower(F.trim(F.col("email"))))
        .withColumn("telefone", F.regexp_replace(F.col("telefone"), r"[^0-9]", ""))
        .withColumn("cep", F.lpad(F.regexp_replace(F.col("cep"), r"[^0-9]", ""), 8, "0"))
        .withColumn("renda_mensal", F.col("renda_mensal").cast("double"))
        .withColumn("updated_at_ts", F.to_timestamp(F.col("updated_at")))
        .drop("cpf_digits")
    )

    is_valid = (
        F.col("id_cliente").isNotNull()
        & (F.length(F.col("cpf")) == 11)
        & (~F.col("cpf").rlike(r"^(\d)\1{10}$"))
        & F.col("nome_completo").isNotNull()
        & (F.col("nome_completo") != "")
    )

    valid_df = cleaned.filter(is_valid)
    quarantine_df = cleaned.filter(~is_valid).withColumn(
        "_quarantine_reason", F.lit("INVALID_CPF_OR_MANDATORY_FIELD")
    )
    return valid_df, quarantine_df


def build_scd2_spark(valid_clientes_df: DataFrame) -> DataFrame:
    """Constrói dimensão SCD Tipo 2 distribuída com PySpark Window Functions."""
    w = Window.partitionBy("id_cliente").orderBy(F.col("updated_at_ts").asc())
    return (
        valid_clientes_df.withColumn("version_num", F.row_number().over(w))
        .withColumn("effective_from", F.col("updated_at_ts"))
        .withColumn("effective_to", F.lead("updated_at_ts", 1).over(w))
        .withColumn("is_current", F.col("effective_to").isNull())
    )
