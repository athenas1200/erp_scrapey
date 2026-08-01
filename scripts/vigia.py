# -*- coding: utf-8 -*-
"""VIGIA - supervisor que mantem os processos do sistema sempre rodando.
A maquina pode desligar 5x/dia: o coletor usa checkpoint (coleta_estado.json)
e retoma de onde parou. Este script roda o tempo todo e:
  - religa o coletor_lote.py se nao estiver rodando (e houver checkpoint pendente)
  - religa o sync_silencioso.py se nao estiver rodando
  - religa o tela_log_server.py se nao estiver rodando
  - roda o mega_etapa.py (lote 100) de tempos em tempos para enviar fotos ao MEGA
Uso: pythonw vigia.py   (registrado na inicializacao do Windows via tarefa S9_Vigia)
"""
import os, sys, io, time, subprocess, re

BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = BASE + r'\logs'
os.makedirs(LOGDIR, exist_ok=True)
VIGLOG = LOGDIR + r'\vigia.log'
VIGLOCK = LOGDIR + r'\vigia.lock'
PY = r'C:\Users\Pe de Apoio\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe'
INTERVALO = 60            # segundos entre verificacoes
MEGA_INTERVALO = 1200     # rodar mega_etapa a cada 20 min
AUTOPUSH_INTERVALO = 1800 # rodar autopush (github) a cada 30 min
CHECKPOINT = LOGDIR + r'\coleta_estado.json'

def ja_rodando():
    if not os.path.exists(VIGLOCK):
        return False
    try:
        with io.open(VIGLOCK, encoding='utf-8') as f:
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
with io.open(VIGLOCK, 'w', encoding='utf-8') as f:
    f.write(str(os.getpid()))

def log(msg):
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    with io.open(VIGLOG, 'a', encoding='utf-8') as f:
        f.write(line + "\n")
    print(line)

def contar_processos(nome_script):
    """Conta processos python (exceto o proprio vigia e o processo atual)
    que tem nome_script na linha de comando."""
    try:
        me = os.getpid()
        ps = ("$me=%d; Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
              "Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -notmatch 'vigia.py' -and "
              "$_.CommandLine -match '%s' } | Measure-Object" % (me, nome_script))
        out = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        m = re.search(r'Count\s*:\s*(\d+)', out.stdout)
        return int(m.group(1)) if m else 0
    except Exception:
        return -1

def iniciar(nome_script, args='', subdir=None):
    """Inicia um script em background de forma totalmente silenciosa.
    Usa subprocess.Popen com pythonw + CREATE_NO_WINDOW (nunca abre janela/cmd piscando)."""
    try:
        caminho = os.path.join(BASE, nome_script) if subdir is None else os.path.join(BASE, subdir, nome_script)
        cmd = [PY, caminho]
        if args:
            cmd += args.split()
        subprocess.Popen(cmd, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        log("iniciado: %s %s" % (nome_script, args))
        return True
    except Exception as e:
        log("falha ao iniciar %s: %s" % (nome_script, e))
        return False

def matar_chrome_headless_perdido(segundos_max=180):
    """Derruba Chrome headless do firecrawl que ficou aberto/perdido por mais de X s.
    Nunca mexe no Chrome normal do usuario (filtro: --headless OU user-data-dir
    temporario, excluindo 'Google\\Chrome\\User Data')."""
    try:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            "Where-Object { $_.CommandLine -match 'headless' -or "
            "($_.CommandLine -match 'user-data-dir' -and $_.CommandLine -notmatch 'Google\\\\Chrome\\\\User Data') } | "
            "Select-Object ProcessId, CreationDate"
        )
        out = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        agora = time.time()
        for linha in out.stdout.splitlines():
            partes = linha.split()
            if len(partes) < 2:
                continue
            pid = partes[0]
            try:
                from datetime import datetime
                dt = datetime.strptime(partes[1] + ' ' + partes[2], '%m/%d/%Y %H:%M:%S')
                idade = agora - dt.timestamp()
                if idade > segundos_max:
                    subprocess.run(['taskkill', '/F', '/T', '/PID', pid],
                                   capture_output=True, text=True, timeout=30,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    log("watchdog: Chrome headless PID %s aberto ha %.0fs > %ds - derrubado" %
                        (pid, idade, segundos_max))
            except Exception:
                pass
    except Exception:
        pass

def main():
    log("VIGIA iniciado. intervalo=%ds" % INTERVALO)
    # relatorio de restauracao apos boot
    try:
        import sys
        sys.path.insert(0, os.path.join(BASE, 'knowledge_base'))
        import health
        r = health.relatorio_restauracao()
        log("restauracao: %s" % r.get("acao"))
    except Exception as e:
        log("relatorio restauracao falhou: %s" % str(e)[:120])
    t_mega = time.time()
    t_push = time.time()
    while True:
        time.sleep(INTERVALO)
        # saude dos servicos -> service_health.json (monitoramento externo)
        try:
            sys.path.insert(0, os.path.join(BASE, 'knowledge_base'))
            import health
            health.coletar()
        except Exception:
            pass
        # Chrome headless perdido: derruba sempre que estiver aberto ha muito tempo
        matar_chrome_headless_perdido(180)
        # coletor: religa apenas se houver trabalho pendente (checkpoint existe)
        # (se o ciclo completou, o checkpoint foi removido e a tarefa das 23h
        #  recomeca um novo ciclo diario)
        if os.path.exists(CHECKPOINT):
            if contar_processos('coletor_lote.py') == 0:
                iniciar('coletor_lote.py', '0 120')
        # sync silencioso: sempre rodando
        if contar_processos('sync_silencioso.py') == 0:
            iniciar('sync_silencioso.py')
        # tela web: sempre rodando
        if contar_processos('tela_log_server.py') == 0:
            iniciar('tela_log_server.py')
        # S9 MEMORY ENGINE: sempre rodando (aprende o ERP no banco memory_*)
        if contar_processos('memoria_service.py') == 0:
            iniciar('memoria_service.py', '300', subdir='knowledge_base')
        # MEGA: periodicamente sobe fotos locais (lote 100)
        if time.time() - t_mega >= MEGA_INTERVALO:
            t_mega = time.time()
            if contar_processos('mega_etapa.py') == 0:
                iniciar('mega_etapa.py', '100')
        # AUTOPUSH: envia alteracoes ao GitHub periodicamente
        if time.time() - t_push >= AUTOPUSH_INTERVALO:
            t_push = time.time()
            iniciar('autopush.py')

if __name__ == '__main__':
    main()
