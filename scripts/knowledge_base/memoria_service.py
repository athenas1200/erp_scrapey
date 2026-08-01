# -*- coding: utf-8 -*-
"""S9 MEMORY ENGINE - aprende o ERP S9 continuamente, construindo um
'gemeo cognitivo' no banco de memoria (schema memory_* no db 'postgres' da VPS).

REGRA DE PERFORMANCE: nunca varrer a replica. Usa apenas information_schema,
pg_catalog e pg_stats (estatisticas do autovacuum). Todas as escritas na memoria
sao em batch (execute_values) - poucos round-trips. Ciclo-alvo < 30s.

- Le s9_real SOMENTE LEITURA (porta 15435)
- Escreve APENAS em memory_* (porta 15436)
- Incremental via checkpoint local (schema_hash).

Uso: python memoria_service.py [intervalo_segundos]   (supervisionado pelo vigia)
"""
import json, io, os, sys, time, re
from db_conn import connect, close_all, q as qr
from db_mem import connect as mconnect, close_all as mclose
from psycopg2.extras import execute_values

BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOG = os.path.join(LOGDIR, 'memoria.log')
CKPT = os.path.join(BASE, 'memory_checkpoint.json')
INTERVALO = 300
if len(sys.argv) > 1:
    try: INTERVALO = int(sys.argv[1])
    except Exception: pass


def log(msg):
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    try:
        with io.open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)


def load_ckpt():
    try:
        return json.load(io.open(CKPT, encoding='utf-8'))
    except Exception:
        return {"ciclo": 0, "schema_hash": {}, "tabelas_amostradas": {}}

def save_ckpt(ck):
    io.open(CKPT, 'w', encoding='utf-8').write(json.dumps(ck, ensure_ascii=False, indent=1))


HB = os.path.join(LOGDIR, 'heartbeat_memoria.json')

def heartbeat(status='ok', extra=None):
    """Grava estado continuo do servico (usado na recuperacao apos reboot)."""
    hb = {
        "servico": "memoria",
        "status": status,
        "horario": time.strftime('%Y-%m-%d %H:%M:%S'),
        "ciclo": 0,
        "tabelas": 0,
        "colunas": 0,
        "pendente": None,
    }
    try:
        if os.path.exists(CKPT):
            ck = load_ckpt()
            hb["ciclo"] = ck.get("ciclo", 0)
            hb["ultimo_ciclo_ok"] = ck.get("ultima_exec", "")
    except Exception:
        pass
    if extra:
        hb.update(extra)
    try:
        io.open(HB, 'w', encoding='utf-8').write(json.dumps(hb, ensure_ascii=False, indent=1))
    except Exception:
        pass


# ================== LEITURA DA REPLICA (metadata apenas) ==================
def scan_schema(cur):
    tabelas = [r[0] for r in qr(cur, "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")]
    cols = qr(cur, """SELECT c.table_name, c.column_name, c.data_type, c.udt_name,
        c.character_maximum_length, c.numeric_precision, c.numeric_scale, c.is_nullable,
        c.column_default, c.ordinal_position
        FROM information_schema.columns c WHERE table_schema='public'
        ORDER BY c.table_name, c.ordinal_position""")
    col_by = {}
    for r in cols:
        col_by.setdefault(r[0], []).append({"coluna": r[1], "tipo": r[2], "udt": r[3], "len": r[4],
                                            "prec": r[5], "scale": r[6], "null": r[7] == "YES",
                                            "default": r[8], "ord": r[9]})
    pks = qr(cur, """SELECT tc.relname, a.attname FROM pg_constraint c
        JOIN pg_class tc ON tc.oid=c.conrelid JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey)
        WHERE c.contype='p' AND tc.relnamespace='public'::regnamespace ORDER BY tc.relname, a.attnum""")
    pk = {}
    for r in pks: pk.setdefault(r[0], []).append(r[1])
    fks = qr(cur, """SELECT tc.relname, a.attname, rc.relname, ra.attname FROM pg_constraint c
        JOIN pg_class tc ON tc.oid=c.conrelid JOIN pg_class rc ON rc.oid=c.confrelid
        JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1]
        JOIN pg_attribute ra ON ra.attrelid=c.confrelid AND ra.attnum=c.confkey[1]
        WHERE c.contype='f' AND tc.relnamespace='public'::regnamespace ORDER BY tc.relname""")
    fk = {}
    for r in fks: fk.setdefault(r[0], []).append({"coluna": r[1], "ref": r[2], "ref_coluna": r[3]})
    rows = qr(cur, """SELECT c.relname, c.reltuples::bigint, pg_size_pretty(pg_total_relation_size(c.oid))
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname""")
    rc = {r[0]: (r[1], r[2]) for r in rows}
    return {"tabelas": tabelas, "col_by": col_by, "pk": pk, "fk": fk, "rows": rc}

