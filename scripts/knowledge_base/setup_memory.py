# -*- coding: utf-8 -*-
"""Cria o schema de memoria completo no banco 'postgres' da VPS.
Idempotente: CREATE TABLE IF NOT EXISTS + ALTER TABLE ADD COLUMN para colunas novas.
Nao mexe no s9_real. Executar: python setup_memory.py
"""
import paramiko, socket, threading, psycopg2

VPS_HOST = "84.247.189.155"; VPS_USER = "root"; VPS_PWD = "Lin1106***"
PG_PWD = "S9pg2026!"
LOCAL_PORT = 15436


def open_tunnel():
    t = paramiko.SSHClient(); t.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    t.connect(VPS_HOST, username=VPS_USER, password=VPS_PWD, timeout=30)
    tr = t.get_transport()
    l = socket.socket(); l.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    l.bind(("127.0.0.1", LOCAL_PORT)); l.listen(5)
    def fwd():
        while True:
            try: c, _ = l.accept()
            except Exception: break
            ch = tr.open_channel("direct-tcpip", ("127.0.0.1", 5434), c.getpeername())
            def pipe(s, d):
                try:
                    while True:
                        b = s.recv(65536)
                        if not b: break
                        d.sendall(b)
                except Exception: pass
                try: d.shutdown(2)
                except Exception: pass
            threading.Thread(target=pipe, args=(c, ch), daemon=True).start()
            threading.Thread(target=pipe, args=(ch, c), daemon=True).start()
    threading.Thread(target=fwd, daemon=True).start()
    return t, l


TABELAS = {
    "memory_tables": """(
        tabela TEXT PRIMARY KEY,
        descricao TEXT,
        modulo TEXT,
        linhas BIGINT,
        colunas INT,
        tamanho TEXT,
        primeira_vista TIMESTAMP,
        ultima_vista TIMESTAMP,
        frequencia_alteracao TEXT,
        importancia TEXT,
        confianca FLOAT)""",
    "memory_columns": """(
        tabela TEXT NOT NULL,
        coluna TEXT NOT NULL,
        tipo TEXT, udt TEXT, len INT, prec INT, scale INT,
        nullable BOOLEAN, is_pk BOOLEAN,
        media NUMERIC, min_val TEXT, max_val TEXT,
        nulos BIGINT, distintos BIGINT, null_frac FLOAT,
        significado TEXT, confianca FLOAT,
        primeira_vista TIMESTAMP, ultima_vista TIMESTAMP,
        PRIMARY KEY (tabela, coluna))""",
    "memory_examples": """(
        id SERIAL PRIMARY KEY,
        tabela TEXT NOT NULL,
        coluna TEXT NOT NULL,
        exemplo TEXT,
        frequencia BIGINT,
        amostrado_em TIMESTAMP,
        UNIQUE (tabela, coluna, exemplo))""",
    "memory_relationships": """(
        id SERIAL PRIMARY KEY,
        tabela TEXT NOT NULL,
        coluna TEXT NOT NULL,
        ref_tabela TEXT,
        ref_coluna TEXT,
        tipo TEXT,
        confianca FLOAT,
        evidencias TEXT,
        descoberto_em TIMESTAMP)""",
    "memory_patterns": """(
        id SERIAL PRIMARY KEY,
        tabela TEXT, coluna TEXT,
        padrao TEXT, descricao TEXT,
        confianca FLOAT, descoberto_em TIMESTAMP)""",
    "memory_business_rules": """(
        id SERIAL PRIMARY KEY,
        tabela TEXT, regra TEXT, significado TEXT,
        confianca FLOAT, descoberto_em TIMESTAMP,
        UNIQUE (tabela, regra))""",
    "memory_history": """(
        id SERIAL PRIMARY KEY,
        tipo TEXT, objeto TEXT, detalhe JSONB,
        detectado_em TIMESTAMP)""",
    "memory_documents": """(
        id SERIAL PRIMARY KEY,
        nome TEXT, tipo TEXT, versao INT, conteudo TEXT,
        gerado_em TIMESTAMP)""",
    "memory_vectors": """(
        id SERIAL PRIMARY KEY,
        entidade TEXT, conteudo TEXT, vetor BYTEA,
        criado_em TIMESTAMP)""",
    "memory_workflows": """(
        id SERIAL PRIMARY KEY,
        nome TEXT, descricao TEXT,
        passos JSONB, frequencia BIGINT,
        confianca FLOAT, descoberto_em TIMESTAMP)""",
    "memory_entities": """(
        id SERIAL PRIMARY KEY,
        entidade TEXT UNIQUE, descricao TEXT,
        tabelas_principais JSONB,
        confianca FLOAT, descoberto_em TIMESTAMP)""",
    "memory_modules": """(
        id SERIAL PRIMARY KEY,
        modulo TEXT UNIQUE, descricao TEXT,
        tabelas JSONB, colunas_importantes JSONB,
        confianca FLOAT, descoberto_em TIMESTAMP)""",
    "memory_statistics": """(
        id SERIAL PRIMARY KEY,
        tabela TEXT, data DATE,
        linhas BIGINT, crescimento_diario BIGINT,
        colunas_nulas JSONB, duplicidades BIGINT,
        valores_unicos BIGINT,
        UNIQUE (tabela, data))""",
    "memory_semantics": """(
        id SERIAL PRIMARY KEY,
        entidade TEXT, pergunta TEXT, resposta TEXT,
        confianca FLOAT, descoberto_em TIMESTAMP)""",
    "memory_actions": """(
        id SERIAL PRIMARY KEY,
        acao TEXT, descricao TEXT,
        tabelas_alteradas JSONB, campos_obrigatorios JSONB,
        validacoes JSONB, confianca FLOAT, descoberto_em TIMESTAMP)""",
    # ---- MODULOS BI ----
    "memory_inventory": """(
        id SERIAL PRIMARY KEY,
        produto_codigo TEXT, produto_nome TEXT,
        estoque_atual NUMERIC, estoque_minimo NUMERIC, estoque_maximo NUMERIC,
        estoque_seguranca NUMERIC, estoque_reservado NUMERIC,
        giro NUMERIC, cobertura_dias NUMERIC,
        consumo_medio_diario NUMERIC, curva_abc TEXT,
        previsao_ruptura DATE, data TIMESTAMP)""",
    "memory_purchase_planning": """(
        id SERIAL PRIMARY KEY,
        produto_codigo TEXT,
        fornecedor_principal TEXT, fornecedor_alternativo TEXT,
        prazo_medio_entrega NUMERIC, atraso_medio NUMERIC,
        qtd_minima NUMERIC, lote_economico NUMERIC,
        preco_medio NUMERIC, ultimo_preco NUMERIC, melhor_preco NUMERIC,
        data TIMESTAMP)""",
    "memory_demand": """(
        id SERIAL PRIMARY KEY,
        produto_codigo TEXT, periodo TEXT, ano INT, mes INT, dia INT, hora INT,
        quantidade NUMERIC, valor NUMERIC,
        data TIMESTAMP)""",
    "memory_sales": """(
        id SERIAL PRIMARY KEY,
        produto_codigo TEXT, cliente_codigo TEXT, vendedor_codigo TEXT,
        filial_codigo TEXT,
        quantidade NUMERIC, valor NUMERIC, margem NUMERIC,
        data TIMESTAMP)""",
    "memory_finance": """(
        id SERIAL PRIMARY KEY,
        tipo TEXT, categoria TEXT, descricao TEXT,
        valor NUMERIC, vencimento DATE, pagamento DATE,
        data TIMESTAMP)""",
    "memory_fiscal": """(
        id SERIAL PRIMARY KEY,
        produto_codigo TEXT, ncm TEXT, cfop TEXT, cst TEXT, csosn TEXT, cest TEXT,
        icms NUMERIC, ipi NUMERIC, pis NUMERIC, cofins NUMERIC, iss NUMERIC,
        ibs NUMERIC, cbs NUMERIC,
        data TIMESTAMP)""",
    "memory_customers": """(
        id SERIAL PRIMARY KEY,
        cliente_codigo TEXT, nome TEXT,
        frequencia_compra NUMERIC, valor_medio NUMERIC,
        limite_credito NUMERIC, risco TEXT,
        ultima_compra DATE, data TIMESTAMP)""",
    "memory_products": """(
        id SERIAL PRIMARY KEY,
        produto_codigo TEXT, nome TEXT,
        giro NUMERIC, margem NUMERIC,
        custo_medio NUMERIC, preco_medio NUMERIC, preco_ideal NUMERIC,
        tempo_reposicao NUMERIC,
        data TIMESTAMP)""",
    "memory_processes": """(
        id SERIAL PRIMARY KEY,
        processo TEXT, descricao TEXT,
        passos JSONB, confianca FLOAT, descoberto_em TIMESTAMP)""",
}


