# -*- coding: utf-8 -*-
"""service_health.json - monitoramento de saude dos servicos + recuperacao.
Le os heartbeats de cada servico e os checkpoints e grava o estado atual.
Chamado pelo vigia a cada ciclo. Tambem gera relatorio de restauracao no boot.
"""
import json, io, os, time

BASE = r'C:\S9'
LOGDIR = BASE + r'\logs'
HEALTH = LOGDIR + r'\service_health.json'
HEARTBEATS = {
    'memoria': LOGDIR + r'\heartbeat_memoria.json',
    'sync': LOGDIR + r'\heartbeat_sync.json',
    'coletor': LOGDIR + r'\heartbeat_coletor.json',
}
CHECKPOINTS = {
    'memoria': BASE + r'\knowledge_base\memory_checkpoint.json',
    'coletor': LOGDIR + r'\coleta_estado.json',
}
ERROS = {
    'memoria': LOGDIR + r'\memoria.log',
    'coletor': LOGDIR + r'\erro_coletor.txt',
    'sync': LOGDIR + r'\err_sync.txt',
}
ARGS = {
    'memoria': 'memoria_service.py',
    'sync': 'sync_silencioso.py',
    'coletor': 'coletor_lote.py',
    'tela': 'tela_log_server.py',
}


def ler_json(path):
    try:
        return json.load(io.open(path, encoding='utf-8'))
    except Exception:
        return None


def ultima_linha_erro(path, palavras=('ERRO', 'Traceback')):
    try:
        with io.open(path, encoding='utf-8', errors='ignore') as f:
            for linha in f:
                pass
            return ''
    except Exception:
        return ''


def serviço_online(nome_script):
    try:
        import subprocess, re
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
              "Where-Object { $_.CommandLine -match '%s' } | Measure-Object" % nome_script)
        out = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        m = re.search(r'Count\s*:\s*(\d+)', out.stdout)
        return int(m.group(1)) if m else 0
    except Exception:
        return -1


def coletar():
    agora = time.strftime('%Y-%m-%d %H:%M:%S')
    state = {
        "gerado_em": agora,
        "servicos": {},
        "checkpoints": {},
        "ultimo_erro": "",
    }
    for nome, hbpath in HEARTBEATS.items():
        hb = ler_json(hbpath)
        on = serviço_online(ARGS.get(nome, ''))
        state["servicos"][nome] = {
            "status": "online" if on else "offline",
            "heartbeat": hb or {},
            "processos": on,
        }
    state["servicos"]["tela"] = {"status": "online" if serviço_online('tela_log_server.py') else "offline"}
    for nome, ckpath in CHECKPOINTS.items():
        ck = ler_json(ckpath)
        if ck:
            info = {}
            if nome == 'memoria':
                info = {"ciclo": ck.get("ciclo", 0), "ultima_exec": ck.get("ultima_exec", "")}
            elif nome == 'coletor':
                info = {"feitos": len(ck.get('feitos', []))}
            state["checkpoints"][nome] = info
    try:
        io.open(HEALTH, 'w', encoding='utf-8').write(json.dumps(state, ensure_ascii=False, indent=1))
    except Exception:
        pass
    return state


def relatorio_restauracao():
    """Apos boot: le checkpoints e gera resumo de restauracao."""
    agora = time.strftime('%Y-%m-%d %H:%M:%S')
    ck_mem = ler_json(CHECKPOINTS['memoria'])
    ck_col = ler_json(CHECKPOINTS['coletor'])
    resumo = {
        "restaurado_em": agora,
        "memoria": {
            "existe_checkpoint": ck_mem is not None,
            "ciclo_anterior": (ck_mem or {}).get("ciclo", 0),
            "ultima_exec": (ck_mem or {}).get("ultima_exec", ""),
        },
        "coletor": {
            "existe_checkpoint": ck_col is not None,
            "produtos_feitos": len((ck_col or {}).get('feitos', [])),
        },
        "acao": "retomando de onde parou" if (ck_mem or ck_col) else "primeira inicializacao",
    }
    try:
        io.open(LOGDIR + r'\restauracao.json', 'w', encoding='utf-8').write(
            json.dumps(resumo, ensure_ascii=False, indent=1))
    except Exception:
        pass
    return resumo


if __name__ == '__main__':
    st = coletar()
    print(json.dumps(st, ensure_ascii=False, indent=1))
