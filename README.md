# GANTECH Pós-Venda — MVP independente

Aplicação Flask isolada da Central Omnichannel, para duas contas Mercado Livre:
- GANTECH — seller 45066833
- V3R — seller 1106389395

## Recursos implementados
- Login de administrador, senha com hash, formulários protegidos por CSRF.
- Cadastro e edição de ocorrências de devolução, reclamação, reembolso, cancelamento e contestação.
- Status, prazos, responsáveis, próximas ações, evidências, inspeção e desfecho.
- Indicadores, filtros, histórico de edições e cálculo de saldo estimado de perdas.
- Importação e exportação CSV no formato próprio, sem duplicar casos pela chave conta/venda/reclamação.
- OAuth separado para cada conta Mercado Livre (autorização, sem sincronização automática ainda).

## Limites desta versão
**Não** faz leitura automática de reclamações, devoluções, pedidos, envios, pagamentos ou reembolsos via API. A autorização OAuth só guarda credenciais para implementação posterior. **Não** importa diretamente XLSX nativo do Mercado Livre. Não envia contestações. Não oferece vários perfis de usuário nem anexos binários. O campo de evidências aceita referências/links internos, não upload. Não deduz causa de quebra ou culpa do comprador.

## Teste local
```bash
pip install -r requirements.txt
export FLASK_SECRET_KEY='segredo-forte'
export ADMIN_USERNAME='admin'
export ADMIN_PASSWORD='senha-forte'
export DATABASE_URL='sqlite:///posvenda.db'
python app.py
```
Acesse http://localhost:5000. Configure `COOKIE_SECURE=true` **apenas em HTTPS**.

## Implantação Render
1. Crie repositório privado contendo `app.py`, `requirements.txt` e `render.yaml`.
2. Use Blueprint Render ou crie serviço Web Python e banco PostgreSQL dedicado.
3. Configure `ADMIN_PASSWORD` forte; `FLASK_SECRET_KEY` estável; `DATABASE_URL` do PostgreSQL. Não use SQLite em produção no Render, pois o armazenamento pode ser efêmero.
4. Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`.
5. Abra `/health`, depois `/login`.
6. Opcional: configure `ML_CLIENT_ID`, `ML_CLIENT_SECRET`, `ML_REDIRECT_URI` (URL HTTPS exata `https://SEU-SERVICO.onrender.com/oauth/callback`), e cadastre a mesma URL no aplicativo de desenvolvedor do Mercado Livre.
7. Autorize separadamente GANTECH e V3R na aba Contas ML. Faça isso apenas quando estiver pronto para guardar credenciais no banco dedicado.

## Segurança e operação
- Mantenha o repositório privado e as variáveis no ambiente do Render, nunca no código.
- OAuth armazena tokens no banco: limite acesso ao banco, habilite backups e planeje criptografia de tokens antes de uso sensível em produção.
- O primeiro usuário administrador é criado somente quando o banco está vazio e `ADMIN_PASSWORD` está configurada.
- `db.create_all()` cria tabelas, mas não substitui migrações de banco em versões futuras.
- Faça testes em staging antes de conectar contas reais. Este projeto não altera a Central existente.
