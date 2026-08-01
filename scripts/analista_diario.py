# -*- coding: utf-8 -*-
"""ANALISTA DIARIO - relatorio consolidado do Analista Tributario e Comercial.
Executa todos os modulos do analista e gera:
  1. Relatorio completo em C:\S9\logs\relatorio_analista_YYYY-MM-DD.md
  2. Versao resumida gravada no metricas_diario.json (vai no email das 9h)
  3. Conhecimento gravado nas tabelas memory_* (memoria de longo prazo)
Ordem: metricas_fiscais -> analista_comercial -> analista_tributario -> analista_auditoria.
Uso: python analista_diario.py            (executa todos os modulos)
     python analista_diario.py --reporte  (so gera relatorio do que ja existe)
"""
import sys, io, os, json, time, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)
PY = r'C:\Users\Pe de Apoio\AppData\Local\Python\pythoncore-3.14-64\python.exe'
LOG = os.path.join(LOGDIR, 'analista_diario.log')

MODULOS = [
    ('metricas_fiscais.py', '30'),
    ('analista_comercial.py', '30'),
    ('analista_tributario.py', '30'),
    ('analista_auditoria.py', '30'),
]


def log(msg):
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    with io.open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + "\n")
    print(line)


def rodar_modulo(nome, args=''):
    try:
        cmd = [PY, os.path.join(BASE, nome)] + (args.split() if args else [])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                           cwd=BASE, creationflags=0x08000000)
        return r.returncode == 0, (r.stdout or '') + (r.stderr or '')
    except Exception as e:
        return False, str(e)[:300]


def ler_metricas_hoje():
    try:
        d = json.load(io.open(os.path.join(LOGDIR, 'metricas_diario.json'), encoding='utf-8'))
        return d.get(time.strftime('%Y-%m-%d'), [])
    except Exception:
        return []


def gravar_relatorio(hoje, metricas):
    md = ["# Relatorio do Analista S9 - %s" % hoje, "",
          "## Analise tributaria e comercial", "",
          "Gerado automaticamente pelos modulos do Analista. ",
          "Confianca: Alta para agregados, Media para inferencias de regra. "
          "Conferir com contador/fiscal.", "", "---", ""]
    for m in metricas:
        md.append("- %s" % m)
    md.append("")
    md.append("## Origem")
    md.append("Dados da replica s9_real (PostgreSQL, VPS) - tabelas: "
              "Movimento_Prod_Serv, Movimento, Cli_For, Prod_Serv, Classe_Imposto_Operacao.")
    md.append("Conhecimento persistido em memory_business_rules, memory_fiscal, memory_products, "
              "memory_customers (schema memory_*).")
    path = os.path.join(LOGDIR, 'relatorio_analista_%s.md' % hoje)
    io.open(path, 'w', encoding='utf-8').write("\n".join(md))
    return path


def main():
    hoje = time.strftime('%Y-%m-%d')
    log("ANALISTA DIARIO iniciado (%s)" % hoje)
    if '--reporte' not in sys.argv:
        for nome, args in MODULOS:
            ok, saida = rodar_modulo(nome, args)
            log("  %s: %s" % (nome, 'OK' if ok else 'FALHA'))
            if not ok:
                log("    " + saida[-300:])
    metricas = ler_metricas_hoje()
    path = gravar_relatorio(hoje, metricas)
    log("Relatorio: %s (%d metricas)" % (path, len(metricas)))
    print(path)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        log("ERRO analista_diario: %s\n%s" % (str(e)[:300], traceback.format_exc()))
