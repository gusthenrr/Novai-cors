# NOVAI CORS

Proxy CORS leve para chamadas da extensão aos domínios permitidos do Mercado
Livre. Este serviço pode trabalhar diretamente e não depende da Decodo.

## Variáveis no Railway

- `LR_FORWARD_AUTH=1`: necessário para encaminhar o `access_token` recebido no
  cabeçalho `Authorization` às APIs do Mercado Livre.
- `LR_LOG=1`: opcional durante o diagnóstico; registra método e destino sem
  registrar o token.

Somente se também quiser enviar este tráfego pela Decodo:

- `LR_USE_PROXY=1`
- `LR_PROXY_URL=http://USUARIO:SENHA@gate.decodo.com:7000`

Para o uso normal da API do Mercado Livre, mantenha `LR_USE_PROXY=0` e não
cadastre `LR_PROXY_URL`.

## Verificação

- `GET /_health`: confirma que o servidor iniciou e mostra se o encaminhamento
  de autorização e o proxy estático estão habilitados.
- `GET /ping`: teste simples de rede e CORS.

O comando de produção, o healthcheck e a política de reinício estão definidos
em `railway.json`.
