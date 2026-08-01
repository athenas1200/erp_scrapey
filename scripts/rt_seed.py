# -*- coding: utf-8 -*-
"""RT_SEED - popula o schema rt_* com parametros iniciais de referencia.
Estes sao APENAS valores iniciais armazenados EM BANCO - podem e devem ser
ajustados pela equipe/fiscal sem tocar no codigo. O motor nunca usa valores fixos.

Insere (idempotente):
  rt_base_calculo   - componentes padrao da base de calculo
  rt_aliquotas_cbs  - referencia de aliquotas CBS por regime (vigencias)
  rt_aliquotas_ibs  - referencia de aliquotas IBS (estadual+municipal)
  rt_regras_uf      - aliquotas IBS por UF (referencia)
  rt_regras_municipio - aliquotas IBS municipais (exemplos)
  rt_parametros     - parametros gerais
Uso: python rt_seed.py
"""
import sys, io, time

sys.path.insert(0, r'C:\S9\knowledge_base')
from db_mem import connect, close_all
from psycopg2.extras import execute_values

BASE_CALCULO = [
    ('CBS', 'valor_bruto', '+', 0),
    ('CBS', 'desconto', '-', 1),
    ('CBS', 'frete', '+', 2),
    ('CBS', 'seguro', '+', 3),
    ('CBS', 'despesa_acessoria', '+', 4),
    ('IBS', 'valor_bruto', '+', 0),
    ('IBS', 'desconto', '-', 1),
    ('IBS', 'frete', '+', 2),
    ('IBS', 'seguro', '+', 3),
    ('IBS', 'despesa_acessoria', '+', 4),
]

# Referencias de aliquotas (2026) - transicao reforma. NUNCA usadas se a tabela
# tiver configuracao especifica. Facil de atualizar via SQL.
ALIQ_CBS = [
    ('2026-01-01', 'lucro real', 'venda', '', 8.0000, 0, 0),
    ('2026-01-01', 'lucro presumido', 'venda', '', 8.0000, 0, 0),
    ('2026-01-01', 'simples', 'venda', '', 4.0000, 0, 0),
]

ALIQ_IBS = [
    ('2026-01-01', 'lucro real', 'venda', '', '', '', 15.5000, 0, 0),
    ('2026-01-01', 'simples', 'venda', '', '', '', 7.7500, 0, 0),
]

# IBS estadual por UF (referencia 8.5% + municipal 7% = 15.5%)
UFS = [('SP', 8.5000, 0), ('RJ', 8.5000, 0), ('MG', 8.5000, 0),
       ('RS', 8.5000, 0), ('PR', 8.5000, 0), ('SC', 8.5000, 0),
       ('BA', 8.5000, 0), ('PE', 8.5000, 0), ('CE', 8.5000, 0),
       ('GO', 8.5000, 0), ('DF', 8.5000, 0), ('AM', 8.5000, 0)]

MUNIS = [('SAO PAULO', 'SP', 7.0000, 0), ('RIO DE JANEIRO', 'RJ', 7.0000, 0),
         ('BELO HORIZONTE', 'MG', 7.0000, 0), ('CURITIBA', 'PR', 7.0000, 0),
         ('PORTO ALEGRE', 'RS', 7.0000, 0)]

PARAMS = [
    ('cbs_base_desconto', 'nao', 'Desconto incondicional entra na base da CBS'),
    ('cbs_base_frete', 'sim', 'Frete entra na base da CBS'),
    ('cbs_base_seguro', 'sim', 'Seguro entra na base da CBS'),
    ('ibs_base_desconto', 'nao', 'Desconto incondicional entra na base do IBS'),
    ('ibs_base_frete', 'sim', 'Frete entra na base do IBS'),
    ('ibs_base_seguro', 'sim', 'Seguro entra na base do IBS'),
    ('split_payment_ativo', 'nao', 'Split payment em uso (preparacao)'),
]


def main():
    tr, lr, cr, cur = connect()
    try:
        hoje = time.strftime('%Y-%m-%d')
        # base de calculo
        cur.execute("SELECT COUNT(*) FROM rt_base_calculo")
        if cur.fetchone()[0] == 0:
            execute_values(cur, """INSERT INTO rt_base_calculo
                (tributo, componente, sinal, ordem) VALUES %s""",
                           BASE_CALCULO)
        # aliquotas CBS
        cur.execute("SELECT COUNT(*) FROM rt_aliquotas_cbs")
        if cur.fetchone()[0] == 0:
            execute_values(cur, """INSERT INTO rt_aliquotas_cbs
                (vigencia_inicio, regime, tipo_operacao, natureza_receita,
                 aliquota, reduzir_base, credito_percentual) VALUES %s""",
                           [(a[0], a[1], a[2], a[3], a[4], a[5], a[6]) for a in ALIQ_CBS])
        # aliquotas IBS
        cur.execute("SELECT COUNT(*) FROM rt_aliquotas_ibs")
        if cur.fetchone()[0] == 0:
            execute_values(cur, """INSERT INTO rt_aliquotas_ibs
                (vigencia_inicio, regime, tipo_operacao, uf_origem, uf_destino, municipio,
                 aliquota, reduzir_base, credito_percentual) VALUES %s""",
                           [(a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8]) for a in ALIQ_IBS])
        # regras UF
        cur.execute("SELECT COUNT(*) FROM rt_regras_uf")
        if cur.fetchone()[0] == 0:
            execute_values(cur, """INSERT INTO rt_regras_uf
                (uf, aliquota_ibs_estadual, credito_percentual, vigencia_inicio) VALUES %s""",
                           [(u[0], u[1], u[2], hoje) for u in UFS])
        # regras municipio
        cur.execute("SELECT COUNT(*) FROM rt_regras_municipio")
        if cur.fetchone()[0] == 0:
            execute_values(cur, """INSERT INTO rt_regras_municipio
                (municipio, uf, aliquota_ibs_municipal, credito_percentual, vigencia_inicio) VALUES %s""",
                           [(m[0], m[1], m[2], m[3], hoje) for m in MUNIS])
        # parametros
        cur.execute("SELECT COUNT(*) FROM rt_parametros")
        if cur.fetchone()[0] == 0:
            execute_values(cur, """INSERT INTO rt_parametros
                (chave, valor, descricao, atualizado_em) VALUES %s""",
                           [(p[0], p[1], p[2], time.strftime('%Y-%m-%d %H:%M:%S')) for p in PARAMS])
        cr.commit()
        for t in ['rt_base_calculo', 'rt_aliquotas_cbs', 'rt_aliquotas_ibs',
                  'rt_regras_uf', 'rt_regras_municipio', 'rt_parametros']:
            cur.execute('SELECT COUNT(*) FROM %s' % t)
            print("%s: %d registros" % (t, cur.fetchone()[0]))
    finally:
        close_all(tr, lr, cr)


if __name__ == '__main__':
    main()
