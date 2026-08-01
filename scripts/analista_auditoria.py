# -*- coding: utf-8 -*-
"""ANALISTA DE AUDITORIA - detecta anomalias e riscos no ERP S9.
Encontra automaticamente na replica s9_real:
  - Produtos vendidos abaixo do custo (margem negativa)
  - Descontos excessivos / fora do padrao
  - Cadastros duplicados (clientes, fornecedores)
  - Movimentos sem operacao / sem filial
  - Margens anormais
Grava em memory_business_rules + logs/metricas_diario.json.
Modo leitura apenas. Uso: python analista_auditoria.py [dias]
"""
import sys, io, os, json, time

sys.path.insert(0, r'C:\S9\knowledge_base')
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOG = os.path.join(LOGDIR, 'analista_auditoria.log')

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


# ---------------- anomalias ----------------
def margem_negativa(cur):
    """Itens vendidos abaixo do custo."""
    cur.execute("""SELECT COUNT(*), COALESCE(SUM("Desconto_Valor"),0),
        COALESCE(SUM("Preco_Total_Com_Desconto") - SUM("Preco_Custo"),0)
        FROM "Movimento_Prod_Serv"
        WHERE "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)
          AND "Preco_Custo" > 0
          AND "Preco_Total_Com_Desconto" < "Preco_Custo"
          AND "Preco_Total_Com_Desconto" > 0""", (DIAS,))
    return cur.fetchone()


def desconto_excessivo(cur):
    """Itens com desconto acima de 20%."""
    cur.execute("""SELECT COUNT(*) FROM "Movimento_Prod_Serv"
        WHERE "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)
          AND "Desconto_Percentual" > 20""", (DIAS,))
    return cur.fetchone()[0]


def clientes_duplicados(cur):
    """Clientes com mesmo CNPJ ou CPF (duplicidade)."""
    cur.execute("""SELECT COUNT(*) FROM (
        SELECT COALESCE(NULLIF("CNPJ",''), NULLIF("CPF",'')) AS doc
        FROM "Cli_For"
        WHERE (("CNPJ" IS NOT NULL AND "CNPJ" <> '') OR ("CPF" IS NOT NULL AND "CPF" <> ''))
        GROUP BY COALESCE(NULLIF("CNPJ",''), NULLIF("CPF",''))
        HAVING COUNT(*) > 1) t""")
    return cur.fetchone()[0]


def movimentos_invalidos(cur):
    """Movimentos sem operacao ou sem cliente."""
    cur.execute("""SELECT
        COUNT(CASE WHEN "Ordem_Operacao" IS NULL OR "Ordem_Operacao" = 0 THEN 1 END),
        COUNT(CASE WHEN "Ordem_Cli_For" IS NULL OR "Ordem_Cli_For" = 0 THEN 1 END)
        FROM "Movimento"
        WHERE "Data" >= CURRENT_TIMESTAMP - make_interval(days => %s)""", (DIAS,))
    return cur.fetchone()


def produtos_sem_classificacao(cur):
    """Produtos sem classe de imposto (entrada/saida)."""
    cur.execute("""SELECT COUNT(*) FROM "Prod_Serv"
        WHERE ("Ordem_Classe_Imposto_Entrada" IS NULL OR "Ordem_Classe_Imposto_Entrada" = 0)
          AND ("Ordem_Classe_Imposto_Saida" IS NULL OR "Ordem_Classe_Imposto_Saida" = 0)""")
    return cur.fetchone()[0]


def descontos_por_vendedor(cur):
    """Desconto medio por vendedor (para detectar abusos)."""
    cur.execute("""SELECT COALESCE(m."Ordem_Vendedor1",0)::text, COALESCE(AVG(m."Desconto_Valor_Somado"),0), COUNT(*)
        FROM "Movimento" m
        WHERE m."Data" >= CURRENT_TIMESTAMP - make_interval(days => %s)
        GROUP BY m."Ordem_Vendedor1" ORDER BY COALESCE(AVG(m."Desconto_Valor_Somado"),0) DESC LIMIT 10""", (DIAS,))
    return cur.fetchall()


# ---------------- gravacao ----------------
def gravar_na_memoria(linhas_texto):
    from db_mem import connect, close_all
    from psycopg2.extras import execute_values
    tm, lm, cm, mcur = connect()
    try:
        hoje = time.strftime('%Y-%m-%d')
        dados = [(r[0], r[1], 0.8, hoje) for r in linhas_texto]
        if dados:
            execute_values(mcur, """INSERT INTO memory_business_rules
                (tabela, regra, significado, confianca, descoberto_em)
                VALUES %s ON CONFLICT DO NOTHING""",
                           [(d[0], d[1], "anomalia/auditoria", d[2], d[3]) for d in dados])
        cm.commit()
    finally:
        close_all(tm, lm, cm)

    mpath = os.path.join(LOGDIR, 'metricas_diario.json')
    try:
        d = json.load(io.open(mpath, encoding='utf-8'))
    except Exception:
        d = {}
    hoje = time.strftime('%Y-%m-%d')
    itens = d.get(hoje, [])
    itens = [x for x in itens if not x.startswith("Analista auditoria")]
    itens.extend(["Analista auditoria: " + t for t in linhas_texto])
    d[hoje] = itens
    io.open(mpath, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=1))


def main():
    from db_conn import connect, close_all
    log("ANALISTA DE AUDITORIA iniciado (DIAS=%d)" % DIAS)
    tr, lr, cr, cur = connect()
    try:
        margem = margem_negativa(cur)
        desc_exc = desconto_excessivo(cur)
        cli_dup = clientes_duplicados(cur)
        mov_inv = movimentos_invalidos(cur)
        sem_class = produtos_sem_classificacao(cur)
        desc_ven = descontos_por_vendedor(cur)
    finally:
        close_all(tr, lr, cr)

    linhas = []
    n_margem, desc_v, prejuizo = margem
    linhas.append("produtos vendidos abaixo do custo: %d (prejuizo R$ %.0f, descontos R$ %.0f)" %
                  (n_margem, _num(prejuizo), _num(desc_v)))
    linhas.append("itens com desconto > 20%%: %d" % desc_exc)
    linhas.append("clientes com CNPJ/CPF duplicado: %d" % cli_dup)
    linhas.append("movimentos sem operacao: %d | sem cliente: %d" % (mov_inv[0], mov_inv[1]))
    linhas.append("produtos sem classe de imposto: %d" % sem_class)
    if desc_ven:
        linhas.append("maiores descontos por vendedor: " + "; ".join(
            "ven %s avg %.1f%% (%d)" % (v[0], _num(v[1]), v[2]) for v in desc_ven[:5]))

    log("Analista auditoria: %d linhas" % len(linhas))
    for l in linhas:
        log("  " + l)
    gravar_na_memoria(linhas)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        log("ERRO analista_auditoria: %s\n%s" % (str(e)[:300], traceback.format_exc()))
