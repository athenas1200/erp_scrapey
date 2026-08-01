# -*- coding: utf-8 -*-
"""AUTOPUSH - envia automaticamente as alteracoes do projeto para o GitHub.
Uso: pythonw autopush.py  (chamado pelo vigia periodicamente)
Faz: git add -A, commit (se houver mudancas) e push. Totalmente silencioso.
"""
import os, sys, io, time, subprocess

BASE = r'C:\Users\Pe de Apoio\Documents\Default Project\erp_scrapey'
LOGDIR = r'C:\S9\logs'
os.makedirs(LOGDIR, exist_ok=True)
LOG = LOGDIR + r'\autopush.log'
GIT = 'git'
REPO = 'origin'
TOKEN = os.environ.get('GH_TOKEN', '')

def log(msg):
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    with io.open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + "\n")

def sh(args, timeout=120):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           cwd=BASE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    except Exception as e:
        return -1, str(e)

def main():
    # sincroniza os scripts de producao do C:\S9 para o repositorio
    for nome in ['sync_silencioso.py', 'tela_log_server.py', 'coletor_lote.py',
                 'coletor_precos.py', 'firecrawl_batch.py', 'mega_etapa.py',
                 'mega_upload_img.py', 'fotos_webp.py', 'email_diario.py',
                 'email_concorrente.py', 'email_horario.py', 'vigia.py']:
        src = r'C:\S9\%s' % nome
        dst = r'%s\scripts' % BASE
        try:
            import shutil
            shutil.copy2(src, os.path.join(dst, nome))
        except Exception:
            pass
    # copia docs e memoria
    try:
        import shutil
        shutil.copy2(r'C:\S9\AGENTS.md', os.path.join(BASE, 'AGENTS.md'))
        shutil.copy2(r'C:\S9\README.md', os.path.join(BASE, 'README.md'))
    except Exception:
        pass

    rc, out = sh([GIT, 'status', '--porcelain'])
    if rc != 0 or not out.strip():
        return 0
    sh([GIT, 'add', '-A'])
    rc, out = sh([GIT, '-c', 'user.name=athenas1200',
                  '-c', 'user.email=athenas1200@users.noreply.github.com',
                  'commit', '-m', 'Auto-update %s' % time.strftime('%Y-%m-%d %H:%M')])
    if rc != 0 and 'nothing to commit' in out:
        return 0
    rc, out = sh([GIT, 'push', REPO, 'main'], timeout=180)
    log("push %s: %s" % ('ok' if rc == 0 else 'falha', out.strip()[:200]))
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
