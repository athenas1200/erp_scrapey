# -*- coding: utf-8 -*-
"""EMAIL DAS 9H - resumo diario de movimentacao + metricas aplicadas.
1) Tabelas que tiveram movimento hoje (Data_Alteracao >= hoje) com a
   quantidade de registros movimentados (ex.: Movimento 450, Estoque 285).
2) Abaixo, relatorio das metricas/aplicacoes feitas hoje (metricas_diario.json).
Uso: python email_9h.py            (manual)
     python email_9h.py --auto      (agendado 09:00, loga sem print)
"""
import os, sys, io, json, time, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

AUTO = '--auto' in sys.argv
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = BASE + r'\logs'
os.makedirs(LOGDIR, exist_ok=True)
CONFIG = BASE + r'\email_config.json'
LOGFILE = LOGDIR + r'\email_9h.log'
METRICAS_FILE = LOGDIR + r'\metricas_diario.json'

sys.path.insert(0, BASE)
import importlib.util
_spec = importlib.util.spec_from_file_location('sync_silencioso', BASE + r'\sync_silencioso.py')
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

NOMES_AMIGAVES = {
    "Cli_For": "cadastro de clientes/fornecedores",
    "Prod_Serv": "cadastro de produtos/servicos",
    "Funcionarios": "cadastro de funcionarios",
    "Configuracoes": "configuracoes",
    "Configuracoes_Filtros": "filtros de configuracao",
    "Prod_Serv_Promocao": "promocoes de produtos",
    "Carga_Tributaria_Estados": "carga tributaria por estado",
    "Estoque_Atual": "estoque",
    "Estoque_Atual_Lote": "estoque por lote",
    "Financeiro_Caixa": "caixa",
    "Financeiro_Contas": "contas financeiras",
    "Movimento_Prod_Serv": "movimento de produtos/servicos",
    "Movimento": "movimento",
    "Movimento_Entrega": "entregas",
    "Movimento_Documentos_Fiscais": "documentos fiscais",
    "Movimento_Estoque_Efetivado": "estoque efetivado",
    "Financeiro_Formas_Pagamento_V2": "formas de pagamento",
    "Contas_V2": "contas",
    "Funcionarios_Conf": "configuracoes de funcionarios",
    "concorrente": "precos concorrentes",
    "Movimento_Origem": "movimento origem",
    "Movimento_Prod_Serv_Lote": "movimento por lote",
}


def log(msg):
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    with io.open(LOGFILE, 'a', encoding='utf-8') as f:
        f.write(line + "\n")
    if not AUTO:
        print(line)


def load_config():
    if os.path.exists(CONFIG):
        return json.load(io.open(CONFIG, encoding='utf-8'))
    return {
        "smtp_host": "mail.consultoriasoft.com.br",
        "smtp_port": 26,
        "remetente": os.environ.get("EMAIL_REMETENTE", ""),
        "senha_app": os.environ.get("EMAIL_SENHA_APP", ""),
        "destinatario": os.environ.get("EMAIL_DESTINATARIO", ""),
    }


def nome_amigavel(t):
    for k, v in NOMES_AMIGAVES.items():
        if t == k or t.startswith(k):
            return v
    return "tabela " + t


def coletar_movimentacao_hoje():
    """Para cada tabela com Data_Alteracao: quantos registros mudaram hoje."""
    import pyodbc
    sconn = pyodbc.connect(sync.SQL_DSN, timeout=60)
    scur = sconn.cursor()
    hoje0 = time.strftime('%Y-%m-%d') + ' 00:00:00'
    res = []
    for t in sync.tabelas:
        try:
            r = scur.execute("""SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME=? AND COLUMN_NAME='Data_Alteracao'""", t).fetchone()
            if r[0] == 0:
                continue
            scur.execute("""SELECT COUNT(*) FROM dbo.[%s]
                WHERE Data_Alteracao >= ?""" % t, (hoje0,))
            qtd = scur.fetchone()[0]
            if qtd > 0:
                res.append((nome_amigavel(t), int(qtd)))
        except Exception:
            continue
    sconn.close()
    res.sort(key=lambda x: -x[1])
    return res


