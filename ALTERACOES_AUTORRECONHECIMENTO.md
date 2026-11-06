# Alterações: robô respondia comentários que já tinham resposta minha

Documento para levar o contexto desta sessão para outro chat. Descreve o
problema, o diagnóstico feito em cima dos logs reais, cada alteração de código
e como validar.

Projeto: automação de resposta a comentários do LinkedIn (Playwright + GUI
CustomTkinter, várias contas).

---

## 1. Sintomas relatados

1. O robô respondia **duas vezes a mesma pessoa**, uma logo após a outra.
2. O robô respondia comentários **onde já existia uma resposta minha** (sou o
   autor do post), em vez de ignorá-los.

## 2. Diagnóstico (baseado nos logs `debug-*.log`)

Os logs são JSON por linha, com `runId` por execução. Os eventos que
importam são `eligibility_evaluated` (decisão por comentário),
`comment_skipped` (motivo do pulo) e `reply_published_success`.

Foram três causas distintas, descobertas em rodadas sucessivas:

### Causa A — o robô não sabia qual perfil era o dele
`MEU_PERFIL_IN_SLUG` estava vazio na conta e o fallback que derivava o perfil da
URL do post devolvia `oduarteoficial_vaga-home-office-software-engineer-pleno`
em vez de `oduarteoficial`, porque cortava só o sufixo `-activity` e nunca o
primeiro `_`. Sem perfil válido, `comentario_eh_meu()` devolvia `False` para as
próprias respostas do robô — daí a resposta dupla.

Evidência: nos logs de 23:16, `body_prefix` igual ao texto das próprias
respostas com `is_my_comment: false`, e zero pulos por `own_comment` em 41
execuções.

### Causa B — a resposta do fio não era encontrada no DOM
Depois de corrigir o perfil, o log de 23:47 mostrou, no MESMO comentário:

```
thread_diag: {"hasAltReply": true, "hasReplyCounterText": true, "nestedCount": 0}
has_reply_block: false
```

Ou seja: existia resposta no fio, mas `fio_ja_tem_minha_resposta()` não achava.
Motivo: a função só coletava respostas embrulhadas em containers conhecidos
(`comment-container`, `replaceableComment`, `li.comments-comment-item`). Quando
o LinkedIn renderiza a resposta como `<article class="comments-comment-entity--reply">`
solta, nada era coletado.

### Causa C — só a comparação por texto alcançava a resposta
No log de 00:09, os pulos por `thread_has_my_reply` passaram a aparecer (11
deles), mas 3 comentários ainda foram respondidos. O que os três tinham em
comum: a resposta já existente usava um texto **antigo**, removido da
biblioteca (`"opaa, me confirma se você recebeu? se ainda não, me envia 'JAVA'
no direct que eu te envio na hora"`). Os corretamente pulados tinham respostas
com as frases atuais.

Conclusão: naquela região do DOM (dentro do próprio comentário, depois do
corpo) apenas a checagem por frase rodava; a checagem por perfil e por selo
"Autor" não alcançava ali. Com a frase fora da biblioteca, nada barrava.

---

## 3. Alterações por arquivo

### `automacao_linkedin/main.py`

| Local | O que mudou |
|---|---|
| `_slug_da_url_post()` (nova, ~L2593) | Deriva o perfil da URL cortando no primeiro `_` e limpando `-activity...`. Devolve vazio para URLs sem perfil (`/feed/update/...`). |
| `_slug_efetivo_config()` (~L2609) | Ordem de prioridade: campo manual → detectado na sessão → detectado e salvo antes → derivado da URL. |
| `_SLUG_DETECTADO_SESSAO` (~L2522) | Cache de módulo com o perfil descoberto na execução (sobrevive ao `importlib.reload(config)`). |
| `detectar_slug_da_conta(page)` (nova, ~L2525) | Abre `linkedin.com/in/me/`, lê o `/in/<perfil>` da URL final; se falhar, procura o link do menu "Você". Salva em `MEU_PERFIL_IN_SLUG_DETECTADO` e imprime `[Info] Perfil desta conta: /in/<slug>`. |
| `iniciar_bot()` (~L3841) | Chama `detectar_slug_da_conta(page)` logo após `garantir_login_linkedin(page)`. |
| `comentario_eh_meu()` (~L3140) | Ganhou `escopoProprio()`: clona o nó, remove listas de resposta e tudo que vem depois do corpo, e roda as checagens só no comentário em si. Passou a reconhecer por **selo "Autor"** e por **frase da biblioteca**, além do link `/in/`. |
| `fio_ja_tem_minha_resposta()` (~L2809) | Ver detalhamento abaixo. |
| `_JS_RAIZ_DO_FIO` (~L2692) | Trecho JS reaproveitado que acha a raiz do fio e o bloco do comentário atual. É colado **dentro** da função avaliada. |
| `respostas_do_fio_estao_ocultas(item)` (nova, ~L2733) | `True` quando o fio anuncia "1 resposta" mas nenhuma está carregada no DOM. |
| `expandir_respostas_do_fio(item)` (nova, ~L2773) | Clica em "N respostas" / "Ver respostas anteriores" daquele fio e espera 1,2 s. |
| `ja_respondi_esta_pessoa_no_post(page, perfil_url)` (nova, ~L1985) | Varre a página inteira procurando um comentário **meu** que **mencione** aquele perfil. Independe da estrutura do fio e do texto usado. |
| Laço de elegibilidade (~L4210) | Se o fio anuncia resposta oculta, expande e reavalia; grava `respostas_expandidas` e `bloqueou_apos_expandir` no `thread_diag`. |
| Laço de elegibilidade (~L4340) | Antes de responder, aplica `ja_respondi_esta_pessoa_no_post`; pula com motivo `profile_reply_found_on_page`. |
| Log de pulos (~L4239) | Imprime `[PULADO] Este comentário/resposta é meu.` quando o motivo é `own_comment`. |