def scan_stats(cur):
    st = qr(cur, """SELECT tablename, attname, null_frac, n_distinct, most_common_vals,
        most_common_freqs FROM pg_stats WHERE schemaname='public'""")
    out = {}
    for r in st:
        out.setdefault(r[0], {})[r[1]] = {"null_frac": r[2], "n_distinct": r[3], "mcv": r[4], "mcf": r[5]}
    return out

def hash_tab(cols):
    return json.dumps(cols, ensure_ascii=False, sort_keys=True)


def parse_mcv(val):
    """Converte most_common_vals do pg_stats em lista limpa.
    psycopg2 pode devolver: lista Python, string literal PG ('{a,b,c}') ou None."""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val]
    if isinstance(val, (tuple, set)):
        return [str(x) for x in val]
    s = str(val).strip()
    if s.startswith('{') and s.endswith('}'):
        s = s[1:-1]
        if s == '':
            return []
        return [p.strip('"') for p in s.split(',')]
    if ',' in s:
        return [p.strip() for p in s.split(',')]
    return [s]


# ================== INFERENCIA ==================
REG_SIGNIFICADO = [
    ("codigocliente", "chave do cliente (Cli_For)"),
    ("codigoproduto", "chave do produto/servico (Prod_Serv)"),
    ("codigofornecedor", "chave do fornecedor"),
    ("codigovendedor", "chave do vendedor/funcionario"),
    ("codigooperacao", "codigo da operacao fiscal/tipo movimento"),
    ("codigofilial", "chave da filial"),
    ("preco_final", "preco final da venda"),
    ("preco_avista", "preco a vista"),
    ("preco_pix", "preco no pix"),
    ("precocusto", "preco de custo"),
    ("ncm", "classificacao fiscal NCM"),
    ("ean", "codigo de barras EAN"),
    ("ean3", "codigo de barras EAN-3"),
    ("cfop", "codigo fiscal CFOP"),
    ("cest", "codigo fiscal CEST"),
    ("cst", "codigo fiscal CST"),
    ("csosn", "codigo fiscal CSOSN"),
    ("quantidade", "quantidade"),
    ("valor", "valor monetario"),
    ("data_emissao", "data de emissao"),
    ("data_cadastro", "data de cadastro"),
    ("data_coleta", "data/hora da coleta"),
    ("cpf", "CPF"),
    ("cnpj", "CNPJ"),
    ("cep", "CEP"),
    ("telefone", "telefone"),
    ("email", "e-mail"),
    ("endereco", "endereco"),
    ("cidade", "cidade"),
    ("estado", "estado (UF)"),
    ("observacao", "observacao"),
    ("descricao", "descricao"),
    ("status", "status"),
    ("ativo", "flag ativo/inativo"),
    ("id", "identificador"),
]

