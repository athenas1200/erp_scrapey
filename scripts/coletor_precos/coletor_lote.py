# -*- coding: utf-8 -*-
"""COLETOR LOTE DE PRECOS - 3 concorrentes fixos (Mercado Livre, Shopmedical, Fisio Store).
Para cada produto: busca no site do concorrente (site:Dominio NOME) e, se nao achar
preco, faz fallback no Google (busca geral). Grava na tabela 'concorrente' do PG.
- Checkpoint: retoma de onde parou (logs/coleta_estado.json)
- Rate limit: backoff (espera 60s em erro 429) e pausa entre chamadas
- Log de progresso em logs/coleta_AAAAMMDD_HHMMSS.log
"""
import os, sys, io, json, time, re, subprocess, glob

LOTE = int(sys.argv[1]) if len(sys.argv) > 1 else 0   # 0 = todos
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = BASE + r'\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOCKFILE = LOGDIR + r'\coletor.lock'
PROGLOG = LOGDIR + r'\coleta_%s.log' % time.strftime('%Y%m%d_%H%M%S')
FOTOS_DIR = BASE + r'\FOTOS_CONC'
os.makedirs(FOTOS_DIR, exist_ok=True)
MAX_FOTOS = 4   # maximo de fotos por produto

def contar_fotos_locais(codigo):
    """Conta fotos ja salvas do produto na pasta local FOTOS_CONC do codigo."""
    p = os.path.join(FOTOS_DIR, codigo)
    if not os.path.isdir(p):
        return 0
    try:
        return len([f for f in os.listdir(p) if f.lower().endswith(('.webp', '.jpg', '.png', '.jpeg'))])
    except Exception:
        return 0

def ja_rodando():
    """Verifica se outro coletor esta em execucao (arquivo lock com PID vivo)."""
    if not os.path.exists(LOCKFILE):
        return False
    try:
        with io.open(LOCKFILE, encoding='utf-8') as f:
            pid = int(f.read().strip())
        if pid == os.getpid():
            return False
        # verifica se o processo existe no Windows
        out = subprocess.run(['tasklist', '/FI', 'PID eq %d' % pid],
                             capture_output=True, text=True, timeout=30)
        return str(pid) in out.stdout
    except Exception:
        return False

if ja_rodando():
    sys.exit(0)
with io.open(LOCKFILE, 'w', encoding='utf-8') as f:
    f.write(str(os.getpid()))

# checkpoint unico persistente: se a maquina reiniciar, continua de onde parou
CHECKPOINT = LOGDIR + r'\coleta_estado.json'

sys.path.insert(0, BASE)
import importlib.util
_spec = importlib.util.spec_from_file_location('sync_silencioso', BASE + r'\sync_silencioso.py')
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

CONCORRENTES = [
    {'nome': 'mercadolivre', 'dominio': 'mercadolivre.com.br'},
    {'nome': 'shopmedical', 'dominio': 'shopmedical.com.br'},
    {'nome': 'fisiostore', 'dominio': 'fisiostore.com.br'},
]

def log(msg):
    line = "[%s] %s" % (time.strftime('%H:%M:%S'), msg)
    with io.open(PROGLOG, 'a', encoding='utf-8') as f:
        f.write(line + "\n")
    print(line)

def parse_preco(texto):
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

def extrair_dominio(url):
    """Extrai dominio simples (ex: mercadolivre) de uma URL."""
    if not url:
        return ''
    m = re.search(r'://(?:www\.)?([^/:?]+)', url)
    if not m:
        return ''
    partes = m.group(1).split('.')
    if len(partes) >= 2:
        return partes[0]
    return m.group(1)

def parse_vista_pix(texto):
    avista = pix = None
    if not texto:
        return avista, pix
    m = re.search(r'R\$\s*([\d\.,]+)[^\d]*?\b(?:a\s*vista|avista)\b', texto, re.I)
    if m: avista = parse_preco('R$ ' + m.group(1))
    m = re.search(r'\b(?:no\s*pix|pix)\b[^\d]*?R\$\s*([\d\.,]+)', texto, re.I)
    if m: pix = parse_preco('R$ ' + m.group(1))
    if avista is None:
        m = re.search(r'\b(?:a\s*vista|avista)\b[^\d]*?R\$\s*([\d\.,]+)', texto, re.I)
        if m: avista = parse_preco('R$ ' + m.group(1))
    if pix is None:
        m = re.search(r'R\$\s*([\d\.,]+)[^\d]*?\b(?:no\s*pix|pix)\b', texto, re.I)
        if m: pix = parse_preco('R$ ' + m.group(1))
    return avista, pix

