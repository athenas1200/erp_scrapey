# AGENTS.md — Memória do Projeto

Guia permanente para agentes que trabalham neste repositório (ERP Scrapey).

## Objetivo
Integrar o ERP S9: sincronizar SQL Server → PostgreSQL (VPS), monitorar preços
concorrentes diariamente (scraper Firecrawl), baixar fotos, enviar ao MEGA e
gerar relatórios por e-mail.

## Conexões / Credenciais
- **VPS**: 84.247.189.155, root, porta PG 5434. Túnel SSH (paramiko) → porta local **15434**.
- **PostgreSQL**: db `s9_real`, user `postgres`. Acesso via túnel. `session_replication_role = replica` para inserir com FK desativada.
- **SQL Server**: `192.168.0.101,1435`, db `S9_Real`. Conexão via DSN pyodbc.
- **MEGA**: conta `bkp.2021romanatian@gmail.com`, pasta `FotosS9`. `mega.py` requer `tenacity==8.2.3` (Python 3.14).
- **Firecrawl**: MCP keyless (`npx firecrawl-mcp`) — tem rate limit por IP (backoff 60s).

## Estrutura
```
scripts/sincronizador/   sync_silencioso.py (loop 5min), tela_log_server.py (porta 8090)
scripts/coletor_precos/  coletor_lote.py (scraper 2332 produtos), firecrawl_batch.py (batch MCP)
scripts/fotos/           fotos_webp.py (extração .webp de Prod_Serv_Fotos)
scripts/mega/            mega_etapa.py (lotes), mega_upload_img.py (link público)
scripts/relatorio/       email_diario.py (00:00, só se houver alterações)
scripts/migracao/        mig_data.py (migração auditada)
```

## Regras de negócio (importantes!)
1. **Scraper por produto**: até **3 preços de 3 sites distintos**. Concorrentes fixos:
   Mercado Livre, Shopmedical, Fisio Store. Fallback: Google (Firecrawl search).
2. **Link mapeado**: passadas seguintes usam scrape do link salvo (rápido). Se o link
   não tiver preço (sem estoque — raro), procura OUTRA empresa.
3. **Histórico**: cada coleta cria registro NOVO (nunca sobrescreve). `data_coleta` = NOW(),
   `data_preco` = dia. Isso gera histórico de preços por data/hora.
4. **Fotos**: máximo **4 fotos por produto** (`MAX_FOTOS = 4`). Se já tiver 4 na pasta
   `FOTOS_CONC\<codigo>`, não baixa mais.
5. **Fotos → MEGA**: salva primeiro na máquina, depois upload em lotes (`mega_etapa.py`)
   para evitar rate limit. Link salvo em `concorrente.foto_mega`.
6. **Checkpoint persistente** (`logs/coleta_estado.json`): se a máquina reiniciar, volta de
   onde parou. Ao completar ciclo, zera e recomeça (histórico diário).
7. **Watchdog Chrome**: coletor roda uma thread que derruba Chrome **headless** do firecrawl
   que ficar preso por mais de `CHROME_TIMEOUT` (padrão 120s, 2º arg do script). Nunca mata o
   Chrome do usuário (filtro: `--headless` OU user-data-dir temporário, excluindo
   `Google\Chrome\User Data`).
8. **Fotos → MEGA em lote de 100**: o coletor NUNCA envia direto ao MEGA — só baixa/converta
   para `FOTOS_CONC\<codigo>\<site>.webp` e grava `foto_local`. O upload é feito
   separadamente por `mega_etapa.py` (limite padrão **100** por execução, arg 1).
9. **Tarefas agendadas**: `S9_Coleta_Precos` (23:00, pythonw silencioso), `S9_Relatorio_Diario` (00:00).
   Para iniciar pythonw em background no Windows: usar `cmd /c` (Start-Process direto com
   pythonw encerra com exit code 2).
