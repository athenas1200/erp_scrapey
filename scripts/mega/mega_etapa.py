# -*- coding: utf-8 -*-
"""UPLOAD ETAPA MEGA - sobe fotos locais da tabela concorrente para o MEGA em lotes.
Processa ate LIMITE fotos por execucao (evita problema de conexao/rate limit).
Grava o link publico em concorrente.foto_mega.
Uso: python mega_etapa.py [limite]
"""
import os, sys, io, json, time, subprocess, glob

LIMITE = int(sys.argv[1]) if len(sys.argv) > 1 else 10
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = BASE + r'\logs'
os.makedirs(LOGDIR, exist_ok=True)
CHECKPOINT = LOGDIR + r'\mega_etapa.json'
PROGLOG = LOGDIR + r'\mega_%s.log' % time.strftime('%Y%m%d_%H%M%S')
LOCKFILE = LOGDIR + r'\mega.lock'

def ja_rodando():
    if not os.path.exists(LOCKFILE):
        return False
    try:
        with io.open(LOCKFILE, encoding='utf-8') as f:
            pid = int(f.read().strip())
        if pid == os.getpid():
            return False
        out = subprocess.run(['tasklist', '/FI', 'PID eq %d' % pid],
                             capture_output=True, text=True, timeout=30)
        return str(pid) in out.stdout
    except Exception:
        return False

if ja_rodando():
    sys.exit(0)
with io.open(LOCKFILE, 'w', encoding='utf-8') as f:
    f.write(str(os.getpid()))

sys.path.insert(0, BASE)
import importlib.util
_spec = importlib.util.spec_from_file_location('sync_silencioso', BASE + r'\sync_silencioso.py')
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

def log(msg):
    line = "[%s] %s" % (time.strftime('%H:%M:%S'), msg)
    with io.open(PROGLOG, 'a', encoding='utf-8') as f:
        f.write(line + "\n")
    print(line)

def carregar_ck():
    if os.path.exists(CHECKPOINT):
        try:
            with io.open(CHECKPOINT, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'feitos': []}

def salvar_ck(ck):
    with io.open(CHECKPOINT, 'w', encoding='utf-8') as f:
        json.dump(ck, f, ensure_ascii=False)

def main():
    import psycopg2
    ck = carregar_ck()
    feitos = set(ck.get('feitos', []))

    tunnel, local = sync.open_tunnel()
    pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real",
                             user="postgres", password=sync.PG_PWD, connect_timeout=30)
    pconn.autocommit = True
    cur = pconn.cursor()
    cur.execute("""SELECT id, produto_codigo, concorrente, foto_local
        FROM concorrente
        WHERE foto_local IS NOT NULL AND foto_local <> ''
          AND (foto_mega IS NULL OR foto_mega = '')
        ORDER BY id""")
    pendentes = [r for r in cur.fetchall() if r[0] not in feitos]
    pconn.close(); tunnel.close(); local.close()
    log("Pendentes de upload MEGA: %d | limite: %d" % (len(pendentes), LIMITE))

    n_ok = 0
    for rid, codigo, conc, foto_local in pendentes[:LIMITE]:
        if not os.path.exists(foto_local):
            feitos.add(rid)
            ck['feitos'] = sorted(feitos)
            salvar_ck(ck)
            continue
        nome = '%s_%s.webp' % (codigo, conc or 'conc')
        r = subprocess.run([sys.executable, os.path.join(BASE, 'mega_upload_img.py'),
                            foto_local, nome], capture_output=True, text=True,
                           timeout=180, cwd=BASE)
        link = (r.stdout or '').strip()
        if link and link.startswith('http'):
            tunnel, local = sync.open_tunnel()
            pconn = psycopg2.connect(host="127.0.0.1", port=15434, dbname="s9_real",
                                     user="postgres", password=sync.PG_PWD, connect_timeout=30)
            pconn.autocommit = True
            cur = pconn.cursor()
            cur.execute("UPDATE concorrente SET foto_mega=%s WHERE id=%s", (link, rid))
            pconn.close(); tunnel.close(); local.close()
            n_ok += 1
            log("  OK id=%s %s -> %s" % (rid, codigo, link[:60]))
        else:
            log("  FALHA id=%s %s (%s)" % (rid, codigo, (r.stderr or '')[:80]))
        feitos.add(rid)
        ck['feitos'] = sorted(feitos)
        salvar_ck(ck)
        time.sleep(2)

    try:
        os.remove(LOCKFILE)
    except Exception:
        pass
    log("Etapa MEGA concluida: %d enviados de %d" % (n_ok, min(len(pendentes), LIMITE)))

if __name__ == '__main__':
    main()