def ler_metricas_hoje():
    hoje = time.strftime('%Y-%m-%d')
    try:
        d = json.load(io.open(METRICAS_FILE, encoding='utf-8'))
        items = d.get(hoje, [])
        return items if isinstance(items, list) else []
    except Exception:
        return []


def montar_html(mov, metricas, hoje):
    sec_mov = ""
    if mov:
        rows = "".join("<tr><td>%s</td><td align='center'><b>%d</b></td></tr>" % (t, q) for t, q in mov[:40])
        total = sum(q for _, q in mov)
        rows += "<tr style='background:#e0f2fe;font-weight:bold'><td>TOTAL</td><td align='center'>%d</td></tr>" % total
        sec_mov = """<h3 style="color:#0ea5e9;margin:0">Tabelas com movimentacao hoje</h3>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;border-color:#cbd5e1;font-size:14px">
          <tr style="background:#f1f5f9"><th>Tabela</th><th>Registros</th></tr>
          %s
        </table>""" % rows
    else:
        sec_mov = "<h3 style='color:#0ea5e9'>Tabelas com movimentacao hoje</h3><p>Nenhum registro movimentado hoje.</p>"

    sec_met = ""
    if metricas:
        items = "".join("<li>%s</li>" % m for m in metricas)
        sec_met = """<h3 style="color:#0ea5e9;margin:0">Metricas aplicadas hoje</h3>
        <ul style="font-size:14px">%s</ul>""" % items
    else:
        sec_met = "<h3 style='color:#0ea5e9'>Metricas aplicadas hoje</h3><p>Nenhuma metrica registrada.</p>"

    return """<html><body style="font-family:Segoe UI,Arial;color:#1e293b">
    <h2 style="color:#0ea5e9">Resumo diario S9 - %s</h2>
    %s
    <br><br>
    %s
    <p style="color:#64748b;font-size:12px">Enviado automaticamente as 09:00.</p>
    </body></html>""" % (hoje, sec_mov, sec_met)


def montar_texto(mov, metricas, hoje):
    txt = ["Resumo diario S9 - %s" % hoje, ""]
    txt.append("TABELAS COM MOVIMENTACAO HOJE:")
    if mov:
        for t, q in mov[:40]:
            txt.append("  %s %d" % (t, q))
        txt.append("  TOTAL %d" % sum(q for _, q in mov))
    else:
        txt.append("  nenhum registro movimentado hoje")
    txt.append("")
    txt.append("METRICAS APLICADAS HOJE:")
    if metricas:
        for m in metricas:
            txt.append("  - %s" % m)
    else:
        txt.append("  nenhuma metrica registrada")
    return "\n".join(txt)


def enviar(cfg, html, texto, hoje):
    msg = MIMEMultipart('alternative')
    msg['From'] = formataddr(('Resumo S9', cfg['remetente']))
    msg['To'] = cfg['destinatario']
    msg['Subject'] = 'Resumo S9 - %s' % hoje
    msg.attach(MIMEText(texto, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    s = smtplib.SMTP(cfg['smtp_host'], int(cfg['smtp_port']), timeout=60)
    s.starttls()
    s.login(cfg['remetente'], cfg['senha_app'])
    s.sendmail(cfg['remetente'], [cfg['destinatario']], msg.as_string())
    s.quit()


def main():
    hoje = time.strftime('%Y-%m-%d')
    cfg = load_config()
    if not cfg.get('remetente') or not cfg.get('senha_app'):
        log("Configuracao de email incompleta")
        return 1
    mov = coletar_movimentacao_hoje()
    metricas = ler_metricas_hoje()
    html = montar_html(mov, metricas, hoje)
    texto = montar_texto(mov, metricas, hoje)
    enviar(cfg, html, texto, hoje)
    log("Email 9h enviado - tabelas=%d metricas=%d" % (len(mov), len(metricas)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