10. **VIGIA (supervisor autônomo)**: `C:\S9\vigia.py` roda o tempo todo e religa
    coletor/sync/tela automaticamente se caírem. Roda na inicialização do Windows via
    atalho `S9_Vigia.lnk` na pasta de Inicialização (aponta para `C:\S9\logs\rodar_vigia.cmd`).
    Se a máquina reiniciar, ele volta sozinho — sem interferência humana.
11. **AUTOPUSH GitHub**: `C:\S9\autopush.py` (rodado pelo vigia a cada 30 min) copia os
    scripts de `C:\S9` para o repositório e faz `git add/commit/push` automaticamente.
    Envia ao GitHub tudo que mudou, sem intervenção.
12. **EMAIL HORÁRIO**: `C:\S9\email_horario.py` envia a cada 1h (via vigia) um relatório
    com resumo por concorrente (produtos extraídos) + produtos com até 5 concorrentes +
    modificações de dados. `--auto` para uso agendado (não loga no console).
13. **EMAIL CONCORRENTE**: `C:\S9\email_concorrente.py` envia sob demanda (1x/dia, 21h)
    o que foi coletado hoje na tabela concorrente.

## LOCAL DE PRODUÇÃO: C:\S9
Todo o sistema roda a partir de **C:\S9** (NÃO do Temp do opencode). Estrutura:
```
C:\S9\  scripts de produção (vigia.py, coletor_lote.py, sync_silencioso.py,
        tela_log_server.py, mega_etapa.py, mega_upload_img.py, autopush.py,
        email_horario.py, email_concorrente.py, email_diario.py, firecrawl_batch.py,
        fotos_webp.py, coletor_precos.py, email_config.json, mig_cat.json,
        sql_rowcounts.json)
C:\S9\logs\    rodar_vigia.cmd, rodar_sync.cmd, rodar_coletor.cmd,
        rodar_email_diario.cmd, *.log, checkpoints (*.json)
C:\S9\FOTOS_CONC\<codigo>\  fotos .webp baixadas
```
- Inicialização do Windows: atalho `S9_Vigia.lnk` → `C:\S9\logs\rodar_vigia.cmd`.
- Tarefas agendadas: `S9_Coleta_Precos` → `rodar_coletor.cmd`, `S9_Relatorio_Diario` → `rodar_email_diario.cmd`.
- O repositório GitHub é uma cópia de backup; o código-fonte de execução é o C:\S9.

## Tabela `concorrente` (PostgreSQL)
Colunas: `id, produto_ordem, produto_codigo, produto_nome, ean, ean3, concorrente,
url, preco, preco_avista, preco_pix, site_empresa, cidade, estado, foto_local,
foto_mega, disponivel, data_coleta, data_preco`.

## Tela web (tela_log_server.py)
- Aba Log Diário (sync) e aba **Concorrente** (preços/fotos).
- Endpoints: `/json?dia=`, `/concorrente?dia=&cod=`, `/concorrente_resumo`,
  `/concorrente_produtos`, `/foto?p=caminho`.
- Colunas concorrente: Foto | Codigo | Produto | EAN | EAN3 | Empresa | Preço | À vista | Pix | Site | Cidade | Data.

## Comandos úteis
- Rodar scraper completo silencioso: `pythonw coletor_lote.py 0`
- Rodar lote teste (1 produto): `python coletor_lote.py 1`
- Upload MEGA em lotes: `python mega_etapa.py 10`
- Testar/validar conexões e banco via scripts `util/*` e `ver_*.py`.

## Observações técnicas
- `psycopg2.execute()` retorna None — usar `fetchall()`.
- SQL Server limita 2100 parâmetros por consulta.
- Nomes de coluna PG > 63 chars são truncados.
- Python 3.14: `tenacity` quebrado em versão nova — fix com `tenacity==8.2.3`.
- Arquivos de produção em `C:\S9\` (histórico/trabalho antigo em `C:\Users\Pe de Apoio\AppData\Local\Temp\opencode\`).
- Para iniciar pythonw oculto: `cmd /c "C:\...\pythonw.exe" "C:\S9\script.py"` (nunca Start-Process direto com pythonw).
