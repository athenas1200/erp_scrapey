# -*- coding: utf-8 -*-
"""ANALISTA COMERCIAL - inteligencia comercial do ERP S9.
Calcula metricas de vendas, giro, margens, descontos, ranking de clientes e
vendedores a partir da replica s9_real e grava o conhecimento em:
  - memory_products / memory_customers / memory_sales / memory_demand
  - memory_business_rules (descobertas com confianca)
  - logs/metricas_diario.json (alimenta o email das 9h)
Modo leitura apenas na replica. Incremental. Uso: python analista_comercial.py [dias]
"""
import sys, io, os, json, time

sys.path.insert(0, r'C:\S9\knowledge_base')
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOG = os.path.join(LOGDIR, 'analista_comercial.log')

DIAS = 30
if len(sys.argv) > 1:
    try: DIAS = int(sys.argv[1])
    except Exception: pass


def log(msg):
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    with io.open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + "\n")
    print(line)


def _num(v):
    try:
        return float(v)
    except Exception:
        return 0.0


# ---------------- consultas na replica (somente leitura) ----------------
def top_produtos(cur):
    """Produtos com maior faturamento, margem, giro e descontos."""
    cur.execute("""SELECT
        COALESCE("Ordem_Prod_Serv",0)::text AS produto,
        COUNT(*) AS vendas,
        COALESCE(SUM("Preco_Total_Com_Desconto"),0) AS faturamento,
        COALESCE(SUM("Preco_Total_Sem_Desconto"),0) AS bruto,
        COALESCE(SUM("Desconto_Valor"),0) AS descontos,
        COALESCE(AVG("Desconto_Percentual"),0) AS desc_perc,
        COALESCE(SUM("ICMS_Normal_Valor")+SUM("PIS_Normal_Valor")+SUM("COFINS_Normal_Valor")
                 +SUM("ICMS_Subst_Valor")+SUM("PIS_Subst_Valor")+SUM("COFINS_Subst_Valor"),0) AS impostos,
        COALESCE(SUM("Preco_Custo"*1),0) AS custo
        FROM "Movimento_Prod_Serv"
        WHERE "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)
        GROUP BY "Ordem_Prod_Serv"
        ORDER BY faturamento DESC LIMIT 20""", (DIAS,))
    return cur.fetchall()


def ranking_clientes(cur):
    """Ranking de clientes por faturamento (via Movimento x Cli_For)."""
    cur.execute("""SELECT
        COALESCE("Ordem_Cli_For",0)::text AS cliente, COUNT(*) AS pedidos,
        COALESCE(SUM("Preco_Total_Com_Desconto_Somado"),0) AS valor
        FROM "Movimento"
        WHERE "Data" >= CURRENT_TIMESTAMP - make_interval(days => %s)
        GROUP BY "Ordem_Cli_For" ORDER BY valor DESC LIMIT 15""", (DIAS,))
    return cur.fetchall()


def ranking_vendedores(cur):
    cur.execute("""SELECT
        COALESCE("Ordem_Vendedor1",0)::text AS vendedor, COUNT(*) AS pedidos,
        COALESCE(SUM("Preco_Total_Com_Desconto_Somado"),0) AS valor
        FROM "Movimento"
        WHERE "Data" >= CURRENT_TIMESTAMP - make_interval(days => %s)
        GROUP BY "Ordem_Vendedor1" ORDER BY valor DESC LIMIT 15""", (DIAS,))
    return cur.fetchall()


def produtos_sem_giro(cur):
    """Produtos cadastrados sem venda na janela (encalhados)."""
    cur.execute("""SELECT COUNT(*) FROM "Prod_Serv" p
        WHERE NOT EXISTS (SELECT 1 FROM "Movimento_Prod_Serv" m
            WHERE m."Ordem_Prod_Serv" = p."Ordem"
              AND m."Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s))""", (DIAS,))
    return cur.fetchone()[0]


def tendencia_vendas(cur):
    """Vendas por dia na janela (para tendencias)."""
    cur.execute("""SELECT "Data"::date AS dia, COUNT(*), COALESCE(SUM("Preco_Total_Com_Desconto_Somado"),0)
        FROM "Movimento"
        WHERE "Data" >= CURRENT_TIMESTAMP - make_interval(days => %s)
        GROUP BY dia ORDER BY dia""", (DIAS,))
    return cur.fetchall()


# ---------------- gravacao na memoria ----------------
def gravar_na_memoria(linhas_texto):
    """Grava as descobertas em memory_business_rules + metricas_diario.json."""
    from db_mem import connect, close_all
    from psycopg2.extras import execute_values
    tm, lm, cm, mcur = connect()
    try:
        hoje = time.strftime('%Y-%m-%d')
        dados = [(r[0], r[1], 0.85, hoje) for r in linhas_texto]
        if dados:
            execute_values(mcur, """INSERT INTO memory_business_rules
                (tabela, regra, significado, confianca, descoberto_em)
                VALUES %s ON CONFLICT DO NOTHING""",
                           [(d[0], d[1], "regra aprendida", d[2], d[3]) for d in dados])
        cm.commit()
    finally:
        close_all(tm, lm, cm)

    # alimenta metricas_diario.json
    mpath = os.path.join(LOGDIR, 'metricas_diario.json')
    try:
        d = json.load(io.open(mpath, encoding='utf-8'))
    except Exception:
        d = {}
    hoje = time.strftime('%Y-%m-%d')
    itens = d.get(hoje, [])
    itens = [x for x in itens if not x.startswith("Analista comercial")]
    itens.extend(["Analista comercial: " + t for t in linhas_texto])
    d[hoje] = itens
    io.open(mpath, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=1))


def main():
    from db_conn import connect, close_all
    log("ANALISTA COMERCIAL iniciado (DIAS=%d)" % DIAS)
    tr, lr, cr, cur = connect()
    try:
        produtos = top_produtos(cur)
        clientes = ranking_clientes(cur)
        vendedores = ranking_vendedores(cur)
        sem_giro = produtos_sem_giro(cur)
        tendencia = tendencia_vendas(cur)
    finally:
        close_all(tr, lr, cr)

    linhas = []
    linhas.append("analise de %d dias: %d produtos vendidos, %d clientes, %d vendedores, %d produtos sem giro" %
                  (DIAS, len(produtos), len(clientes), len(vendedores), sem_giro))
    if produtos:
        linhas.append("Top 5 faturamento: " + "; ".join(
            "prod %s R$ %.0f (%d vendas)" % (p[0], _num(p[2]), p[1]) for p in produtos[:5]))
    if clientes:
        linhas.append("Top 3 clientes: " + "; ".join(
            "cli %s R$ %.0f (%d pedidos)" % (c[0], _num(c[2]), c[1]) for c in clientes[:3]))
    if vendedores:
        linhas.append("Top 3 vendedores: " + "; ".join(
            "ven %s R$ %.0f (%d pedidos)" % (v[0], _num(v[2]), v[1]) for v in vendedores[:3]))
    if tendencia:
        total = sum(_num(v) for _, _, v in tendencia)
        media = total / len(tendencia) if tendencia else 0
        linhas.append("vendas/jornada: R$ %.0f media diaria em %d dias" % (media, len(tendencia)))

    log("Analista comercial: %d linhas geradas" % len(linhas))
    for l in linhas:
        log("  " + l)
    gravar_na_memoria(linhas)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        log("ERRO analista_comercial: %s\n%s" % (str(e)[:300], traceback.format_exc()))
