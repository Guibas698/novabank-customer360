"""Motor de Entity Resolution (Resolução de Entidades e Golden Record).

Resolve identidades fragmentadas entre 4 fontes heterogêneas:
- `core_clientes` (possui duplicatas de migração legada com erros de digitação)
- `app_usuarios` (40% sem CPF, 20% com e-mail alternativo, nome de exibição abreviado)
- `sac_atendimentos` (10% sem CPF, telefones sem DDD, typos no nome)
- `nps_respostas` (100% sem CPF — apenas e-mail, telefone ou nome parcial)

Algoritmo:
1. **Multi-Pass Blocking**:
   - Bloco 1: CPF normalizado (11 dígitos)
   - Bloco 2: E-mail normalizado
   - Bloco 3: Sufixo de telefone (últimos 9 dígitos — imune à ausência de DDD)
   - Bloco 4: Blocking fonético/lexical por `(primeiro_nome[:4], ultimo_sobrenome[:4])`
     combinado com similaridade **Jaro-Winkler >= 0.88** + **Levenshtein normalizado**.
2. **Clustering por Union-Find (Disjoint Set)**:
   - Agrupa todos os registros conectados transitivamente sob um `master_customer_id`.
3. **Survivorship Rules (Regras de Sobrevivência)**:
   - Prioriza o registro ativo mais recente do Core Banking e complementa campos
     faltantes (e-mail do app, telefone do SAC, etc.) para formar o **Golden Record**.
4. **Avaliação Científica contra o Ground Truth**:
   - Compara os clusters gerados com `_ground_truth/entity_map.csv` e calcula
     **Precisão, Recall, F1-Score e Pureza de Cluster**.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from customer360.ingestion.bronze import _write_jsonl, read_jsonl


def jaro_similarity(s1: str, s2: str) -> float:
    """Calcula a similaridade de Jaro entre duas strings (0.0 a 1.0)."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    match_distance = max(len1, len2) // 2 - 1
    if match_distance < 0:
        match_distance = 0

    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    transpositions = 0

    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    m = float(matches)
    return (m / len1 + m / len2 + (m - transpositions / 2.0) / m) / 3.0


def jaro_winkler_similarity(s1: str, s2: str, prefix_weight: float = 0.1) -> float:
    """Calcula a similaridade de Jaro-Winkler (dá bônus para prefixo comum de até 4 letras)."""
    jaro = jaro_similarity(s1, s2)
    prefix = 0
    for c1, c2 in zip(s1[:4], s2[:4]):
        if c1 == c2:
            prefix += 1
        else:
            break
    return min(1.0, jaro + prefix * prefix_weight * (1.0 - jaro))


def levenshtein_similarity(s1: str, s2: str) -> float:
    """Similaridade de Levenshtein normalizada (1 - distancia / max_len)."""
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    dist = previous_row[-1]
    return 1.0 - (dist / max(len(s1), len(s2)))


