CREATE TABLE IF NOT EXISTS perfil (
    id text PRIMARY KEY,
    nome text NOT NULL,
    slug_linkedin text NOT NULL DEFAULT '',
    respostas_simples text NOT NULL DEFAULT '',
    mensagem_perfil_template text NOT NULL DEFAULT '',
    resposta_publica_dm_sucesso text NOT NULL DEFAULT '',
    resposta_publica_dm_falha text NOT NULL DEFAULT '',
    navegador text NOT NULL DEFAULT 'edge',
    perfil_navegador_subpasta text NOT NULL DEFAULT 'Default',
    usuario_id text,
    criado_em timestamptz NOT NULL DEFAULT now(),
    atualizado_em timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS config_global (
    chave text PRIMARY KEY,
    valor text NOT NULL DEFAULT '',
    atualizado_em timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS campanha (
    id text PRIMARY KEY,
    perfil_id text NOT NULL REFERENCES perfil (id),
    nome text NOT NULL,
    url_post text NOT NULL DEFAULT '',
    palavras_chave text NOT NULL DEFAULT '',
    urls_extras text NOT NULL DEFAULT '',
    arquivada boolean NOT NULL DEFAULT false,
    usuario_id text,
    criado_em timestamptz NOT NULL DEFAULT now(),
    atualizado_em timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS campanha_perfil_criado_idx
    ON campanha (perfil_id, criado_em DESC);

CREATE TABLE IF NOT EXISTS historico_respondido (
    campanha_id text NOT NULL REFERENCES campanha (id) ON DELETE CASCADE,
    url_chave text NOT NULL,
    url_post text NOT NULL DEFAULT '',
    tipo text NOT NULL CHECK (tipo IN ('comentario', 'perfil')),
    chave text NOT NULL,
    criado_em timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (campanha_id, url_chave, tipo, chave)
);

CREATE TABLE IF NOT EXISTS execucao (
    run_id text PRIMARY KEY,
    campanha_id text NOT NULL REFERENCES campanha (id) ON DELETE CASCADE,
    started_at timestamptz,
    ended_at timestamptz,
    duration_ms bigint NOT NULL DEFAULT 0,
    mode text NOT NULL DEFAULT '',
    url_post text NOT NULL DEFAULT '',
    recognized integer NOT NULL DEFAULT 0,
    responded integer NOT NULL DEFAULT 0,
    edited integer NOT NULL DEFAULT 0,
    reposted_after_delete integer NOT NULL DEFAULT 0,
    skipped integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS evento_metrica (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    campanha_id text NOT NULL REFERENCES campanha (id) ON DELETE CASCADE,
    ts timestamptz NOT NULL DEFAULT now(),
    dia date NOT NULL,
    tipo text NOT NULL,
    amount integer NOT NULL DEFAULT 1,
    texto text NOT NULL DEFAULT '',
    duration_ms integer
);

CREATE INDEX IF NOT EXISTS evento_metrica_campanha_dia_idx
    ON evento_metrica (campanha_id, dia, tipo);

ALTER TABLE perfil ADD COLUMN IF NOT EXISTS slug_manual text NOT NULL DEFAULT '';
