# -*- coding: utf-8 -*-
"""BACKUP PERIODICO DO PROJETO S9 PARA O MEGA (pasta erp_backup).
Compacta C:\S9 (exceto FOTOS_CONC, __pycache__, logs antigos) e envia ao MEGA
com nome timestamp: backup_YYYYMMDD_HHMMSS.zip. Mantem os ultimos N backups.
Uso: pythonw backup_mega.py   (agendado a cada 2h)
Log: logs/backup.log
"""
import os, sys, io, time, zipfile, glob, shutil, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = BASE + r'\logs'
os.makedirs(LOGDIR, exist_ok=True)
PROGLOG = LOGDIR + r'\backup.log'
LOCKFILE = LOGDIR + r'\backup.lock'
TMP = os.path.join(LOGDIR, 'backup_tmp')
MANTER = 12   # manter os ultimos 12 backups (2h * 12 = 24h)

EMAIL = "bkp.2021romanatian@gmail.com"
SENHA = "Lin281168***"
MEGA_DIR = "erp_backup"

# pasta/arquivos a excluir do backup
EXCLUIR_PASTAS = {'FOTOS_CONC', '__pycache__', 'backup_tmp'}
EXCLUIR_EXT = ('.log', '.pyc', '.lock')

def log(msg):
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    with io.open(PROGLOG, 'a', encoding='utf-8') as f:
        f.write(line + "\n")
    print(line)

def ja_rodando():
    if not os.path.exists(LOCKFILE):
        return False
    try:
        with io.open(LOCKFILE, encoding='utf-8') as f:
            pid = int(f.read().strip())
        if pid == os.getpid():
            return False
        out = subprocess.run(['tasklist', '/FI', 'PID eq %d' % pid],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return str(pid) in out.stdout
    except Exception:
        return False

if ja_rodando():
    sys.exit(0)
with io.open(LOCKFILE, 'w', encoding='utf-8') as f:
    f.write(str(os.getpid()))

def criar_zip():
    if os.path.exists(TMP):
        shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)
    nome = 'backup_%s.zip' % time.strftime('%Y%m%d_%H%M%S')
    destino = os.path.join(TMP, nome)
    n = 0
    with zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for raiz, dirs, arquivos in os.walk(BASE):
            dirs[:] = [d for d in dirs if d not in EXCLUIR_PASTAS]
            for arq in arquivos:
                if arq.endswith(EXCLUIR_EXT):
                    continue
                p = os.path.join(raiz, arq)
                try:
                    rel = os.path.relpath(p, BASE)
                    if os.path.getsize(p) > 50 * 1024 * 1024:
                        continue
                    zf.write(p, rel)
                    n += 1
                except Exception:
                    pass
    log("zip criado: %s (%d arquivos, %.1f MB)" %
        (nome, n, os.path.getsize(destino) / 1048576))
    return destino, nome

def enviar_mega(zip_path, nome_zip):
    from mega import Mega
    try:
        m = Mega().login(EMAIL, SENHA)
        dest = None
        for n, f in m.get_files().items():
            if f['a'] and f['a'].get('n') == MEGA_DIR and f['t'] == 1:
                dest = f
                break
        if dest is None:
            m.create_folder(MEGA_DIR)
            time.sleep(1)
            for n, f in m.get_files().items():
                if f['a'] and f['a'].get('n') == MEGA_DIR and f['t'] == 1:
                    dest = f
                    break
        if dest is None:
            log("ERRO: pasta erp_backup nao encontrada/criada")
            return False
        m.upload(zip_path, dest['h'], dest_filename=nome_zip)
        time.sleep(2)
        log("upload OK: %s" % nome_zip)
        return True
    except Exception as e:
        log("ERRO upload MEGA: %s" % str(e)[:200])
        return False

def main():
    try:
        destino, nome = criar_zip()
        ok = enviar_mega(destino, nome)
        log("backup %s: %s" % (nome, 'OK' if ok else 'FALHA'))
    except Exception as e:
        log("ERRO backup: %s" % str(e)[:200])
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
        try:
            os.remove(LOCKFILE)
        except Exception:
            pass

if __name__ == '__main__':
    main()
