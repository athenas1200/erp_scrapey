# -*- coding: utf-8 -*-
"""ANALISTA ABC/XYZ - classificacao de produtos por faturamento e variabilidade.
  Curva ABC: A (80% do faturamento), B (15%), C (5%)
  Curva XYZ: X (venda regular), Y (variavel), Z (irregular/imprevisivel)
Grava em memory_business_rules + memory_products + metricas_diario.json.
Modo leitura apenas. Uso: python analista_abc.py [dias]
"""
import sys, io, os, json, time

sys.path.insert(0, r'C:\S9\knowledge_base')
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOG = os.path.join(LOGDIR, 'analista_abc.log')

DIAS = 90
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
    log("ANALISTA ABC/XYZ iniciado (DIAS=%d)" % DIAS)
    hoje = time.strftime('%Y-%m-%d')

    tr, lr, cr, cur = connect()
    try:
        # faturamento por produto na janela
        cur.execute("""SELECT COALESCE("Ordem_Prod_Serv",0)::text, COUNT(*),
            COALESCE(SUM("Preco_Total_Com_Desconto"),0),
            COALESCE(SUM("Preco_Total_Sem_Desconto"),0)
            FROM "Movimento_Prod_Serv"
            WHERE "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)
            GROUP BY "Ordem_Prod_Serv" ORDER BY 3 DESC""", (DIAS,))
        produtos = cur.fetchall()
        # vendas por produto por semana (para XYZ)
        cur.execute("""SELECT COALESCE("Ordem_Prod_Serv",0)::text,
            date_trunc('week', "Data_Efetivacao_Estoque")::date, COUNT(*)
            FROM "Movimento_Prod_Serv"
            WHERE "Data_Efetivacao_Estoque" >= CURRENT_TIMESTAMP - make_interval(days => %s)
            GROUP BY 1, 2""", (DIAS,))
        semanal = {}
        for prod, semana, n in cur.fetchall():
            semanal.setdefault(prod, []).append(n)
    finally:
        close_all(tr, lr, cr)

    total = sum(_num(p[2]) for p in produtos) or 1
    acum = 0.0
    classif = []
    for prod, vendas, valor, bruto in produtos:
        acum += _num(valor)
        pct = acum / total * 100
        cls = 'A' if pct <= 80 else ('B' if pct <= 95 else 'C')
        # XYZ: coeficiente de variacao das vendas semanais
        vals = semanal.get(prod, [])
        if vals:
            media = sum(vals) / len(vals)
            var = sum((v - media) ** 2 for v in vals) / len(vals)
            cv = (var ** 0.5) / media if media > 0 else 99
        else:
            cv = 99
        xyz = 'X' if cv < 0.5 else ('Y' if cv < 1.0 else 'Z')
        classif.append((prod, int(vendas), _num(valor), cls, xyz, round(cv, 2)))

    nA = sum(1 for c in classif if c[3] == 'A')
    nB = sum(1 for c in classif if c[3] == 'B')
    nC = sum(1 for c in classif if c[3] == 'C')
    nX = sum(1 for c in classif if c[4] == 'X')
    nY = sum(1 for c in classif if c[4] == 'Y')
    nZ = sum(1 for c in classif if c[4] == 'Z')

    linhas = []
    linhas.append("curva ABC (%d dias): A=%d B=%d C=%d de %d produtos" %
                  (DIAS, nA, nB, nC, len(classif)))
    linhas.append("curva XYZ: X=%d Y=%d Z=%d (regularidade de venda)" % (nX, nY, nZ))
    if classif:
        top_a = classif[:5]
        linhas.append("Top A: " + "; ".join("prod %s R$ %.0f (%d vendas, %s%s)" %
                     (c[0], c[2], c[1], c[3], c[4]) for c in top_a))
        z = [c for c in classif if c[4] == 'Z'][:5]
        if z:
            linhas.append("Irregulares (Z): " + "; ".join("prod %s CV=%.2f" % (c[0], c[5]) for c in z))

    # grava em memory_products (valores numericos + classificacao ABC/XYZ) + memory_business_rules
    tm, lm, cm, mcur = mconnect()
    try:
        rows = []
        for c in classif[:200]:
            prod, vendas, valor, cls, xyz, cv = c
            rows.append((prod, None, valor, cv, None, None, None, vendas, hoje, cls, xyz))
        if rows:
            execute_values(mcur, """INSERT INTO memory_products
                (produto_codigo, nome, giro, margem, custo_medio, preco_medio, preco_ideal,
                 tempo_reposicao, data, abc, xyz)
                VALUES %s ON CONFLICT (produto_codigo) DO UPDATE SET
                 giro=EXCLUDED.giro, margem=EXCLUDED.margem, tempo_reposicao=EXCLUDED.tempo_reposicao,
                 data=EXCLUDED.data, abc=EXCLUDED.abc, xyz=EXCLUDED.xyz""", rows)
        # tabela analise_abc_xyz (consumida pelo extrator do motor de compras)
        rows_abc = [(str(c[0]), c[3], c[4], c[5], hoje) for c in classif[:500]]
        if rows_abc:
            execute_values(mcur, """INSERT INTO analise_abc_xyz
                (produto_id, curva_abc, curva_xyz, coeficiente_variacao, data_referencia)
                VALUES %s ON CONFLICT (produto_id, data_referencia) DO UPDATE SET
                 curva_abc=EXCLUDED.curva_abc, curva_xyz=EXCLUDED.curva_xyz,
                 coeficiente_variacao=EXCLUDED.coeficiente_variacao""", rows_abc)
        execute_values(mcur, """INSERT INTO memory_business_rules
            (tabela, regra, significado, confianca, descoberto_em) VALUES %s ON CONFLICT DO NOTHING""",
                       [('Prod_Serv', l, 'curva ABC/XYZ', 0.8, hoje) for l in linhas])
        cm.commit()
    finally:
        mclose(tm, lm, cm)

    mpath = os.path.join(LOGDIR, 'metricas_diario.json')
    try:
        d = json.load(io.open(mpath, encoding='utf-8'))
    except Exception:
        d = {}
    itens = d.get(hoje, [])
    itens = [x for x in itens if not x.startswith("Analista ABC")]
    itens.extend(["Analista ABC/XYZ: " + l for l in linhas])
    d[hoje] = itens
    io.open(mpath, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=1))

    log("Analista ABC/XYZ: %d linhas" % len(linhas))
    for l in linhas:
        log("  " + l)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        log("ERRO analista_abc: %s\n%s" % (str(e)[:300], traceback.format_exc()))
