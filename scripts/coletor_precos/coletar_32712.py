# -*- coding: utf-8 -*-
"""Coleta precos do produto 32712 especificamente."""
import sys, json, os, time, io
BASE = r'C:\Users\Pe de Apoio\AppData\Local\Temp\opencode'
sys.path.insert(0, BASE)
import importlib.util
_spec = importlib.util.spec_from_file_location('coletor_precos', BASE + r'\coletor_precos.py')
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)
import pyodbc, psycopg2

hoje = time.strftime('%Y-%m-%d')
sconn = pyodbc.connect(mod.SQL_DSN, timeout=30)
scur = sconn.cursor()
scur.execute("""SELECT p.Ordem, p.Codigo, p.Nome, p.Codigo_Barras, pr.Preco
    FROM dbo.Prod_Serv p
    LEFT JOIN (SELECT Ordem_Prod_Serv, Preco FROM dbo.Prod_Serv_Precos
        WHERE Ordem_Tabela_Preco = 7) pr ON pr.Ordem_Prod_Serv = p.Ordem
    WHERE p.Codigo = '32712'""")
ordem, codigo, nome, ean, preco_atual = scur.fetchone()
sconn.close()
print("Produto:", codigo, nome, "| Preco loja:", preco_atual)

q = '%s preco comprar' % nome
res = mod.firecrawl_search(q)
resultado = []
if res:
    for bloco in res:
        try:
            dados = json.loads(bloco) if bloco.strip().startswith('{') else None
        except Exception:
            dados = None
        itens = []
        if isinstance(dados, dict):
            web = dados.get('data', {})
            if isinstance(web, dict):
                web = web.get('web', [])
            if isinstance(web, list):
                itens = web
        for item in itens:
            url = item.get('url', '')
            desc = item.get('description', '') or ''
            title = item.get('title', '')
            preco = mod.parse_preco(desc) or mod.parse_preco(title)
            if preco:
                resultado.append({'url': url, 'preco': preco, 'desc': desc[:120]})

# grava no PG
tunnel, local = mod.sync.open_tunnel()
pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real",
                         user="postgres", password=mod.sync.PG_PWD, connect_timeout=30)
pconn.autocommit = True
cur = pconn.cursor()
for r in resultado:
    cur.execute("""INSERT INTO concorrente
        (produto_ordem, produto_codigo, produto_nome, ean, concorrente, url, preco, data_coleta, data_preco)
        VALUES (%s,%s,%s,%s,%s,%s,%s, NOW(), %s)""",
        (ordem, codigo, nome, ean, '', r['url'], r['preco'], hoje))
pconn.close(); tunnel.close(); local.close()

print("\nPrecos encontrados e gravados:")
for r in resultado:
    print("  R$ %s | %s" % (r['preco'], r['desc']))
print("Total:", len(resultado))
