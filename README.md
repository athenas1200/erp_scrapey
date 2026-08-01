# ERP Scrapey

Sistema de integração e monitoramento de preços concorrentes para ERP S9.

## Funcionalidades

- **Sincronização contínua** SQL Server (S9_Real) → PostgreSQL (VPS), loop a cada 5 min com log diário JSON.
- **Dashboard web** local (porta 8090) com log diário, abas e exportação JSON.
- **Relatório diário por e-mail** com seções de dados e layout alterados.
- **Coleta de preços concorrentes** via Firecrawl (Mercado Livre, Shopmedical, Fisio Store + fallback Google), com checkpoint persistente (retoma após reinicialização).
- **Fotos dos produtos**: extração .webp, download da foto do concorrente, upload em etapas para o MEGA com link salvo no banco.
- **Migração de dados** auditada do SQL Server para o PostgreSQL.

## Estrutura

```
scripts/
  sincronizador/     sync_silencioso.py, tela_log_server.py
  coletor_precos/    coletor_lote.py, firecrawl_batch.py
  fotos/             fotos_webp.py
  mega/              mega_etapa.py, mega_upload_img.py
  relatorio/         email_diario.py
  migracao/          mig_data.py
  util/              scripts de apoio
logs/                logs de sync, coleta e e-mail
docs/                documentação
```

## Agendamentos Windows

- `S9_Relatorio_Diario` — e-mail diário às 00:00
- `S9_Coleta_Precos` — coleta de preços diariamente às 23:00 (silencioso, via pythonw)

## Configuração

As credenciais estão nos scripts (não versionar em produção): conexões SQL Server, PostgreSQL (via túnel SSH paramiko), SMTP e conta MEGA.