def infer_significado(col, tipo):
    l = col.lower()
    for k, v in REG_SIGNIFICADO:
        if l == k or l.startswith(k):
            return v
    if tipo in ("numeric", "integer", "bigint", "smallint", "money", "double precision", "real"):
        return "valor numerico"
    if "timestamp" in tipo or tipo == "date":
        return "data/hora"
    if "bool" in tipo:
        return "flag booleano"
    return "texto"

def infer_modulo(tab):
    l = tab.lower()
    if l.startswith("cli_for") or "credito_cli" in l:
        return "clientes"
    if l.startswith("prod_serv") or l.startswith("estoque") or "familia" in l or "marca" in l:
        return "produtos/estoque"
    if l.startswith("movimento"):
        return "movimentos"
    if l.startswith("financeiro") or l.startswith("contas") or "plano_contas" in l or "bancos" in l:
        return "financeiro"
    if l.startswith("nfe") or l.startswith("nfce") or l.startswith("mdfe") or l.startswith("cte") \
       or "fiscal" in l or "cfop" in l or "cest" in l or "ncm" in l or "reforma_impostos" in l:
        return "fiscal"
    if l.startswith("funcionario") or l.startswith("caixa"):
        return "rh/caixa"
    if l.startswith("transport") or "frete" in l or "veiculo" in l or "carregamento" in l:
        return "logistica/transporte"
    if l.startswith("config") or l.startswith("parametro") or l.startswith("filial"):
        return "configuracao"
    if l.startswith("vendedor") or "promocao" in l or "codigo_promocional" in l:
        return "comercial"
    return "geral"


