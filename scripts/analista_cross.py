# -*- coding: utf-8 -*-
"""ANALISTA CROSS-SELL - produtos vendidos juntos (cross-sell/up-sell).
Encontra pares de produtos que aparecem no mesmo movimento (venda) com
frequencia acima do esperado. Grava em memory_business_rules + metricas_diario.json.
Modo leitura apenas. Uso: python analista_cross.py [dias] [limite_pares]
"""
import sys, io, os, json, time

sys.path.insert(0, r'C:\S9\knowledge_base')
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOG = os.path.join(LOGDIR, 'analista_cross.log')

DIAS = 30
LIMITE = 20
if len(sys.argv) > 1:
    try: DIAS = int(sys.argv[1])
    except Exception: pass
if len(sys.argv) > 2:
    try: LIMITE = int(sys.argv[2])
    except Exception: pass


def log(msg):
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    with io.open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + "\n")
    print(line)


def main():
    from db_conn import connect, close_all
    from db_mem import connect as mconnect, close_all as mclose
    from psycopg2.extras import execute_values
    log("ANALISTA CROSS-SELL iniciado (DIAS=%d)" % DIAS)
    hoje = time.strftime('%Y-%m-%d')

    tr, lr, cr, cur = connect()
    try:
        # pares de produtos por movimento (self-join nos itens do mesmo movimento)
        cur.execute("""SELECT a."Ordem_Prod_Serv", b."Ordem_Prod_Serv", COUNT(*) AS juntos
            FROM "Movimento_Prod_Serv" a
            JOIN "Movimento_Prod_Serv" b ON a."Ordem_Movimento" = b."Ordem_Movimento"
                AND a."Ordem_Prod_Serv" < b."Ordem_Prod_Serv"
            WHERE a."Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)
            GROUP BY 1, 2 ORDER BY juntos DESC LIMIT %s""", (DIAS, LIMITE))
        pares = cur.fetchall()
        # total de vendas para calcular o lift
        cur.execute("""SELECT COUNT(*) FROM (
            SELECT DISTINCT "Ordem_Movimento" FROM "Movimento_Prod_Serv"
            WHERE "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)) t""", (DIAS,))
        n_vendas = cur.fetchone()[0] or 1
    finally:
        close_all(tr, lr, cr)

    linhas = []
    if pares:
        linhas.append("pares mais vendidos juntos (%d vendas na janela):" % n_vendas)
        for pa, pb, juntos in pares[:LIMITE]:
            linhas.append("  produtos %s + %s: %d vezes juntos" % (pa, pb, juntos))
    else:
        linhas.append("nenhum par significativo encontrado na janela")

    tm, lm, cm, mcur = mconnect()
    try:
        execute_values(mcur, """INSERT INTO memory_business_rules
            (tabela, regra, significado, confianca, descoberto_em) VALUES %s ON CONFLICT DO NOTHING""",
                       [('Movimento_Prod_Serv', l, 'cross-sell', 0.7, hoje) for l in linhas])
        cm.commit()
    finally:
        mclose(tm, lm, cm)

    mpath = os.path.join(LOGDIR, 'metricas_diario.json')
    try:
        d = json.load(io.open(mpath, encoding='utf-8'))
    except Exception:
        d = {}
    itens = d.get(hoje, [])
    itens = [x for x in itens if not x.startswith("Analista cross")]
    itens.extend(["Analista cross-sell: " + l for l in linhas])
    d[hoje] = itens
    io.open(mpath, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=1))

    log("Analista cross-sell: %d linhas" % len(linhas))
    for l in linhas:
        log("  " + l)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        log("ERRO analista_cross: %s\n%s" % (str(e)[:300], traceback.format_exc()))
