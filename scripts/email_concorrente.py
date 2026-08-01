# -*- coding: utf-8 -*-
"""EMAIL PRECOS - envia por e-mail o que foi coletado hoje na tabela concorrente.
Uso: python email_concorrente.py            (disparo manual/sob demanda)
     python email_concorrente.py --auto      (chamado pelo agendador, loga sem print)
Cada envio e registrado em logs/email_concorrente.log para nunca enviar 2x no dia.
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
LOGFILE = LOGDIR + r'\email_concorrente.log'
STATUS = LOGDIR + r'\email_concorrente_status.json'

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

def ja_enviou_hoje():
    hoje = time.strftime('%Y-%m-%d')
    if os.path.exists(STATUS):
        try:
            d = json.load(io.open(STATUS, encoding='utf-8'))
            return d.get('data') == hoje
        except Exception:
            return False
    return False

def marcar_enviado():
    with io.open(STATUS, 'w', encoding='utf-8') as f:
        json.dump({'data': time.strftime('%Y-%m-%d'),
                   'hora': time.strftime('%H:%M:%S')}, f, ensure_ascii=False)

def coletar_hoje():
    import psycopg2
    tunnel, local = sync.open_tunnel()
    pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real",
                             user="postgres", password=sync.PG_PWD, connect_timeout=30)
    cur = pconn.cursor()
    cur.execute("""SELECT concorrente,
        COUNT(DISTINCT produto_codigo) AS produtos,
        COUNT(*) AS registros
        FROM concorrente WHERE data_preco=CURRENT_DATE
        GROUP BY concorrente ORDER BY produtos DESC""")
    rows = cur.fetchall()
    pconn.close(); tunnel.close(); local.close()
    return rows

def moeda(v):
    if v is None:
        return '-'
    return 'R$ %.2f' % float(v)

def montar_html(rows, hoje):
    if not rows:
        return ("<html><body style='font-family:Segoe UI,Arial;color:#1e293b'>"
                "<h2 style='color:#0ea5e9'>Coleta de precos - %s</h2>"
                "<p>Nenhum preco coletado hoje ainda.</p></body></html>" % hoje)
    rows_html = ""
    total_prod = 0
    for conc, produtos, registros in rows:
        total_prod += produtos
        rows_html += ("<tr><td>%s</td><td align='center'>%d</td></tr>") % (conc, produtos)
    rows_html += ("<tr style='background:#e0f2fe;font-weight:bold'>"
                  "<td>TOTAL</td><td align='center'>%d</td></tr>") % total_prod
    return ("""<html><body style="font-family:Segoe UI,Arial;color:#1e293b">
    <h2 style="color:#0ea5e9">Coleta de precos concorrentes - %s</h2>
    <p>Produtos extraidos hoje (tabela concorrente): <b>%d</b>.</p>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;border-color:#cbd5e1;font-size:14px">
      <tr style="background:#f1f5f9"><th>Concorrente</th><th>Produtos extraidos</th></tr>
      %s
    </table>
    <p style="color:#64748b;font-size:12px">Gerado automaticamente pelo monitor de precos.</p>
    </body></html>""") % (hoje, total_prod, rows_html)

def montar_texto(rows, hoje):
    txt = ["Coleta de precos concorrentes - %s" % hoje, ""]
    if not rows:
        txt.append("Nenhum preco coletado hoje.")
        return "\n".join(txt)
    txt.append("PRODUTOS EXTRAIDOS POR CONCORRENTE:")
    total_prod = 0
    for conc, produtos, registros in rows:
        total_prod += produtos
        txt.append("  %s extraiu %d produtos" % (conc, produtos))
    txt.append("  TOTAL: %d produtos extraidos" % total_prod)
    return "\n".join(txt)

def enviar(cfg, html, texto, hoje):
    msg = MIMEMultipart('alternative')
    msg['From'] = formataddr(('Monitor de Precos', cfg['remetente']))
    msg['To'] = cfg['destinatario']
    msg['Subject'] = 'Precos concorrentes - %s' % hoje
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
        log("Configuracao de email incompleta: preencha email_config.json")
        return 1
    if AUTO and ja_enviou_hoje():
        log("Ja enviado hoje - email NAO reenviado")
        return 0
    rows = coletar_hoje()
    html = montar_html(rows, hoje)
    texto = montar_texto(rows, hoje)
    enviar(cfg, html, texto, hoje)
    marcar_enviado()
    log("Email enviado para %s - %d registros de hoje" % (cfg['destinatario'], len(rows)))
    return 0

if __name__ == '__main__':
    sys.exit(main())