# colunas adicionadas em versoes posteriores (ALTER idempotente)
ADDCOLS = [
    ("memory_tables", "modulo", "TEXT"),
    ("memory_tables", "frequencia_alteracao", "TEXT"),
    ("memory_tables", "importancia", "TEXT"),
    ("memory_tables", "confianca", "FLOAT"),
    ("memory_columns", "null_frac", "FLOAT"),
    ("memory_columns", "nulos", "BIGINT"),
    ("memory_columns", "distintos", "BIGINT"),
    ("memory_columns", "media", "NUMERIC"),
    ("memory_columns", "min_val", "TEXT"),
    ("memory_columns", "max_val", "TEXT"),
    ("memory_examples", "frequencia", "BIGINT"),
    ("memory_relationships", "evidencias", "TEXT"),
    ("memory_documents", "versao", "INT"),
]

t, l = open_tunnel()
try:
    c = psycopg2.connect(host="127.0.0.1", port=LOCAL_PORT, dbname="postgres",
                         user="postgres", password=PG_PWD, connect_timeout=30)
    cur = c.cursor()
    for nome, ddl in TABELAS.items():
        cur.execute("CREATE TABLE IF NOT EXISTS %s %s" % (nome, ddl))
    # adiciona colunas novas se nao existirem
    for tab, col, ctype in ADDCOLS:
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND column_name=%s", (tab, col))
        if not cur.fetchone():
            cur.execute('ALTER TABLE "%s" ADD COLUMN "%s" %s' % (tab, col, ctype))
            print("  + coluna", tab, col)
    c.commit()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
    print("tabelas memory_* no db postgres (%d):" % len([r[0] for r in cur.fetchall() if r[0].startswith('memory_')]))
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'memory\\_%' ORDER BY table_name")
    for r in cur.fetchall():
        print("  -", r[0])
    c.close()
finally:
    l.close(); t.close()