def firecrawl_batch(queries, delay=1.0):
    """Executa varias buscas em UMA sessao MCP. queries = lista de dicts {id, query}.
    Retorna dict {id: [blocos]}. Fallback p/ chamada individual se der erro."""
    if not queries:
        return {}
    payload = {'queries': queries, 'delay': delay}
    try:
        r = subprocess.run([sys.executable, os.path.join(BASE, 'firecrawl_batch.py')],
                           input=json.dumps(payload), capture_output=True,
                           text=True, timeout=3600, cwd=BASE)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or '')[:200])
        out = r.stdout.strip().split('\n')[-1]
        data = json.loads(out)
        return data.get('results', {})
    except Exception:
        return {}

def firecrawl_search(query, tentativas=4):
    """Fallback individual (usado se batch falhar por produto especifico)."""
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
                out.append(getattr(c, 'text', str(c)))
            print(json.dumps(out))

asyncio.run(main())
"""
    for tentativa in range(tentativas):
        try:
            r = subprocess.run([sys.executable, '-c', script, query], capture_output=True,
                               text=True, timeout=120, cwd=BASE)
            if r.returncode != 0:
                err = (r.stderr or '')
                if '429' in err or 'rate' in err.lower() or 'too many' in err.lower():
                    log("  rate limit, aguardando 60s (tent %d)..." % (tentativa + 1))
                    time.sleep(60); continue
                return None
            out = r.stdout.strip().split('\n')[-1]
            return json.loads(out)
        except subprocess.TimeoutExpired:
            time.sleep(10)
        except Exception:
            time.sleep(5)
    return None

def extrair_resultados(res):
    """Transforma blocos JSON do firecrawl em lista de itens (url, description, title)."""
    itens = []
    if not res:
        return itens
    for bloco in res:
        try:
            dados = json.loads(bloco) if bloco.strip().startswith('{') else None
        except Exception:
            dados = None
        if isinstance(dados, dict):
            web = dados.get('data', {})
            if isinstance(web, dict):
                web = web.get('web', [])
            elif not isinstance(web, list):
                web = []
            if isinstance(web, list):
                for item in web:
                    itens.append({
                        'url': item.get('url', ''),
                        'description': item.get('description', '') or '',
                        'title': item.get('title', '') or '',
                    })
    return itens

def salvar_estado(estado):
    with io.open(CHECKPOINT, 'w', encoding='utf-8') as f:
        json.dump(estado, f, ensure_ascii=False)

def carregar_estado():
    if os.path.exists(CHECKPOINT):
        try:
            with io.open(CHECKPOINT, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'feitos': []}

# ---------- SCRAPE DE PAGINA (usar link salvo nas passadas seguintes) ----------
def firecrawl_scrape(url):
    """Scrape de uma URL para extrair preco + imagem. Retorna dict ou None."""
    script = r"""
import sys, json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import asyncio

async def main():
    u = sys.argv[1]
    params = StdioServerParameters(command='npx', args=['-y', 'firecrawl-mcp'])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool('firecrawl_scrape', {'url': u, 'formats': ['markdown', 'links']})
            out = []
            for c in r.content:
                out.append(getattr(c, 'text', str(c)))
            print(json.dumps(out))

