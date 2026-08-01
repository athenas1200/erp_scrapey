# -*- coding: utf-8 -*-
"""COLETOR DIARIO DE PRECOS CONCORRENTES - Firecrawl + tabela concorrente.
1. Conecta no SQL Server e lista produtos (com EAN/nome).
2. Para cada produto, usa Firecrawl search (keyless) para achar preços na internet.
3. Grava na tabela 'concorrente' do PostgreSQL.
Roda silencioso, log em logs/concorrente_AAAA-MM-DD.json
"""
import os, sys, io, json, time, re, subprocess, threading

LIMITE_PADRAO = int(sys.argv[1]) if len(sys.argv) > 1 else 20

BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = BASE + r'\logs'
os.makedirs(LOGDIR, exist_ok=True)

sys.path.insert(0, BASE)
import importlib.util
_spec = importlib.util.spec_from_file_location('sync_silencioso', BASE + r'\sync_silencioso.py')
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

SQL_DSN = sync.SQL_DSN

def parse_preco(texto):
    """Extrai preco R$ do texto. Retorna float ou None."""
    if not texto:
        return None
    m = re.search(r'R\$\s*([\d\.,]+)', texto)
    if m:
        s = m.group(1)
        s = s.replace('.', '').replace(',', '.') if s.count(',') == 1 else s.replace(',', '')
        try:
            return round(float(s), 2)
        except Exception:
            return None
    return None

def parse_preco_vista_pix(texto):
    """Extrai preco a vista e pix de textos tipo 'R$ 150,00 a vista' / 'no pix R$ 140,00'."""
    avista = pix = None
    if not texto:
        return avista, pix
    m = re.search(r'R\$\s*([\d\.,]+)[^\d]*?\b(?:a\s*vista|avista|a vista)\b', texto, re.I)
    if m:
        avista = parse_preco('R$ ' + m.group(1))
    m = re.search(r'\b(?:no\s*pix|no\s*pix|pix)\b[^\d]*?R\$\s*([\d\.,]+)', texto, re.I)
    if m:
        pix = parse_preco('R$ ' + m.group(1))
    if avista is None:
        m = re.search(r'\b(?:a\s*vista|avista)\b[^\d]*?R\$\s*([\d\.,]+)', texto, re.I)
        if m:
            avista = parse_preco('R$ ' + m.group(1))
    if pix is None:
        m = re.search(r'R\$\s*([\d\.,]+)[^\d]*?\b(?:no\s*pix|pix)\b', texto, re.I)
        if m:
            pix = parse_preco('R$ ' + m.group(1))
    return avista, pix

def nome_empresa_url(url):
    """Deriva um nome de empresa legivel a partir do URL (dominio)."""
    if not url:
        return ''
    m = re.search(r'://(?:www\.)?([^/]+)', url)
    if not m:
        return ''
    dom = m.group(1)
    base = dom.split('.')[0]
    base = base.replace('-', ' ').replace('_', ' ').strip()
    return (base or dom)[:100]

def site_empresa_url(url):
    """Deriva o site raiz (scheme + dominio) a partir do URL."""
    if not url:
        return ''
    m = re.search(r'^(https?://(?:www\.)?[^/]+)', url)
    return m.group(1) if m else ''

def firecrawl_search(query):
    """Chama firecrawl-mcp search via MCP stdio (keyless). Retorna resultados."""
    script = r"""
import sys, json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import asyncio

async def main():
    q = sys.argv[1]
    params = StdioServerParameters(command='npx', args=['-y', 'firecrawl-mcp'])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool('firecrawl_search', {'query': q, 'limit': 5, 'lang': 'pt', 'country': 'br'})
            out = []
            for c in r.content:
                t = getattr(c, 'text', str(c))
                out.append(t)
            print(json.dumps(out))

asyncio.run(main())
"""
    try:
        r = subprocess.run([sys.executable, '-c', script, query], capture_output=True,
                           text=True, timeout=120, cwd=BASE,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if r.returncode != 0:
            return None
        out = r.stdout.strip().split('\n')[-1]
        return json.loads(out)
    except Exception:
        return None

LOJA_PROPRIAS = ('relaxmedic', 'pedeapoio', 'gruporelaxmedic')

def main():
    import pyodbc
    hoje = time.strftime('%Y-%m-%d')
    sconn = pyodbc.connect(SQL_DSN, timeout=30)
    scur = sconn.cursor()
    scur.execute("""SELECT p.Ordem, p.Codigo, p.Nome, p.Codigo_Barras, pr.Preco
        FROM dbo.Prod_Serv p
        LEFT JOIN (SELECT Ordem_Prod_Serv, Preco FROM dbo.Prod_Serv_Precos
            WHERE Ordem_Tabela_Preco = 7) pr ON pr.Ordem_Prod_Serv = p.Ordem
        WHERE p.Codigo_Barras IS NOT NULL AND LEN(p.Codigo_Barras) >= 8
        ORDER BY p.Codigo""")
    produtos = scur.fetchall()
    sconn.close()

    resultado = []
    for i, (ordem, codigo, nome, ean, preco_atual) in enumerate(produtos):
        if i >= 3:
            break
        q = '%s preco' % nome
        res = firecrawl_search(q)
        if not res:
            continue
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
                elif isinstance(web, list):
                    pass
                if isinstance(web, list):
                    itens = web
            if not itens:
                continue
            for item in itens:
                url = item.get('url', '')
                desc = item.get('description', '') or ''
                title = item.get('title', '')
                preco = parse_preco(desc) or parse_preco(title)
                if not preco:
                    continue
                avista, pix = parse_preco_vista_pix(desc) or parse_preco_vista_pix(title)
                # exclui lojas proprias
                u = (url or '').lower()
                if any(lp in u for lp in LOJA_PROPRIAS):
                    continue
                resultado.append({
                    'produto_ordem': ordem, 'produto_codigo': codigo,
                    'produto_nome': nome, 'ean': ean,
                    'concorrente': nome_empresa_url(url), 'url': url,
                    'preco': preco, 'preco_avista': avista, 'preco_pix': pix,
                    'site_empresa': site_empresa_url(url),
                    'cidade': '', 'estado': '',
                    'preco_atual_loja': float(preco_atual) if preco_atual else None,
                    'data': hoje,
                })
        print("  %s | %s | %d precos" % (codigo, nome[:40], len([r for r in resultado if r['produto_codigo']==codigo])))
        time.sleep(2)

    # grava no PG
    try:
        import psycopg2
        tunnel, local = sync.open_tunnel()
        pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real",
                                 user="postgres", password=sync.PG_PWD, connect_timeout=30)
        pconn.autocommit = True
        cur = pconn.cursor()
        for r in resultado:
            cur.execute("""INSERT INTO concorrente
                (produto_ordem, produto_codigo, produto_nome, ean, concorrente, url, preco,
                 preco_avista, preco_pix, site_empresa, cidade, estado, data_coleta, data_preco)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW(), %s)""",
                (r['produto_ordem'], r['produto_codigo'], r['produto_nome'], r['ean'],
                 r['concorrente'], r['url'], r['preco'], r['preco_avista'], r['preco_pix'],
                 r['site_empresa'], r['cidade'], r['estado'], r['data']))
        pconn.close(); tunnel.close(); local.close()
    except Exception as e:
        print("ERRO PG:", str(e)[:150])

    with io.open(LOGDIR + r'\concorrente_%s.json' % hoje, 'w', encoding='utf-8') as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print("\nTotal precos coletados:", len(resultado))

if __name__ == '__main__':
    main()
