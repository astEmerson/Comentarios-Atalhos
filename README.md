# Automação de Respostas para LinkedIn

Um robô inteligente desenvolvido para responder automaticamente a comentários em suas postagens do LinkedIn, extrair potenciais leads e enviar mensagens diretas (DMs) com altíssima resiliência contra as atualizações de interface do LinkedIn.

## 🚀 Funcionalidades Principais

- **Resposta Automática a Comentários:** Lê os comentários de uma postagem específica e responde baseado em palavras-chave.
- **Variação de Respostas (Anti-Spam):** Sorteia respostas diferentes de uma biblioteca de frases, garantindo que o LinkedIn não bloqueie sua conta por spam.
- **Isca / Envio de DM:** (Modo `dm_com_comentario`) Pode enviar uma mensagem diretamente no inbox (DM) do autor do comentário e, em seguida, responder ao comentário público avisando que a mensagem foi enviada.
- **Integração com IA:** (Modo `comentario_ia`) Pode usar inteligência artificial para gerar respostas contextuais baseadas no conteúdo do comentário e do post.
- **Resiliência de Interface:** Utiliza chaves universais (`componentkey` e URNs) e varredura de contêineres DOM, não dependendo de classes CSS frágeis do LinkedIn.
- **Várias Contas em Paralelo:** Cada conta é uma página própria no painel, com processo, navegador, histórico e métricas separados. Iniciar uma não pausa as outras.

---

## 💻 Pré-requisitos e Instalação

1. **Python 3.10+** instalado em sua máquina.
2. Clone este repositório para sua máquina local.
3. Instale as dependências (se você tiver um `requirements.txt`):
   ```bash
   pip install -r requirements.txt
   ```
4. Instale os navegadores do Playwright (motor de automação base):
   ```bash
   playwright install
   ```

---

## 🎮 Como Usar e Testar a Automação

A forma mais fácil de rodar e testar o projeto é utilizando a Interface Gráfica (GUI).

### 1. Iniciando a Interface
Abra o seu terminal na pasta do projeto e execute:
```bash
python gui.py
```
Uma janela de configurações escura (estilo painel de controle) será aberta.

Na coluna da esquerda ficam as **contas**. Cada uma abre uma página completa e independente.

### 2. Configurando a Conta
Na aba **Campanha**:
- **Modo de Automação:**
  - **⚠️ IMPORTANTE:** Atualmente, o modo principal e **100% funcional** para a nova versão do LinkedIn é o modo **`comentario_simples` (Simples)**. Esse modo lê a palavra-chave e responde ao comentário sorteando uma frase da sua biblioteca. Use sempre esta opção.
- **URL do Post:** Cole o link direto da postagem do LinkedIn (pegue em "Copiar link da publicação").
- **Palavras-Chave:** Defina a palavra-chave que aciona o robô (ex: `Quero`, `Eu quero`). Se a pessoa comentar algo sem essa palavra, o robô ignora.
- **Posts extras:** Um link por linha. Quando o post da vez esgota, a automação vai sozinha para o próximo da fila.

Na aba **Sessão e navegador**:
- **Cookie li_at:** Cole o valor do cookie `li_at` da conta (F12 → Aplicativo → Cookies → `https://www.linkedin.com`). Com ele o robô entra já logado, sem senha nem código.
- **Navegador e Perfil:** Cada conta usa uma pasta de perfil exclusiva, então duas contas podem rodar ao mesmo tempo sem brigar pelo mesmo navegador.

Nas abas **Textos** e **Ritmo e regras** ficam as frases sorteadas, a espera entre respostas (0 = responde e já vai para o próximo) e as regras anti-repetição.

### 3. Rodando
1. Clique em **Iniciar**. As configurações são salvas automaticamente antes de começar.
2. O robô abrirá uma janela do navegador sozinha. **Não clique nem mexa no mouse enquanto ele estiver trabalhando na tela do LinkedIn**.
4. Ele vai:
   - Carregar a página.
   - Expandir os comentários ("Ver comentários mais recentes").
   - Identificar quem disse a palavra-chave.
   - Digitar a resposta e enviar.

---

## 👥 Rodando várias contas (ou vários posts) ao mesmo tempo

- **Nova conta:** botão **+ Nova conta** na lateral. Cada conta tem cookie, post, palavras-chave, textos, histórico e métricas próprios.
- **Mesma conta em dois posts:** abra a conta e use **Duplicar atual**. A cópia mantém a mesma sessão do LinkedIn; basta trocar a URL do post nela. As duas rodam juntas, cada uma na sua janela de navegador.
- **Independência:** iniciar ou parar uma conta não interfere nas outras, e trocar de página não pausa nada. A lateral mostra `●` em quem está rodando.
- **Peso na máquina:** cada conta em execução é um navegador aberto. Duas ou três rodam tranquilas em um PC comum; acima disso, prefira intervalos maiores na aba *Ritmo e regras*.
- **Onde ficam os dados:** um arquivo por conta em `contas/`, mais `respondidos_history_<conta>.json` e `historico_metricas_<conta>.json` dentro de `automacao_linkedin/`. O `settings.json` antigo é migrado sozinho na primeira abertura e deixa de ser usado.

---

## 🛡️ Mecanismos de Proteção (Por que ele é seguro?)

Este robô possui regras estritas para evitar passar vergonha no seu perfil:
- **Proteção contra Duplicatas:** O robô lê a "árvore" inteira de comentários (o comentário da pessoa e todas as respostas grudadas a ele). Se ele encontrar a palavra **"Autor"** do lado do seu nome, ou se o texto do comentário já contiver a sua frase de resposta, ele **pula automaticamente**.
- **Chave Permanente (URN):** Mesmo que você pare o robô e reinicie no dia seguinte, ele usa um identificador escondido no código do LinkedIn (`componentkey`) para lembrar quem ele já atendeu.
- **Histórico por perfil:** além do comentário, o robô guarda o perfil de quem já foi respondido naquele post. Se a mesma pessoa comentar de novo (ou o LinkedIn devolver o comentário com outro id), ela não é respondida duas vezes. Para liberar todo mundo de novo, use **Limpar histórico deste post** na aba *Ritmo e regras*.
- **Bloqueio de Menu:** Ele foi programado para clicar exatamente no botão azul "Responder" e ignorar os ícones de "3 pontinhos" ou de "Abrir menu", não importando se o layout do LinkedIn mudar.

## ⚠️ Solução de Problemas Comuns

- **O robô abriu o LinkedIn mas pediu login:** O perfil selecionado na interface (`Default` ou `Profile 1`) não está logado no LinkedIn. Abra o seu navegador normal, acesse o LinkedIn e faça login. Na próxima execução do robô, ele já entrará logado.
- **Terminal cheio de erros (Crash):** Certifique-se de que fechou todas as janelas "fantasmas" de terminais antigos antes de iniciar uma rodada nova. Você pode usar o comando `taskkill /f /im python.exe` no terminal (apenas no Windows) para limpar processos travados antes de iniciar o `python gui.py`.
