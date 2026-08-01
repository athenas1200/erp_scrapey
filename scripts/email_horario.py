# -*- coding: utf-8 -*-
"""EMAIL HORARIO - relatorio a cada 1h com o que o scraper fez (fotos e precos),
listando apenas o EAN dos produtos processados desde o ultimo envio.
Uso: python email_horario.py            (disparo manual)
     python email_horario.py --auto      (chamado pelo vigia, loga sem print)
O horario do ultimo envio fica em logs/email_horario_ck.json para nunca
perder dados entre execucoes (mesmo se a maquina desligar).
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
LOGFILE = LOGDIR + r'\email_horario.log'
CKFILE = LOGDIR + r'\email_horario_ck.json'

sys.path.insert(0, BASE)
import importlib.util
_spec = importlib.util.spec_from_file_location('sync_silencioso', BASE + r'\sync_silencioso.py')
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

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

def ler_ultimo():
    """Retorna o timestamp do ultimo envio (epoch) ou inicio de hoje."""
    hoje0 = time.mktime(time.strptime(time.strftime('%Y-%m-%d'), '%Y-%m-%d'))
    if os.path.exists(CKFILE):
        try:
            d = json.load(io.open(CKFILE, encoding='utf-8'))
            ts = d.get('ultimo_ts')
            if ts:
                return float(ts)
        except Exception:
            pass
    return hoje0

def salvar_ultimo(ts):
    with io.open(CKFILE, 'w', encoding='utf-8') as f:
        json.dump({'ultimo_ts': ts, 'hora': time.strftime('%H:%M:%S')}, f, ensure_ascii=False)

def coletar_resumo(ts):
    """Resumo por concorrente dos produtos extraidos desde ts."""
    import psycopg2
    tunnel, local = sync.open_tunnel()
    pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real",
                             user="postgres", password=sync.PG_PWD, connect_timeout=30)
    cur = pconn.cursor()
    cur.execute("""SELECT concorrente,
        COUNT(DISTINCT produto_codigo) AS produtos,
        COUNT(*) AS registros
        FROM concorrente
        WHERE data_coleta >= to_timestamp(%s)
          AND ean IS NOT NULL AND ean <> ''
        GROUP BY concorrente ORDER BY produtos DESC""", (ts,))
    precos = cur.fetchall()
    fotos = []
    por_produto = []
    pconn.close(); tunnel.close(); local.close()
    return precos, fotos, por_produto

def coletar_alteracoes_tabelas():
    """Tabelas sincronizadas com Data_Alteracao mais recente que o PG
    (modificacoes de dados, igual ao relatorio diario)."""
    import pyodbc, psycopg2
    sconn = pyodbc.connect(sync.SQL_DSN, timeout=30)
    scur = sconn.cursor()
    tunnel, local = sync.open_tunnel()
    pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real",
                             user="postgres", password=sync.PG_PWD, connect_timeout=30)
    pcur = pconn.cursor()
    linhas = []
    for t in sync.tabelas:
        r = scur.execute("""SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME=? AND COLUMN_NAME='Data_Alteracao'""", t).fetchone()
        if r[0] == 0:
            continue
        pk = scur.execute("""SELECT c.COLUMN_NAME FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE c
            JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc ON c.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
            WHERE c.TABLE_NAME=? AND tc.CONSTRAINT_TYPE='PRIMARY KEY'""", t).fetchall()
        pkcol = pk[0][0] if pk else None
        if not pkcol:
            continue
        try:
            pcur.execute('SELECT max("Data_Alteracao") FROM "%s"' % t)
            pg_max = pcur.fetchone()[0]
            scur.execute("""SELECT COUNT(*) FROM dbo.[%s]
                WHERE Data_Alteracao > ?""" % t, (pg_max,))
            qtd = scur.fetchone()[0]
            if qtd > 0:
                linhas.append((t, qtd, str(pg_max)))
        except Exception:
            continue
    pconn.close(); sconn.close(); tunnel.close(); local.close()
    linhas.sort(key=lambda x: -x[1])
    return linhas

NOMES_AMIGAVES = {
    "Cli_For": "cadastro de clientes/fornecedores",
    "Prod_Serv": "cadastro de produtos/servicos",
    "Funcionarios": "cadastro de funcionarios",
    "Configuracoes": "configuracoes",
    "Configuracoes_Filtros": "filtros de configuracao",
    "Prod_Serv_Promocao": "promocoes de produtos",
    "Carga_Tributaria_Estados": "carga tributaria por estado",
    "Estoque_Atual": "estoque atual",
    "Estoque_Atual_Lote": "estoque por lote",
    "Financeiro_Caixa": "movimento de caixa",
    "Financeiro_Contas": "contas financeiras",
    "Movimento_Prod_Serv": "movimento de produtos/servicos",
    "Movimento": "movimentos",
    "Financeiro_Formas_Pagamento_V2": "formas de pagamento",
    "Contas_V2": "contas",
    "Funcionarios_Conf": "configuracoes de funcionarios",
}

def nome_amigavel(t):
    for k, v in NOMES_AMIGAVES.items():
        if t == k or t.startswith(k):
            return v
    return "tabela " + t

def moeda(v):
    if v is None:
        return '-'
    return 'R$ %.2f' % float(v)

def montar_html(precos, fotos, por_produto, alteracoes, desde):
    sec = ""
    if not precos and not fotos and not por_produto:
        sec = "<p>Nenhum produto processado neste periodo.</p>"
    if precos:
        rows = ""
        total_prod = 0
        for conc, produtos, registros in precos:
            total_prod += produtos
            rows += ("<tr><td>%s</td><td align='center'>%d</td></tr>") % (conc, produtos)
        rows += ("<tr style='background:#e0f2fe;font-weight:bold'>"
                 "<td>TOTAL</td><td align='center'>%d</td></tr>") % total_prod
        sec += """<h3 style="color:#0ea5e9;margin-bottom:4px">Produtos extraidos por concorrente</h3>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;border-color:#cbd5e1;font-size:14px">
          <tr style="background:#f1f5f9"><th>Concorrente</th><th>Produtos extraidos</th></tr>
          %s
        </table>""" % rows
    if alteracoes:
        rows = ""
        for t, qtd, pg_max in alteracoes:
            data = (pg_max or "")[:10]
            rows += ("<tr><td>%s</td><td align='center'>%d itens</td>"
                     "<td align='center'>%s</td></tr>") % (nome_amigavel(t), qtd, data)
        sec += """<h3 style="color:#0ea5e9;margin-top:16px;margin-bottom:4px">Modificacoes de dados</h3>
        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse;border-color:#cbd5e1;font-size:13px">
          <tr style="background:#f1f5f9"><th>Cadastro</th><th>Alterado</th><th>Data</th></tr>
          %s
        </table>""" % rows
    return ("""<html><body style="font-family:Segoe UI,Arial;color:#1e293b">
    <h2 style="color:#0ea5e9">Relatorio horario - Scraper de fotos e precos</h2>
    <p>Periodo: <b>desde %s</b> (relatorio automatico a cada 1h)</p>
    %s
    <p style="color:#64748b;font-size:12px">Gerado pelo monitor de precos.</p>
    </body></html>""") % (time.strftime('%H:%M', time.localtime(desde)), sec)

def montar_texto(precos, fotos, por_produto, alteracoes, desde):
    txt = ["Relatorio horario - Scraper de fotos e precos",
           "Periodo: desde %s" % time.strftime('%H:%M', time.localtime(desde)), ""]
    if precos:
        txt.append("PRODUTOS EXTRAIDOS POR CONCORRENTE:")
        total_prod = 0
        for conc, produtos, registros in precos:
            total_prod += produtos
            txt.append("  %s extraiu %d produtos" % (conc, produtos))
        txt.append("  TOTAL: %d produtos extraidos" % total_prod)
    else:
        txt.append("Nenhum produto extraido no periodo.")
    txt.append("")
    if alteracoes:
        txt.append("MODIFICACOES DE DADOS:")
        for t, qtd, pg_max in alteracoes:
            data = (pg_max or "")[:10]
            txt.append("  %s - %d itens - data %s" % (nome_amigavel(t), qtd, data))
    else:
        txt.append("MODIFICACOES DE DADOS: nenhuma.")
    return "\n".join(txt)

def enviar(cfg, html, texto):
    msg = MIMEMultipart('alternative')
    msg['From'] = formataddr(('Monitor de Precos', cfg['remetente']))
    msg['To'] = cfg['destinatario']
    msg['Subject'] = 'Relatorio horario scraper - %s' % time.strftime('%d/%m %H:%M')
    msg.attach(MIMEText(texto, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    s = smtplib.SMTP(cfg['smtp_host'], int(cfg['smtp_port']), timeout=60)
    s.starttls()
    s.login(cfg['remetente'], cfg['senha_app'])
    s.sendmail(cfg['remetente'], [cfg['destinatario']], msg.as_string())
    s.quit()

def main():
    cfg = load_config()
    if not cfg.get('remetente') or not cfg.get('senha_app'):
        log("Configuracao de email incompleta")
        return 1
    desde = ler_ultimo()
    agora = time.time()
    precos, fotos, por_produto = coletar_resumo(desde)
    alteracoes = coletar_alteracoes_tabelas()
    # sempre envia no horario, mesmo sem dados, para confirmar que esta vivo
    html = montar_html(precos, fotos, por_produto, alteracoes, desde)
    texto = montar_texto(precos, fotos, por_produto, alteracoes, desde)
    enviar(cfg, html, texto)
    salvar_ultimo(agora)
    log("Email horario enviado - concorrentes=%d fotos=%d produtos=%d tabelas=%d (desde %s)" %
        (len(precos), len(fotos), len(por_produto), len(alteracoes),
         time.strftime('%H:%M', time.localtime(desde))))
    return 0

if __name__ == '__main__':
    sys.exit(main())
