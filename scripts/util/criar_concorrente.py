# -*- coding: utf-8 -*-
"""Cria tabela 'concorrente' no PostgreSQL (VPS)."""
import sys, io, json, time
BASE = r'C:\Users\Pe de Apoio\AppData\Local\Temp\opencode'
sys.path.insert(0, BASE)
import importlib.util
_spec = importlib.util.spec_from_file_location('sync_silencioso', BASE + r'\sync_silencioso.py')
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

import psycopg2
tunnel, local = sync.open_tunnel()
pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real",
                         user="postgres", password=sync.PG_PWD, connect_timeout=30)
pconn.autocommit = True
cur = pconn.cursor()

# verifica se existe
cur.execute("""SELECT EXISTS (SELECT 1 FROM information_schema.tables
    WHERE table_schema='public' AND table_name='concorrente')""")
existe = cur.fetchone()[0]
print("Tabela concorrente existe:", existe)

if not existe:
    ddl = """
    CREATE TABLE concorrente (
        id BIGSERIAL PRIMARY KEY,
        produto_ordem INTEGER,
        produto_codigo VARCHAR(20),
        produto_nome VARCHAR(200),
        ean VARCHAR(30),
        concorrente VARCHAR(100),
        url VARCHAR(500),
        preco NUMERIC(14,2),
        preco_original NUMERIC(14,2),
        desconto_pct NUMERIC(6,2),
        disponivel BOOLEAN DEFAULT TRUE,
        data_coleta TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        data_preco DATE
    );
    CREATE INDEX idx_concorrente_produto ON concorrente (produto_codigo, data_preco);
    CREATE INDEX idx_concorrente_concorrente ON concorrente (concorrente);
    CREATE INDEX idx_concorrente_ean ON concorrente (ean);
    """
    cur.execute(ddl)
    print("Tabela concorrente CRIADA com indices.")
else:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='concorrente' ORDER BY ordinal_position")
    print("Colunas:", [r[0] for r in cur.fetchall()])

pconn.close(); tunnel.close(); local.close()
print("OK")