# ================== CICLO (tudo em batch) ==================
def ciclo(ck):
    n_ciclo = ck.get("ciclo", 0) + 1
    t_start = time.time()
    log("ciclo %d iniciado" % n_ciclo)

    tr, lr, cr, cur = connect()
    schema = None; stats = None
    try:
        cur.execute("SET statement_timeout = 120000")
        schema = scan_schema(cur)
        stats = scan_stats(cur)
    finally:
        close_all(tr, lr, cr)

    schema_hash = {}
    for t in schema["tabelas"]:
        schema_hash[t] = hash_tab(schema["col_by"].get(t, []))
    ant = ck.get("schema_hash", {})
    primeira_vez = not ant
    if primeira_vez:
        t_mod = schema["tabelas"]
    else:
        t_mod = [t for t in schema["tabelas"] if schema_hash.get(t) != ant.get(t)]
    removidas = [t for t in ant if t not in schema_hash]
    log("scanner: %d tabelas, %d a processar, %d removidas (%.1fs)" %
        (len(schema["tabelas"]), len(t_mod), len(removidas), time.time() - t_start))

    agora = time.strftime('%Y-%m-%d %H:%M:%S')
    ck_amostradas = ck.get("tabelas_amostradas", {})
    tabelas_set = set(schema["tabelas"])

    # carregar estado da memoria em set (uma query)
    tm, lm, cm, mcur = mconnect()
    try:
        mcur.execute("SELECT tabela FROM memory_tables")
        tab_exist = {r[0] for r in mcur.fetchall()}
        mcur.execute("SELECT tabela||'.'||coluna FROM memory_columns")
        col_exist = {r[0] for r in mcur.fetchall()}

        # ---- 1. TABELAS (batch) ----
        dt = []
        for t in t_mod:
            colunas = schema["col_by"].get(t, [])
            rcount = schema["rows"].get(t, (0, ''))[0] or 0
            tsize = schema["rows"].get(t, (0, ''))[1]
            pk = schema["pk"].get(t, [])
            fk = schema["fk"].get(t, [])
            mod = infer_modulo(t)
            refs = ", ".join(sorted(set(f["ref"] for f in fk))[:5])
            desc = "Tabela do modulo %s com %d colunas" % (mod, len(colunas))
            if pk: desc += " (PK: %s)" % ", ".join(pk)
            if refs: desc += " | relaciona-se com: %s" % refs
            if rcount: desc += " | ~%d registros" % rcount
            imp = "alta" if rcount > 100000 else ("media" if rcount > 10000 else "baixa")
            dt.append((t, desc, mod, int(rcount), len(colunas), tsize, agora, agora,
                       "novo" if t not in tab_exist else "atualizado", imp,
                       0.5 if t not in tab_exist else 0.55))
        if dt:
            execute_values(mcur, """INSERT INTO memory_tables
                (tabela, descricao, modulo, linhas, colunas, tamanho, primeira_vista, ultima_vista,
                 frequencia_alteracao, importancia, confianca)
                VALUES %s ON CONFLICT (tabela) DO UPDATE SET
                 descricao=EXCLUDED.descricao, modulo=EXCLUDED.modulo, linhas=EXCLUDED.linhas,
                 colunas=EXCLUDED.colunas, tamanho=EXCLUDED.tamanho, ultima_vista=EXCLUDED.ultima_vista,
                 frequencia_alteracao=EXCLUDED.frequencia_alteracao, importancia=EXCLUDED.importancia,
                 confianca=LEAST(memory_tables.confianca+0.05, 1.0)""", dt)
        log("tabelas: %d upsert" % len(dt))

        # ---- 2. COLUNAS (batch, INSERT ON CONFLICT DO UPDATE) ----
        dc = []
        for t in t_mod:
            ispk_t = set(schema["pk"].get(t, []))
            st_t = stats.get(t) or {}
            for c in schema["col_by"].get(t, []):
                nome = c["coluna"]
                st = st_t.get(nome) or {}
                sign = infer_significado(nome, c["tipo"])
                key = t + "." + nome
                dc.append((t, nome, c["tipo"], c["udt"], c["len"], c["prec"], c["scale"],
                           c["null"], nome in ispk_t,
                           st.get("null_frac"), st.get("n_distinct"),
                           sign, 0.5 if key not in col_exist else 0.55, agora, agora))
        if dc:
            execute_values(mcur, """INSERT INTO memory_columns
                (tabela, coluna, tipo, udt, len, prec, scale, nullable, is_pk,
                 null_frac, distintos, significado, confianca, primeira_vista, ultima_vista)
                VALUES %s ON CONFLICT (tabela, coluna) DO UPDATE SET
                 tipo=EXCLUDED.tipo, udt=EXCLUDED.udt, len=EXCLUDED.len, prec=EXCLUDED.prec,
                 scale=EXCLUDED.scale, nullable=EXCLUDED.nullable, is_pk=EXCLUDED.is_pk,
                 null_frac=EXCLUDED.null_frac, distintos=EXCLUDED.distintos,
                 significado=EXCLUDED.significado,
                 confianca=LEAST(memory_columns.confianca+0.02, 0.99), ultima_vista=EXCLUDED.ultima_vista""", dc)
        log("colunas: %d upsert" % len(dc))

        # ---- 3. RELACIONAMENTOS (FK formais + por nome) ----
        # incremental: so recalcula quando houve mudanca de schema
        rel = []
        if t_mod or n_ciclo % 12 == 0:
            for t, flist in schema["fk"].items():
                for f in flist:
                    rel.append((t, f["coluna"], f["ref"], f["ref_coluna"], 'fk', 1.0, 'foreign key formal', agora))
            for t in schema["tabelas"]:
                for c in schema["col_by"].get(t, []):
                    nome = c["coluna"]
                    cand = None
                    if nome.startswith("Codigo") and nome != "Codigo":
                        cand = nome[6:]
                    elif nome.endswith("_Codigo") or nome.endswith("_codigo"):
                        cand = nome.split("_")[0]
                    if cand:
                        for t2 in schema["tabelas"]:
                            if t2.lower() == cand.lower():
                                rel.append((t, nome, t2, "Id", 'nome', 0.7, 'padrao de nome Codigo*', agora))
                                break
            if rel:
                execute_values(mcur, """INSERT INTO memory_relationships
                    (tabela, coluna, ref_tabela, ref_coluna, tipo, confianca, evidencias, descoberto_em)
                    VALUES %s ON CONFLICT DO NOTHING""", rel)
        log("relacionamentos: %d upsert" % len(rel))

        # ---- 4. EXEMPLOS (via pg_stats MCV - zero custo na replica) ----
        # incremental: so tabelas modificadas; refresh completo a cada 12 ciclos
        ex = []
        ex_alvo = t_mod if t_mod else schema["tabelas"]
        if n_ciclo % 12 == 0 or not ck_amostradas.get("exemplos_feito"):
            ex = []
            for t in ex_alvo:
                st_t = stats.get(t) or {}
                for col, st in st_t.items():
                    vals = parse_mcv(st.get("mcv"))
                    for v in vals[:3]:
                        ex.append((t, col, str(v)[:300], agora))
            if ex:
                execute_values(mcur, """INSERT INTO memory_examples (tabela, coluna, exemplo, amostrado_em)
                    VALUES %s ON CONFLICT DO NOTHING""", ex)
            ck_amostradas["exemplos_feito"] = 1
            log("exemplos: %d upsert" % len(ex))
        else:
            log("exemplos: skip (incremental)")

        # ---- 5. REGRAS (valores dominantes) ----
        ru = []
        for t in ex_alvo:
            st_t = stats.get(t) or {}
            for col, st in st_t.items():
                mcv = parse_mcv(st.get("mcv"))
                mcf = st.get("mcf")
                freqs = parse_mcv(mcf) if mcf is not None else []
                if mcv and freqs and len(mcv) >= 1 and len(freqs) >= 1:
                    try: f0 = float(freqs[0])
                    except Exception: f0 = 0.0
                    if f0 > 0.85:
                        ru.append((t, "%s = %s (%.0f%% dos registros)" % (col, mcv[0], f0 * 100),
                                   "valor dominante: " + infer_significado(col, ""), 0.9, agora))
        if ru:
            execute_values(mcur, """INSERT INTO memory_business_rules (tabela, regra, significado, confianca, descoberto_em)
                VALUES %s ON CONFLICT (tabela, regra) DO NOTHING""", ru)
        log("regras: %d upsert" % len(ru))

        # ---- 6. ENTIDADES ----
        entidades = {
            "Cliente": ("cli_for", ["Cli_For", "Cli_For_Contatos", "Cli_For_Limite_Credito", "Credito_Cli_For"]),
            "Fornecedor": ("cli_for", ["Cli_For", "Movimento_Cotacao_Compra"]),
            "Produto": ("prod_serv", ["Prod_Serv", "Prod_Serv_Composicao", "Prod_Serv_Grade", "Prod_Serv_Precos"]),
            "Pedido/Venda": ("movimento", ["Movimento", "Movimento_Prod_Serv", "Movimento_Entrega"]),
            "Nota Fiscal": ("fiscal", ["Movimento_Documentos_Fiscais", "NFe_Eventos", "NFe_Inutilizadas"]),
            "Conta financeira": ("financeiro", ["Financeiro_Contas", "Financeiro_Caixa", "Plano_Contas1"]),
            "Transportadora": ("logistica", ["Transporte_Transportadora", "Frete", "Carregamentos_Veiculos"]),
            "Vendedor": ("comercial", ["Funcionarios_Conf", "Meta_Venda", "Meta_Funcionario"]),
            "Estoque": ("produtos/estoque", ["Estoque_Atual", "Estoque_Atual_Lote", "Movimento_Estoque_Efetivado"]),
        }
        mcur.execute("SELECT entidade FROM memory_entities")
        ent_exist = {r[0] for r in mcur.fetchall()}
        de = []
        for ent, (mod, tabs) in entidades.items():
            if ent in ent_exist: continue
            de.append((ent, "Entidade de negocio principal do ERP (modulo %s)" % mod,
                       json.dumps([t for t in tabs if t in tabelas_set]), 0.8, agora))
        if de:
            execute_values(mcur, """INSERT INTO memory_entities (entidade, descricao, tabelas_principais, confianca, descoberto_em)
                VALUES %s ON CONFLICT DO NOTHING""", de)
        log("entidades: %d upsert" % len(de))

        # ---- 7. ESTATISTICAS DIARIAS (batch) ----
        ds = []
        hoje = time.strftime('%Y-%m-%d')
        ult_estat = ck_amostradas.get("estatistica_dia")
        if t_mod or ult_estat != hoje:
            for t in schema["tabelas"]:
                rcount = schema["rows"].get(t, (0, ''))[0] or 0
                ds.append((t, hoje, int(rcount)))
            execute_values(mcur, """INSERT INTO memory_statistics (tabela, data, linhas)
                VALUES %s ON CONFLICT (tabela, data) DO UPDATE SET linhas=EXCLUDED.linhas""", ds)
            ck_amostradas["estatistica_dia"] = hoje
        log("estatisticas: %d upsert" % len(ds))

        # ---- 8. HISTORICO (novidades deste ciclo) ----
        dh = []
        for t in t_mod:
            if t not in tab_exist:
                dh.append(("nova_tabela", t, json.dumps({"colunas": len(schema["col_by"].get(t, []))}), agora))
            else:
                dh.append(("tabela_alterada", t, None, agora))
        for t in removidas:
            dh.append(("tabela_removida", t, None, agora))
        if dh:
            execute_values(mcur, """INSERT INTO memory_history (tipo, objeto, detalhe, detectado_em)
                VALUES %s""", dh)
        log("historico: %d registros" % len(dh))

        # ---- 9. DOCUMENTOS markdown (local + banco) ----
        outdir = os.path.join(BASE, 'documentation')
        try: os.makedirs(outdir, exist_ok=True)
        except Exception: pass
        dd = []
        for t in t_mod:
            colunas = schema["col_by"].get(t, [])
            pk = schema["pk"].get(t, [])
            fk = schema["fk"].get(t, [])
            mod = infer_modulo(t)
            rcount = schema["rows"].get(t, (0, ''))[0] or 0
            md = ["# %s" % t, "", "**Modulo:** %s | **Registros:** ~%s" % (mod, rcount), "",
                  "## Colunas (%d)" % len(colunas), "",
                  "| Coluna | Tipo | Null | PK | Significado |", "|---|---|---|---|---|"]
            for c in colunas:
                md.append("| %s | %s | %s | %s | %s |" % (c["coluna"], c["tipo"],
                            "sim" if c["null"] else "nao", "sim" if c["coluna"] in pk else "",
                            infer_significado(c["coluna"], c["tipo"])))
            md.append("")
            md.append("## Relacionamentos")
            for f in fk:
                md.append("- %s -> %s(%s)" % (f["coluna"], f["ref"], f["ref_coluna"]))
            conteudo = "\n".join(md)
            try:
                io.open(os.path.join(outdir, t + '.md'), 'w', encoding='utf-8').write(conteudo)
            except Exception:
                pass
            dd.append((t, 'markdown', 1, conteudo, agora))
        if dd:
            execute_values(mcur, """INSERT INTO memory_documents (nome, tipo, versao, conteudo, gerado_em)
                VALUES %s ON CONFLICT DO NOTHING""", dd)
        log("documentos: %d gerados" % len(dd))

        # ---- 10. SEMANTICA (pergunta->resposta por entidade/coluna/relacionamento) ----
        dsem = []
        if t_mod or n_ciclo % 12 == 0 or not ck_amostradas.get("semantica_feito"):
            # (a) por entidade: onde fica X, como identificar X
            mcur.execute("SELECT entidade, descricao, tabelas_principais FROM memory_entities")
            for ent, desc, tabs in mcur.fetchall():
                try:
                    tlista = json.loads(tabs) if isinstance(tabs, str) else (tabs or [])
                except Exception:
                    tlista = []
                t0 = tlista[0] if tlista else ''
                if t0:
                    dsem.append((ent, "Onde ficam os dados de %s?" % ent,
                                 "Tabela principal: %s (entidade: %s)" % (t0, ent),
                                 0.8, agora))
                    pks = schema["pk"].get(t0, [])
                    if pks:
                        dsem.append((ent, "Como identificar um %s?" % ent,
                                     "Chave primaria: %s.%s" % (t0, ", ".join(pks)),
                                     0.8, agora))
                    dsem.append((ent, "Quais tabelas pertencem a %s?" % ent,
                                 "Tabelas: %s" % ", ".join(tlista), 0.8, agora))
            # (b) por coluna-chave de entidade: significado no contexto
            chaves = ['Codigo', 'Cod', 'Id', 'Numero', 'Nome', 'Descricao', 'Data',
                      'Total', 'Valor', 'Quantidade', 'Preco', 'Custo']
            mcur.execute("""SELECT tabela, coluna, significado FROM memory_columns
                WHERE is_pk = TRUE OR coluna ILIKE ANY(%s)
                ORDER BY tabela, coluna""",
                         (["%" + c + "%" for c in chaves],))
            for tab, col, sign in mcur.fetchall():
                for ent, desc, tabs in [(None, None, None)]:
                    pass
                dsem.append((tab, "O que e a coluna %s.%s?" % (tab, col),
                             "Significado: %s" % (sign or col), 0.6, agora))
            # (c) por relacionamento formal: ligacoes semanticas
            mcur.execute("""SELECT DISTINCT tabela, coluna, ref_tabela FROM memory_relationships
                WHERE tipo = 'fk' LIMIT 2000""")
            for tab, col, ref in mcur.fetchall():
                dsem.append((tab, "O que referencia %s.%s?" % (tab, col),
                             "Referencia %s (chave estrangeira para %s)" % (ref, ref),
                             0.7, agora))
            # grava apenas novos (pergunta unica por entidade)
            execute_values(mcur, """INSERT INTO memory_semantics
                (entidade, pergunta, resposta, confianca, descoberto_em)
                VALUES %s ON CONFLICT DO NOTHING""", dsem)
            ck_amostradas["semantica_feito"] = 1
            log("semantica: %d upsert" % len(dsem))
        else:
            log("semantica: skip (incremental)")

        cm.commit()
    except Exception as e:
        try: cm.rollback()
        except Exception: pass
        raise
    finally:
        mclose(tm, lm, cm)

    # checkpoint seguro (processa em chunks para nao reprocessar no proximo ciclo)
    ck["ciclo"] = n_ciclo
    ck["schema_hash"] = schema_hash
    ck["tabelas_amostradas"] = ck_amostradas
    ck["ultima_exec"] = time.strftime('%Y-%m-%d %H:%M:%S')
    save_ckpt(ck)
    log("ciclo %d concluido em %.1fs (tabelas=%d, colunas=%d, rel=%d, exemplos=%d, regras=%d)" %
        (n_ciclo, time.time() - t_start, len(t_mod), len(dc), len(rel), len(ex), len(ru)))


def main():
    log("S9 MEMORY ENGINE iniciado. intervalo=%ds" % INTERVALO)
    heartbeat('online', {"msg": "servico iniciado"})
    ck = load_ckpt()
    while True:
        try:
            ciclo(ck)
            heartbeat('ok', {"ciclo_ok": ck.get("ciclo", 0)})
        except Exception as e:
            log("ERRO no ciclo: %s" % str(e)[:300])
            heartbeat('erro', {"ultimo_erro": str(e)[:300]})
        time.sleep(INTERVALO)

if __name__ == '__main__':
    main()
