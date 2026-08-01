# -*- coding: utf-8 -*-
"""ANALISTA DE SIMULACAO - impacto da reforma tributaria (CBS + IBS) no ERP.
Simula, por classe de imposto e por produto, a comparacao entre:
  - Regime atual: ICMS + PIS + COFINS (e ICMS-ST)
  - Regime futuro: CBS + IBS (Reforma Tributaria - Dual VAT)
Calcula impacto medio na carga, no preco e na margem.
Grava em memory_fiscal + memory_business_rules + metricas_diario.json.
Modo leitura apenas. Uso: python analista_simulacao.py [dias]
"""
import sys, io, os, json, time

sys.path.insert(0, r'C:\S9\knowledge_base')
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOG = os.path.join(LOGDIR, 'analista_simulacao.log')

DIAS = 30
if len(sys.argv) > 1:
    try: DIAS = int(sys.argv[1])
    except Exception: pass

# aliquotas hipoteticas de referencia para a simulacao quando o ERP
# ainda nao tem CBS/IBS preenchidos (baseado na reforma / valores usuais)
ALIQ_CBS_REF = 8.0     # CBS federal (projecao)
ALIQ_IBS_REF = 15.5    # IBS (estados 8.5% + municipios 7% - projecao media)


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


def simular_por_classe(cur):
    """Carga atual vs futura agrupada por CST/CSOSN da Classe de Imposto."""
    cur.execute("""SELECT
        "ICMS_CST_CSOSN",
        COALESCE(AVG("ICMS_Percentual_Norm"),0) AS icms,
        COALESCE(AVG("PIS_Percentual_Norm"),0) AS pis,
        COALESCE(AVG("COFINS_Percentual_Norm"),0) AS cofins,
        COALESCE(AVG("ICMS_Percentual_Subs"),0) AS icms_st,
        COALESCE(AVG("CBS_Percentual_TribReg"),0) AS cbs,
        COALESCE(AVG("IBS_Est_Percentual_TribReg"),0) AS ibs_est,
        COALESCE(AVG("IBS_Mun_Percentual_TribReg"),0) AS ibs_mun,
        COUNT(*) AS n
        FROM "Classe_Imposto_Operacao"
        GROUP BY "ICMS_CST_CSOSN" ORDER BY n DESC LIMIT 12""")
    return cur.fetchall()


def simular_por_item(cur):
    """Carga atual vs futura nos itens de movimento (margem media)."""
    cur.execute("""SELECT
        COALESCE(AVG("Preco_Custo"),0) AS custo_medio,
        COALESCE(AVG("Preco_Total_Sem_Desconto"),0) AS preco_medio,
        COALESCE(AVG("ICMS_Normal_Percentual")+AVG("PIS_Normal_Percentual")+AVG("COFINS_Normal_Percentual"),0) AS carga_atual,
        COALESCE(AVG("CBS_Percentual")+AVG("IBS_Est_Percentual")+AVG("IBS_Mun_Percentual"),0) AS carga_futura_registrada,
        COALESCE(AVG("CBS_Valor"),0) AS cbs_valor,
        COALESCE(AVG("IBS_Valor"),0) AS ibs_valor,
        COUNT(*) AS n
        FROM "Movimento_Prod_Serv"
        WHERE "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)""", (DIAS,))
    return cur.fetchone()


def gravar_na_memoria(linhas_texto):
    from db_mem import connect, close_all
    from psycopg2.extras import execute_values
    tm, lm, cm, mcur = connect()
    try:
        hoje = time.strftime('%Y-%m-%d')
        dados = [(r[0], r[1], 0.7, hoje) for r in linhas_texto]
        if dados:
            execute_values(mcur, """INSERT INTO memory_business_rules
                (tabela, regra, significado, confianca, descoberto_em)
                VALUES %s ON CONFLICT DO NOTHING""",
                           [(d[0], d[1], "simulacao reforma tributaria", d[2], d[3]) for d in dados])
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
    itens = [x for x in itens if not x.startswith("Analista simulacao")]
    itens.extend(["Analista simulacao: " + t for t in linhas_texto])
    d[hoje] = itens
    io.open(mpath, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=1))


def main():
    from db_conn import connect, close_all
    log("ANALISTA DE SIMULACAO iniciado (DIAS=%d)" % DIAS)
    tr, lr, cr, cur = connect()
    try:
        por_classe = simular_por_classe(cur)
        item = simular_por_item(cur)
    finally:
        close_all(tr, lr, cr)

    custo, preco, carga_atual, carga_futura_reg, cbs_val, ibs_val, n = item
    custo, preco, carga_atual, carga_futura_reg, cbs_val, ibs_val = (
        _num(custo), _num(preco), _num(carga_atual), _num(carga_futura_reg), _num(cbs_val), _num(ibs_val))

    linhas = []
    # margem atual (antes dos tributos) e margem apos regime atual vs futuro
    if preco > 0:
        margem_atual_pct = (preco - custo) / preco * 100
        # simulacao: repassa a diferenca de carga ao preco final
        carga_futura_sim = carga_futura_reg if carga_futura_reg > 0 else (ALIQ_CBS_REF + ALIQ_IBS_REF)
        diferenca = carga_futura_sim - carga_atual
        preco_novo = preco * (1 + diferenca / 100) if diferenca > 0 else preco
        margem_futura_pct = (preco_novo - custo) / preco_novo * 100 if preco_novo > 0 else 0
        linhas.append("item medio: preco R$ %.2f, custo R$ %.2f, margem atual %.1f%%" %
                      (preco, custo, margem_atual_pct))
        linhas.append("carga tributaria atual (ICMS+PIS+COFINS): %.2f%% | futura registrada (CBS+IBS): %.2f%%" %
                      (carga_atual, carga_futura_reg))
        linhas.append("SIMULACAO: diferenca de carga %.2f p.p. -> impacto no preco %+.2f%% "
                      "(margem futura estimada %.1f%%)" %
                      (diferenca, (preco_novo - preco) / preco * 100, margem_futura_pct))
    if por_classe:
        linhas.append("Simulacao por CST/CSOSN (media):")
        for cst, icms, pis, cofins, icms_st, cbs, ibs_est, ibs_mun, cnt in por_classe[:8]:
            atual = _num(icms) + _num(pis) + _num(cofins)
            futuro = (_num(cbs) + _num(ibs_est) + _num(ibs_mun)) if (_num(cbs) + _num(ibs_est) + _num(ibs_mun)) > 0 \
                else (ALIQ_CBS_REF + ALIQ_IBS_REF)
            linhas.append("  CST %s: atual %.1f%% -> futuro %.1f%% (%+ .1f p.p., %d classes)" %
                          (cst, atual, futuro, futuro - atual, cnt))

    log("Analista simulacao: %d linhas" % len(linhas))
    for l in linhas:
        log("  " + l)
    gravar_na_memoria(linhas)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        log("ERRO analista_simulacao: %s\n%s" % (str(e)[:300], traceback.format_exc()))