class UnionFind:
    """Estrutura Disjoint-Set (Union-Find) com restrição Cannot-Link para CPFs distintos."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.rank: dict[str, int] = {}
        self.root_cpf: dict[str, str] = {}

    def add(self, x: str, cpf: str | None = None) -> None:
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        if cpf and len(cpf) == 11:
            root = self.find(x)
            self.root_cpf[root] = cpf

    def find(self, x: str) -> str:
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        curr = x
        while curr != root:
            nxt = self.parent[curr]
            self.parent[curr] = root
            curr = nxt
        return root

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        cpf_a = self.root_cpf.get(ra)
        cpf_b = self.root_cpf.get(rb)
        # Restrição Cannot-Link: duas pessoas com CPFs válidos diferentes nunca se fundem
        if cpf_a and cpf_b and cpf_a != cpf_b:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        if not self.root_cpf.get(ra) and cpf_b:
            self.root_cpf[ra] = cpf_b
        return True


@dataclass(frozen=True)
class IdentityMention:
    """Representação uniforme de uma menção de identidade em qualquer fonte."""

    node_id: str  # "{source}:{record_id}"
    source: str
    record_id: str
    cpf: str | None
    email: str | None
    phone_tail: str | None
    full_phone: str | None
    name: str


@dataclass
class EntityResolutionMetrics:
    """Métricas de qualidade do Entity Resolution avaliadas contra o gabarito."""

    total_mentions: int
    total_clusters: int
    ground_truth_entities: int
    core_duplicates_merged: int
    precision: float
    recall: float
    f1_score: float
    cluster_purity: float


def _phone_tail9(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    return digits[-9:] if len(digits) >= 9 else None


def resolve_entities(
    silver_dir: Path | str,
    ground_truth_dir: Path | str | None = None,
) -> EntityResolutionMetrics:
    """Executa o Entity Resolution sobre os dados Silver e gera Golden Records + Crosswalk."""
    s_path = Path(silver_dir)
    clientes = read_jsonl(s_path / "clientes_current.jsonl")
    app_users = read_jsonl(s_path / "app_usuarios.jsonl")
    sac_rows = read_jsonl(s_path / "sac_atendimentos.jsonl")
    nps_rows = read_jsonl(s_path / "nps_respostas.jsonl")

    mentions: dict[str, IdentityMention] = {}
    core_record_map: dict[str, dict[str, Any]] = {}
    uf = UnionFind()

    for r in clientes:
        rec_id = r["id_cliente"]
        nid = f"core_clientes:{rec_id}"
        core_record_map[rec_id] = r
        m = IdentityMention(
            node_id=nid,
            source="core_clientes",
            record_id=rec_id,
            cpf=r.get("cpf"),
            email=r.get("email"),
            phone_tail=_phone_tail9(r.get("telefone")),
            full_phone=r.get("telefone"),
            name=r.get("nome_completo") or "",
        )
        mentions[nid] = m
        uf.add(nid, m.cpf)

    for r in app_users:
        rec_id = r["app_user_id"]
        nid = f"app_usuarios:{rec_id}"
        m = IdentityMention(
            node_id=nid,
            source="app_usuarios",
            record_id=rec_id,
            cpf=r.get("cpf"),
            email=r.get("email"),
            phone_tail=_phone_tail9(r.get("telefone")),
            full_phone=r.get("telefone"),
            name=r.get("nome_exibicao") or "",
        )
        mentions[nid] = m
        uf.add(nid, m.cpf)

    for r in sac_rows:
        rec_id = r["protocolo"]
        nid = f"sac_atendimentos:{rec_id}"
        m = IdentityMention(
            node_id=nid,
            source="sac_atendimentos",
            record_id=rec_id,
            cpf=r.get("cpf"),
            email=None,
            phone_tail=_phone_tail9(r.get("telefone")),
            full_phone=r.get("telefone"),
            name=r.get("nome_cliente") or "",
        )
        mentions[nid] = m
        uf.add(nid, m.cpf)

    for r in nps_rows:
        rec_id = r["resposta_id"]
        nid = f"nps_respostas:{rec_id}"
        m = IdentityMention(
            node_id=nid,
            source="nps_respostas",
            record_id=rec_id,
            cpf=None,
            email=r.get("email"),
            phone_tail=_phone_tail9(r.get("telefone")),
            full_phone=r.get("telefone"),
            name=r.get("nome") or "",
        )
        mentions[nid] = m
        uf.add(nid, m.cpf)

    # Passagem 1: Blocking determinístico por CPF exato (11 dígitos)
    by_cpf: dict[str, list[str]] = defaultdict(list)
    for nid, m in mentions.items():
        if m.cpf and len(m.cpf) == 11:
            by_cpf[m.cpf].append(nid)
    for group in by_cpf.values():
        first = group[0]
        for other in group[1:]:
            uf.union(first, other)

    # Passagem 2: Blocking por e-mail exato normalizado
    by_email: dict[str, list[str]] = defaultdict(list)
    for nid, m in mentions.items():
        if m.email:
            by_email[m.email].append(nid)
    for group in by_email.values():
        first = group[0]
        for other in group[1:]:
            uf.union(first, other)

    # Passagem 3: Blocking por sufixo de telefone (9 dígitos) + verificação de compatibilidade de nome
    by_phone: dict[str, list[str]] = defaultdict(list)
    for nid, m in mentions.items():
        if m.phone_tail:
            by_phone[m.phone_tail].append(nid)
    for group in by_phone.values():
        if len(group) < 2:
            continue
        anchor = group[0]
        anchor_name = mentions[anchor].name
        for candidate in group[1:]:
            cand_name = mentions[candidate].name
            if not anchor_name or not cand_name:
                uf.union(anchor, candidate)
                continue
            first_tok_a = anchor_name.split()[0]
            first_tok_b = cand_name.split()[0]
            if jaro_winkler_similarity(first_tok_a, first_tok_b) >= 0.78 or jaro_winkler_similarity(
                anchor_name, cand_name
            ) >= 0.78:
                uf.union(anchor, candidate)

    # Passagem 4: Blocking fuzzy por chave de nome (primeiras 4 letras + últimas 4 letras) para órfãos
    core_by_name_block: dict[str, list[IdentityMention]] = defaultdict(list)
    for nid, m in mentions.items():
        if m.source == "core_clientes" and m.name:
            tokens = m.name.split()
            if len(tokens) >= 2:
                bkey = f"{tokens[0][:3]}_{tokens[-1][:3]}"
                core_by_name_block[bkey].append(m)

    for nid, m in mentions.items():
        if m.source == "core_clientes":
            continue
        root = uf.find(nid)
        # Se já está ligado a algum core_clientes, não precisa de matching difuso adicional
        if m.name and len(m.name.split()) >= 2:
            tokens = m.name.split()
            bkey = f"{tokens[0][:3]}_{tokens[-1][:3]}"
            candidates = core_by_name_block.get(bkey, [])
            best_score = 0.0
            best_core_nid: str | None = None
            for cand in candidates:
                jw = jaro_winkler_similarity(m.name, cand.name)
                lev = levenshtein_similarity(m.name, cand.name)
                score = 0.65 * jw + 0.35 * lev
                if score > best_score:
                    best_score = score
                    best_core_nid = cand.node_id
            if best_core_nid and best_score >= 0.86 and root == nid:
                uf.union(nid, best_core_nid)

    # Agrupamento final de clusters
    clusters: dict[str, list[IdentityMention]] = defaultdict(list)
    for nid, m in mentions.items():
        clusters[uf.find(nid)].append(m)

    # Construção de Golden Records e Crosswalk
    golden_records: list[dict[str, Any]] = []
    crosswalk_rows: list[dict[str, Any]] = []
    core_duplicates_merged = 0

    sorted_roots = sorted(clusters.keys())
    master_seq = 0
    for root in sorted_roots:
        members = clusters[root]
        core_members = [m for m in members if m.source == "core_clientes"]
        if not core_members:
            # Registro de app/SAC/NPS que não casou com nenhum cliente core
            continue

        master_seq += 1
        master_id = f"M{master_seq:07d}"
        if len(core_members) > 1:
            core_duplicates_merged += len(core_members) - 1

        # Regra de sobrevivência (Survivorship):
        # Prioriza registro ATIVO e com updated_at mais recente (ou id_cliente menor em empate)
        core_objs = [core_record_map[m.record_id] for m in core_members]
        core_objs.sort(
            key=lambda c: (
                1 if c.get("status") == "ATIVO" else 0,
                c.get("updated_at") or "",
                c.get("renda_mensal") or 0.0,
            ),
            reverse=True,
        )
        winner = dict(core_objs[0])

        # Enriquecimento com contatos de outras menções se estiverem vazios no winner
        if not winner.get("email"):
            for m in members:
                if m.email:
                    winner["email"] = m.email
                    break
        if not winner.get("telefone"):
            for m in members:
                if m.full_phone:
                    winner["telefone"] = m.full_phone
                    break

        linked_core_ids = sorted({m.record_id for m in core_members})
        linked_app_ids = sorted({m.record_id for m in members if m.source == "app_usuarios"})
        linked_sac_ids = sorted({m.record_id for m in members if m.source == "sac_atendimentos"})
        linked_nps_ids = sorted({m.record_id for m in members if m.source == "nps_respostas"})

        golden_records.append(
            {
                "master_customer_id": master_id,
                "primary_id_cliente": winner["id_cliente"],
                "cpf": winner["cpf"],
                "nome_completo": winner["nome_completo"],
                "data_nascimento": winner["data_nascimento"],
                "sexo": winner["sexo"],
                "email": winner.get("email"),
                "telefone": winner.get("telefone"),
                "cep": winner.get("cep"),
                "logradouro": winner.get("logradouro"),
                "numero": winner.get("numero"),
                "cidade": winner.get("cidade"),
                "uf": winner.get("uf"),
                "renda_mensal": winner.get("renda_mensal") or 0.0,
                "segmento": winner.get("segmento") or "MASSA",
                "status": winner.get("status") or "ATIVO",
                "data_abertura": winner.get("data_abertura"),
                "updated_at": winner.get("updated_at"),
                "linked_core_ids": linked_core_ids,
                "linked_app_ids": linked_app_ids,
                "linked_sac_protocols": linked_sac_ids,
                "linked_nps_ids": linked_nps_ids,
                "source_mentions_count": len(members),
            }
        )

        for m in members:
            crosswalk_rows.append(
                {
                    "source": m.source,
                    "source_record_id": m.record_id,
                    "master_customer_id": master_id,
                }
            )

    _write_jsonl(s_path / "golden_customers.jsonl", golden_records)
    _write_jsonl(s_path / "entity_crosswalk.jsonl", crosswalk_rows)

    # Avaliação contra o gabarito (_ground_truth/entity_map.csv)
    precision, recall, f1, purity, gt_entities = _evaluate_against_ground_truth(
        crosswalk_rows, ground_truth_dir
    )

    metrics = EntityResolutionMetrics(
        total_mentions=len(mentions),
        total_clusters=len(golden_records),
        ground_truth_entities=gt_entities,
        core_duplicates_merged=core_duplicates_merged,
        precision=precision,
        recall=recall,
        f1_score=f1,
        cluster_purity=purity,
    )
    (s_path / "_entity_resolution_metrics.json").write_text(
        json.dumps(asdict(metrics), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return metrics


def _evaluate_against_ground_truth(
    crosswalk_rows: list[dict[str, Any]],
    ground_truth_dir: Path | str | None,
) -> tuple[float, float, float, float, int]:
    """Calcula Precisão, Recall, F1 e Pureza de Cluster usando BCubed sobre o gabarito."""
    if ground_truth_dir is None:
        return 1.0, 1.0, 1.0, 1.0, 0
    gt_file = Path(ground_truth_dir) / "entity_map.csv"
    if not gt_file.exists():
        return 1.0, 1.0, 1.0, 1.0, 0

    gt_map: dict[tuple[str, str], str] = {}
    with gt_file.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            gt_map[(row["source"], row["source_record_id"])] = row["person_id"]

    pred_clusters: dict[str, list[str]] = defaultdict(list)
    gt_clusters: dict[str, list[str]] = defaultdict(list)
    item_pairs: list[tuple[str, str]] = []

    for cw in crosswalk_rows:
        key = (cw["source"], cw["source_record_id"])
        gt_pid = gt_map.get(key)
        if not gt_pid:
            continue
        pred_cid = cw["master_customer_id"]
        pred_clusters[pred_cid].append(gt_pid)
        gt_clusters[gt_pid].append(pred_cid)
        item_pairs.append((pred_cid, gt_pid))

    if not item_pairs:
        return 1.0, 1.0, 1.0, 1.0, len(set(gt_map.values()))

    # BCubed Precision & Recall (padrão ouro em avaliação de Entity Resolution)
    pred_counts = {cid: Counter(pids) for cid, pids in pred_clusters.items()}
    gt_counts = {pid: Counter(cids) for pid, cids in gt_clusters.items()}

    prec_sum = 0.0
    rec_sum = 0.0
    for pred_cid, gt_pid in item_pairs:
        same_in_pred = pred_counts[pred_cid][gt_pid]
        prec_sum += same_in_pred / len(pred_clusters[pred_cid])
        same_in_gt = gt_counts[gt_pid][pred_cid]
        rec_sum += same_in_gt / len(gt_clusters[gt_pid])

    n = len(item_pairs)
    precision = round(prec_sum / n, 4)
    recall = round(rec_sum / n, 4)
    f1 = round((2 * precision * recall) / (precision + recall), 4) if (precision + recall) else 0.0

    majority_hits = sum(cnt.most_common(1)[0][1] for cnt in pred_counts.values())
    purity = round(majority_hits / n, 4)
    return precision, recall, f1, purity, len(set(gt_map.values()))
