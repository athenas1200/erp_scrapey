# -*- coding: utf-8 -*-
"""ANALISTA DE ALERTAS - consolida riscos fiscais e comerciais em prioridades.
Reune achados dos demais analistas + consultas proprias e gera uma lista de
alertas classificados por gravidade (ALTA/MEDIA/BAIXA) com origem (tabela/campo).
Grava em memory_business_rules + logs/alertas_YYYY-MM-DD.json + metricas_diario.json.
Modo leitura apenas. Uso: python analista_alertas.py [dias]
"""
import sys, io, os, json, time

sys.path.insert(0, r'C:\S9\knowledge_base')
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOG = os.path.join(LOGDIR, 'analista_alertas.log')

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


def main():
    from db_conn import connect, close_all
    from db_mem import connect as mconnect, close_all as mclose
    from psycopg2.extras import execute_values
    log("ANALISTA DE ALERTAS iniciado (DIAS=%d)" % DIAS)
    hoje = time.strftime('%Y-%m-%d')
    alertas = []

    tr, lr, cr, cur = connect()
    try:
        # 1. ST sem CEST (risco fiscal alto)
        cur.execute("""SELECT COUNT(*) FROM "Movimento_Prod_Serv"
            WHERE "ICMS_CST_CSOSN" IN ('10','30','70','90') AND ("CEST" IS NULL OR "CEST" = '')
              AND "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)""", (DIAS,))
        n = cur.fetchone()[0]
        if n:
            alertas.append(("ALTA", "Substituicao tributaria sem CEST", "%d itens com CST 10/30/70/90 sem CEST" % n,
                            "Movimento_Prod_Serv.ICMS_CST_CSOSN / CEST"))

        # 2. margem negativa (venda abaixo do custo)
        cur.execute("""SELECT COUNT(*), COALESCE(SUM("Preco_Total_Com_Desconto" - "Preco_Custo"),0)
            FROM "Movimento_Prod_Serv"
            WHERE "Preco_Custo" > 0 AND "Preco_Total_Com_Desconto" < "Preco_Custo"
              AND "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)""", (DIAS,))
        n, prej = cur.fetchone()
        if n:
            alertas.append(("ALTA", "Vendas abaixo do custo", "%d itens, prejuizo R$ %.2f" % (n, _num(prej)),
                            "Movimento_Prod_Serv.Preco_Custo / Preco_Total_Com_Desconto"))

        # 3. desconto excessivo (> 20%)
        cur.execute("""SELECT COUNT(*) FROM "Movimento_Prod_Serv"
            WHERE "Desconto_Percentual" > 20
              AND "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)""", (DIAS,))
        n = cur.fetchone()[0]
        if n:
            alertas.append(("MEDIA", "Descontos excessivos", "%d itens com desconto > 20%%" % n,
                            "Movimento_Prod_Serv.Desconto_Percentual"))

        # 4. clientes duplicados
        cur.execute("""SELECT COUNT(*) FROM (SELECT COALESCE(NULLIF("CNPJ",''),NULLIF("CPF",'')) AS d
            FROM "Cli_For" WHERE ("CNPJ" IS NOT NULL AND "CNPJ"<>'') OR ("CPF" IS NOT NULL AND "CPF"<>'')
            GROUP BY 1 HAVING COUNT(*) > 1) t""")
        n = cur.fetchone()[0]
        if n:
            alertas.append(("MEDIA", "Clientes/fornecedores duplicados", "%d CNPJ/CPF repetidos" % n,
                            "Cli_For.CNPJ / CPF"))

        # 5. aliquotas ICMS anormais
        cur.execute("""SELECT COUNT(*) FROM "Movimento_Prod_Serv"
            WHERE ("ICMS_Normal_Percentual" > 20 OR "ICMS_Normal_Percentual" < 0)
              AND "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)""", (DIAS,))
        n = cur.fetchone()[0]
        if n:
            alertas.append(("MEDIA", "Aliquotas ICMS fora do padrao", "%d itens >20%% ou negativas" % n,
                            "Movimento_Prod_Serv.ICMS_Normal_Percentual"))

        # 6. produtos sem classe de imposto
        cur.execute("""SELECT COUNT(*) FROM "Prod_Serv"
            WHERE ("Ordem_Classe_Imposto_Entrada" IS NULL OR "Ordem_Classe_Imposto_Entrada"=0)
              AND ("Ordem_Classe_Imposto_Saida" IS NULL OR "Ordem_Classe_Imposto_Saida"=0)""")
        n = cur.fetchone()[0]
        if n:
            alertas.append(("BAIXA", "Produtos sem classe de imposto", "%d produtos" % n,
                            "Prod_Serv.Ordem_Classe_Imposto_Entrada/Saida"))
    finally:
        close_all(tr, lr, cr)

    # 7. NCM sem regra rt_regras_ncm (reforma) - consulta na replica separada
    tr2, lr2, cr2, cur2 = connect()
    try:
        cur2.execute("""SELECT "NCM", COUNT(*) FROM "Movimento_Prod_Serv"
            WHERE "NCM" IS NOT NULL AND "NCM"<>'' AND "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)
            GROUP BY 1 ORDER BY 2 DESC LIMIT 10""", (DIAS,))
        top_ncm = cur2.fetchall()
    finally:
        close_all(tr2, lr2, cr2)

    tm, lm, cm, mcur = mconnect()
    try:
        mcur.execute("SELECT ncm FROM rt_regras_ncm")
        ncm_reg = {r[0] for r in mcur.fetchall()}
        sem_regra = [r for r in top_ncm if str(r[0]) not in ncm_reg]
        if sem_regra:
            alertas.append(("MEDIA", "NCM sem regra CBS/IBS cadastrada",
                            "; ".join("%s x%d" % (r[0], r[1]) for r in sem_regra[:5]),
                            "rt_regras_ncm (parametro)"))

        # grava alertas na memoria
        execute_values(mcur, """INSERT INTO memory_business_rules
            (tabela, regra, significado, confianca, descoberto_em) VALUES %s ON CONFLICT DO NOTHING""",
                       [('alertas', "[%s] %s: %s (%s)" % (g, t, d, o), 'alerta risco', 0.9, hoje)
                        for g, t, d, o in alertas])
        cm.commit()
    finally:
        mclose(tm, lm, cm)

    # salva alertas em arquivo
    arq = os.path.join(LOGDIR, 'alertas_%s.json' % hoje)
    io.open(arq, 'w', encoding='utf-8').write(json.dumps(
        {'data': hoje, 'alertas': [{'gravidade': g, 'titulo': t, 'detalhe': d, 'origem': o}
                                   for g, t, d, o in alertas]}, ensure_ascii=False, indent=1))

    linhas = ["%d alerta(s) de risco identificado(s):" % len(alertas)]
    for g, t, d, o in alertas:
        linhas.append("  [%s] %s: %s (fonte: %s)" % (g, t, d, o))

    # alimenta metricas_diario.json
    mpath = os.path.join(LOGDIR, 'metricas_diario.json')
    try:
        d = json.load(io.open(mpath, encoding='utf-8'))
    except Exception:
        d = {}
    itens = d.get(hoje, [])
    itens = [x for x in itens if not x.startswith("Analista alertas")]
    itens.extend(["Analista alertas: " + l for l in linhas])
    d[hoje] = itens
    io.open(mpath, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=1))

    log("Analista alertas: %d alertas" % len(alertas))
    for l in linhas:
        log("  " + l)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        log("ERRO analista_alertas: %s\n%s" % (str(e)[:300], traceback.format_exc()))
