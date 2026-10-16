-- models/marts/mart_customer_360.sql
-- Visão 360º do cliente no Data Warehouse / Lakehouse (Gold Layer)

WITH golden_clientes AS (
    SELECT
        master_customer_id,
        primary_id_cliente,
        cpf,
        nome_completo,
        data_nascimento,
        sexo,
        email,
        telefone,
        cidade,
        uf,
        renda_mensal,
        segmento,
        status,
        data_abertura
    FROM {{ source('silver', 'golden_customers') }}
),

transacoes_agg AS (
    SELECT
        cw.master_customer_id,
        COUNT(t.id_transacao) AS qtd_transacoes,
        SUM(CASE WHEN t.tipo <> 'SALARIO' THEN t.valor ELSE 0 END) AS tpv_total,
        COUNT(CASE WHEN t.tipo IN ('PIX_OUT', 'PIX_IN') THEN 1 END) AS qtd_pix,
        SUM(CASE WHEN t.tipo IN ('PIX_OUT', 'PIX_IN') THEN t.valor ELSE 0 END) AS valor_total_pix,
        SUM(CASE WHEN t.tipo = 'CREDITO' THEN t.valor ELSE 0 END) AS valor_gasto_cartao
    FROM {{ source('silver', 'transacoes') }} t
    INNER JOIN {{ source('silver', 'contas') }} c
        ON t.id_conta = c.id_conta
    INNER JOIN {{ source('silver', 'entity_crosswalk') }} cw
        ON cw.source = 'core_clientes' AND cw.source_record_id = c.id_cliente
    GROUP BY cw.master_customer_id
),

sac_agg AS (
    SELECT
        cw.master_customer_id,
        COUNT(s.protocolo) AS qtd_chamados_sac,
        SUM(CASE WHEN s.status <> 'RESOLVIDO' THEN 1 ELSE 0 END) AS qtd_chamados_abertos,
        AVG(CASE WHEN s.resolvido_1o_contato = 'S' THEN 100.0 ELSE 0.0 END) AS taxa_fcr_sac_pct
    FROM {{ source('silver', 'sac_atendimentos') }} s
    INNER JOIN {{ source('silver', 'entity_crosswalk') }} cw
        ON cw.source = 'sac_atendimentos' AND cw.source_record_id = s.protocolo
    GROUP BY cw.master_customer_id
),

nps_latest AS (
    SELECT
        cw.master_customer_id,
        n.nota AS ultima_nota_nps,
        n.categoria_nps,
        n.comentario AS ultimo_comentario_nps,
        ROW_NUMBER() OVER (
            PARTITION BY cw.master_customer_id
            ORDER BY n.respondido_em DESC
        ) AS rn
    FROM {{ source('silver', 'nps_respostas') }} n
    INNER JOIN {{ source('silver', 'entity_crosswalk') }} cw
        ON cw.source = 'nps_respostas' AND cw.source_record_id = n.resposta_id
)

SELECT
    g.master_customer_id,
    g.primary_id_cliente,
    g.cpf,
    g.nome_completo,
    g.segmento,
    g.status,
    g.renda_mensal,
    COALESCE(tx.qtd_transacoes, 0) AS qtd_transacoes,
    COALESCE(tx.tpv_total, 0.0) AS tpv_total,
    COALESCE(tx.qtd_pix, 0) AS qtd_pix,
    COALESCE(tx.valor_total_pix, 0.0) AS valor_total_pix,
    COALESCE(tx.valor_gasto_cartao, 0.0) AS valor_gasto_cartao,
    COALESCE(sc.qtd_chamados_sac, 0) AS qtd_chamados_sac,
    COALESCE(sc.qtd_chamados_abertos, 0) AS qtd_chamados_abertos,
    sc.taxa_fcr_sac_pct,
    np.ultima_nota_nps,
    COALESCE(np.categoria_nps, 'SEM_RESPOSTA') AS categoria_nps,
    CASE WHEN g.status = 'ENCERRADO' THEN TRUE ELSE FALSE END AS is_churned
FROM golden_clientes g
LEFT JOIN transacoes_agg tx ON g.master_customer_id = tx.master_customer_id
LEFT JOIN sac_agg sc ON g.master_customer_id = sc.master_customer_id
LEFT JOIN nps_latest np ON g.master_customer_id = np.master_customer_id AND np.rn = 1