asyncio.run(main())
"""
    try:
        r = subprocess.run([sys.executable, '-c', script, url], capture_output=True,
                           text=True, timeout=120, cwd=BASE)
        if r.returncode != 0:
            return None
        out = r.stdout.strip().split('\n')[-1]
        return json.loads(out)
    except Exception:
        return None

def extrair_dados_scrape(blocos):
    """De blocos do firecrawl_scrape extrai preco, avista, pix, url_imagem."""
    preco = avista = pix = None
    img = None
    if not blocos:
        return preco, avista, pix, img
    for bloco in blocos:
        try:
            dados = json.loads(bloco) if bloco.strip().startswith('{') else None
        except Exception:
            dados = None
        if not isinstance(dados, dict):
            continue
        data = dados.get('data', {})
        if isinstance(data, dict):
            md = data.get('markdown') or ''
            preco = parse_preco(md) or preco
            av, px = parse_vista_pix(md)
            avista = av or avista
            pix = px or pix
            links = data.get('links') or data.get('metadata', {}).get('ogImage', '') if isinstance(data.get('links'), list) else None
            if not img:
                meta = data.get('metadata', {}) or {}
                for chave in ('ogImage', 'twitter:image', 'og:image'):
                    v = meta.get(chave)
                    if v:
                        img = v
                        break
                if not img and isinstance(data.get('links'), list):
                    for l in data['links']:
                        if 'http' in str(l) and any(e in str(l).lower() for e in ('.jpg', '.png', '.webp', '.jpeg')):
                            img = str(l)
                            break
    return preco, avista, pix, img

def baixar_foto(url_img, destino):
    """Baixa uma imagem e salva como webp no caminho destino. Retorna True/False."""
    import httpx
    if not url_img:
        return False
    try:
        r = httpx.get(url_img, timeout=30, follow_redirects=True)
        if r.status_code != 200 or len(r.content) < 100:
            return False
        try:
            from PIL import Image
            import io as _io
            im = Image.open(_io.BytesIO(r.content))
            im = im.convert('RGB')
            im.save(destino, 'WEBP', quality=80)
            return True
        except Exception:
            with open(destino, 'wb') as f:
                f.write(r.content)
            return True
    except Exception:
        return False

def main():
    import pyodbc, psycopg2
    hoje = time.strftime('%Y-%m-%d')
    estado = carregar_estado()
    feitos = set(estado.get('feitos', []))

    sconn = pyodbc.connect(sync.SQL_DSN, timeout=30)
    scur = sconn.cursor()
    scur.execute("""SELECT p.Ordem, p.Codigo, p.Nome, p.Codigo_Barras, p.Codigo_Adicional3, pr.Preco
        FROM dbo.Prod_Serv p
        LEFT JOIN (SELECT Ordem_Prod_Serv, Preco FROM dbo.Prod_Serv_Precos
            WHERE Ordem_Tabela_Preco = 7) pr ON pr.Ordem_Prod_Serv = p.Ordem
        WHERE p.Codigo_Barras IS NOT NULL AND LEN(p.Codigo_Barras) >= 8
        ORDER BY p.Codigo""")
    produtos = scur.fetchall()
    sconn.close()
    log("Total produtos candidatos: %d | ja feitos: %d" % (len(produtos), len(feitos)))

    tunnel, local = sync.open_tunnel()
    pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real",
                             user="postgres", password=sync.PG_PWD, connect_timeout=30)
    pconn.autocommit = True
    cur = pconn.cursor()

    n_novo = 0
    n_total = 0
    t0 = time.time()
    for idx, (ordem, codigo, nome, ean, ean3, preco_atual) in enumerate(produtos):
        if LOTE and idx >= LOTE:
            break
        if codigo in feitos:
            continue

        # --- ja tem link mapeado? (passada seguinte: usa link salvo, rapido) ---
        cur.execute("""SELECT url, concorrente, site_empresa FROM concorrente
            WHERE produto_codigo=%s AND url IS NOT NULL AND url<>''
            ORDER BY data_coleta DESC""", (codigo,))
        links_antigos = cur.fetchall()
        if links_antigos:
            # tenta scrape dos links salvos (o link mais recente primeiro)
            achou = False
            for u, conc_nome, site_emp in links_antigos[:3]:
                blocos = firecrawl_scrape(u)
                preco, avista, pix, img = extrair_dados_scrape(blocos)
                if preco:
                    # cria registro NOVO (historico): nunca sobrescreve o anterior
                    cur.execute("""INSERT INTO concorrente
                        (produto_ordem, produto_codigo, produto_nome, ean, ean3, concorrente, url, preco,
                         preco_avista, preco_pix, site_empresa, cidade, estado, data_coleta, data_preco)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW(), %s)""",
                        (ordem, codigo, nome, ean, ean3, conc_nome or '', u, preco,
                         avista, pix, site_emp or ('https://' + extrair_dominio(u)), '', '', hoje))
                    if img and contar_fotos_locais(codigo) < MAX_FOTOS:
                        dom = extrair_dominio(u)
                        fpasta = os.path.join(FOTOS_DIR, codigo)
                        os.makedirs(fpasta, exist_ok=True)
                        fdest = os.path.join(fpasta, '%s.webp' % dom)
                        baixar_foto(img, fdest)
                        cur.execute("UPDATE concorrente SET foto_local=%s WHERE url=%s AND produto_codigo=%s AND data_preco=%s AND foto_local IS NULL",
                                    (fdest, u, codigo, hoje))
                    achou = True
                    n_novo += 1
                    break
                time.sleep(1)
            if achou:
                feitos.add(codigo)
                estado['feitos'] = sorted(feitos)
                salvar_estado(estado)
                continue

        # --- nao tem link (ou link sem preco): busca nova empresa ate ter 3 precos ---
        coletados = []
        usados = set()
        for conc in CONCORRENTES:
            if len(coletados) >= 3:
                break
            q1 = 'site:%s %s preco' % (conc['dominio'], nome)
            res = firecrawl_search(q1)
            for item in extrair_resultados(res):
                preco = parse_preco(item['description']) or parse_preco(item['title'])
                if not preco:
                    continue
                u = (item['url'] or '').lower()
                dom = extrair_dominio(u)
                if not dom or dom in usados:
                    continue
                avista, pix = parse_vista_pix(item['description']) or parse_vista_pix(item['title'])
                coletados.append({'site': dom, 'url': item['url'], 'preco': preco,
                                  'avista': avista, 'pix': pix, 'nome_conc': conc['nome']})
                usados.add(dom)
                break
            time.sleep(2)
        tent_fb = 0
        while len(coletados) < 3 and tent_fb < 5:
            q2 = '%s preco' % nome
            res2 = firecrawl_search(q2)
            for item in extrair_resultados(res2):
                preco = parse_preco(item['description']) or parse_preco(item['title'])
                if not preco:
                    continue
                u = (item['url'] or '').lower()
                dom = extrair_dominio(u)
                if not dom or dom in usados:
                    continue
                if any(lp in u for lp in ('relaxmedic', 'pedeapoio', 'gruporelaxmedic')):
                    continue
                avista, pix = parse_vista_pix(item['description']) or parse_vista_pix(item['title'])
                coletados.append({'site': dom, 'url': item['url'], 'preco': preco,
                                  'avista': avista, 'pix': pix, 'nome_conc': dom})
                usados.add(dom)
                break
            tent_fb += 1
            time.sleep(2)
        # grava (na primeira passada baixa a foto do concorrente e salva local,
        # no maximo MAX_FOTOS por produto - se ja tem, nao baixa mais)
        fpasta = os.path.join(FOTOS_DIR, codigo)
        n_fotos = contar_fotos_locais(codigo)
        for c in coletados:
            foto_local = None
            img = None
            if n_fotos < MAX_FOTOS:
                blocos = firecrawl_scrape(c['url'])
                if blocos:
                    p2, a2, px2, im2 = extrair_dados_scrape(blocos)
                    img = im2
                    if not c['preco'] and p2:
                        c['preco'] = p2
                    if not c['avista'] and a2:
                        c['avista'] = a2
                    if not c['pix'] and px2:
                        c['pix'] = px2
                if img:
                    os.makedirs(fpasta, exist_ok=True)
                    foto_local = os.path.join(fpasta, '%s.webp' % c['site'])
                    if baixar_foto(img, foto_local):
                        n_fotos += 1
                    else:
                        foto_local = None
            cur.execute("""INSERT INTO concorrente
                (produto_ordem, produto_codigo, produto_nome, ean, ean3, concorrente, url, preco,
                 preco_avista, preco_pix, site_empresa, cidade, estado, foto_local, data_coleta, data_preco)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW(), %s)""",
                (ordem, codigo, nome, ean, ean3, c['nome_conc'], c['url'], c['preco'],
                 c['avista'], c['pix'], 'https://' + c['site'], '', '', foto_local, hoje))
        n_novo += len(coletados)
        n_total += 1
        feitos.add(codigo)
        estado['feitos'] = sorted(feitos)
        salvar_estado(estado)
        if len(coletados) < 3:
            log("  %s | %s | so %d precos" % (codigo, nome[:35], len(coletados)))
        if (idx + 1) % 25 == 0:
            dec = (time.time() - t0) / max(len(feitos), 1)
            log("  %d/%d | registros novos: %d | media %.1fs/produto | estado salvo" %
                (len(feitos), len(produtos), n_novo, dec))

    pconn.close(); tunnel.close(); local.close()
    try:
        os.remove(LOCKFILE)
    except Exception:
        pass
    # se todos os produtos foram coletados, zera o estado p/ proximo ciclo diario
    if len(feitos) >= len(produtos):
        try:
            os.remove(CHECKPOINT)
        except Exception:
            pass
        log("Ciclo completo (%d produtos) - estado zerado para proxima coleta" % len(produtos))
    log("FIM. Registros inseridos: %d | buscas: %d | duracao %.0f min" %
        (n_novo, n_total, (time.time() - t0) / 60))

if __name__ == '__main__':
    main()