Detalhamento de `fio_ja_tem_minha_resposta()`:

- **FONTE F (nova)** — em vez de tentar adivinhar o seletor da resposta, monta
  as "regiões de resposta" do fio: (a) tudo que está na raiz do fio fora do
  bloco deste comentário e (b) tudo que está dentro do comentário **depois do
  corpo dele** (é onde o LinkedIn pendura a resposta). As três provas — link do
  meu perfil, selo "Autor" e frase da biblioteca — rodam sobre essas regiões.
- **Rede de segurança** — se a raiz do fio agrupar mais de 12 comentários, a
  fonte F é desligada. Sem isso, uma resposta minha em qualquer lugar poderia
  bloquear o post inteiro e o robô pararia de responder.
- **`contemAlgumaFrase()`** — aceita a frase inteira **ou** os 40 primeiros
  caracteres dela, para o caso de comentário cortado por "…ver mais".

### `automacao_linkedin/config.py`

- Novo `MEU_PERFIL_IN_SLUG_DETECTADO`, lido das settings da conta (~L134).
- Comentários atualizados: o campo manual passa a ser opcional; a derivação
  pela URL virou último recurso.

### `automacao_linkedin/settings_manager.py`

- `atualizar_settings(campos)` (~L153): grava só as chaves informadas,
  preservando o resto do arquivo da conta. Usada pelo robô para salvar o perfil
  detectado sem atropelar o que a tela gravou.
- `DEFAULT_SETTINGS` ganhou `MEU_PERFIL_IN_SLUG_DETECTADO` (~L56).

### `gui.py`

- `PaginaConta.coletar()` (~L426): passou a reler a conta do disco
  (`sm.carregar_conta`) antes de aplicar os campos, em vez de partir de um
  retrato antigo. Sem isso a tela apagava o perfil detectado durante a execução.
- `_entrada()` (~L228): aceita `dica` e usa `placeholder_text`.
- Campo "Meu perfil (/in/)" (~L276): mostra o perfil detectado como dica e
  explica que preencher é só para forçar.

---

## 4. Camadas de proteção resultantes

Na ordem em que agem, para o robô não responder duas vezes:

1. **Histórico em disco** (`respondidos_history_<conta>.json`) — comentários e
   perfis já respondidos → motivo `already_replied` / `profile_already_replied`.
2. **`comentario_eh_meu`** — não responde às próprias respostas → `own_comment`.
3. **`fio_ja_tem_minha_resposta`** — fio já atendido → `thread_has_my_reply`.
4. **Expansão de respostas ocultas** — abre "1 resposta" e reavalia a regra 3.
5. **`ja_respondi_esta_pessoa_no_post`** — última barreira, por menção na
   página → `profile_reply_found_on_page`.

## 5. Como validar

1. Iniciar a conta e conferir no log: `[Info] Perfil desta conta: /in/<seu-perfil>`.
2. Rodar duas vezes seguidas no mesmo post: a segunda execução não deve
   responder ninguém.
3. Nos logs de depuração, conferir os motivos de pulo. Se ainda escapar algum
   caso, o `thread_diag` mostra `hasAltReply`, `hasReplyCounterText`,
   `respostas_expandidas` e `bloqueou_apos_expandir` — dá para saber qual
   camada falhou.

### Testes executados nesta sessão

Foram feitos com páginas HTML simuladas e Playwright (arquivos temporários,
removidos depois). Cenários cobertos, todos passando:

- Derivação do perfil pela URL e ordem de prioridade das quatro fontes.
- Minha resposta reconhecida **sem** perfil configurado (só selo + frase).
- Comentário de outra pessoa com resposta minha aninhada não é confundido com
  comentário meu.
- Fio com minha resposta como `article --reply` solto → bloqueado.
- Fio com minha resposta de **texto antigo** (fora da biblioteca) → bloqueado
  (inclusive com a biblioteca de frases vazia).
- Fio com resposta cortada por "…ver mais" → bloqueado.
- Fio que anuncia "1 resposta" sem carregá-la → detecta, expande e bloqueia.
- Controles negativos: resposta de outra pessoa **não** bloqueia; comentário sem
  resposta continua sendo respondido; layout antigo `ul/li` segue funcionando.

## 6. Pontos de atenção para quem continuar

- Os seletores do LinkedIn mudam com frequência. As checagens foram escritas
  para depender o mínimo possível deles: as provas são link `/in/`, selo
  "Autor" e texto das frases, aplicadas a regiões calculadas por posição
  (antes/depois do corpo) e não por classe CSS.
- O limite de 12 comentários da fonte F é a proteção contra o cenário oposto
  (bloquear o post inteiro). Se um dia o robô parar de responder todo mundo,
  esse limite e a função `raizEBloco` são o primeiro lugar para olhar.
- `expandir_respostas_do_fio` clica na página. Ele só roda quando o fio anuncia
  resposta e nenhuma está carregada, para não pesar a execução.
- A checagem por menção assume que o primeiro link `/in/` de um comentário é o
  autor dele — vale para o layout atual.
