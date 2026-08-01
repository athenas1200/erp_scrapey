# -*- coding: utf-8 -*-
"""Consulta a tabela concorrente."""
import sys
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
cur = pconn.cursor()
cur.execute("SELECT COUNT(*) FROM concorrente")
print("Total registros:", cur.fetchone()[0])
cur.execute("""SELECT produto_codigo, produto_nome, url, preco, data_preco
    FROM concorrente ORDER BY id DESC LIMIT 10""")
for r in cur.fetchall():
    print("cod=%s | %s | %s | R$ %s | %s" % tuple(str(x)[:60] for x in r))
pconn.close(); tunnel.close(); local.close()
