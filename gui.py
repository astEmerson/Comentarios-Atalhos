"""
Painel da automação de respostas do LinkedIn.

A lateral filtra campanhas por perfil. Cookie, navegador e mensagens ficam no
perfil. Cada campanha guarda só o post e herda o resto ao ser criada.
"""

import os
import subprocess
import sys
import threading
from collections import deque
from tkinter import messagebox

import customtkinter as ctk

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "automacao_linkedin"))
from automacao_linkedin import historico, repositorio
from automacao_linkedin import settings_manager as sm

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

COR_OK = "#2F9E62"
COR_OK_HOVER = "#23764A"
COR_PARAR = "#D4534A"
COR_PARAR_HOVER = "#A33B34"
COR_AMBER = "#E08A2F"
COR_FUNDO = "#0C1016"
COR_LATERAL = "#121820"
COR_CARD = "#1C2531"
COR_PAINEL = "#171E28"
COR_LINHA = "#2C3848"
COR_TEXTO = "#E7EDF5"
COR_MUTED = "#8E9AAB"
COR_AZUL = "#7EB6FF"

MAX_LINHAS_LOG = 600
INTERVALO_METRICAS_MS = 4000


def _subtitulo(campanha: dict) -> str:
    palavra = str(campanha.get("palavras_chave") or "").split(",")[0].strip()
    url = str(campanha.get("url_post") or "")
    for prefixo in ("https://", "http://"):
        if url.startswith(prefixo):
            url = url[len(prefixo):]
    if len(url) > 42:
        url = url[:40] + "…"
    if palavra and url:
        return f"{palavra}  ·  {url}"
    return palavra or url or "Sem post"


class Dialogo(ctk.CTkToplevel):
    def __init__(self, master, titulo: str, texto: str, rotulos: list[str], confirmar: str = "Criar"):
        super().__init__(master)
        self.title(titulo)
        self.resizable(False, False)
        self.configure(fg_color=COR_PAINEL)
        self.resultado = None
        self.entries: list[ctk.CTkEntry] = []

        ctk.CTkLabel(
            self, text=titulo, font=ctk.CTkFont(size=18, weight="bold"), anchor="w"
        ).pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            self, text=texto, wraplength=440, justify="left", text_color=COR_MUTED, anchor="w"
        ).pack(fill="x", padx=18, pady=(0, 12))

        for rotulo in rotulos:
            ctk.CTkLabel(self, text=rotulo, anchor="w", text_color=COR_MUTED).pack(fill="x", padx=18)
            entrada = ctk.CTkEntry(self, width=440, height=36)
            entrada.pack(fill="x", padx=18, pady=(2, 10))
            self.entries.append(entrada)

        botoes = ctk.CTkFrame(self, fg_color="transparent")
        botoes.pack(fill="x", padx=18, pady=(4, 16))
        ctk.CTkButton(
            botoes, text="Cancelar", width=110, fg_color="transparent",
            border_width=1, border_color=COR_LINHA, command=self._cancelar,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            botoes, text=confirmar, width=110, fg_color=COR_OK, hover_color=COR_OK_HOVER,
            command=self._ok,
        ).pack(side="right")

        self.transient(master)
        self.grab_set()
        if self.entries:
            self.entries[0].focus()
        self.bind("<Escape>", lambda _e: self._cancelar())
        self.bind("<Return>", lambda _e: self._ok())
        self.protocol("WM_DELETE_WINDOW", self._cancelar)

    def _ok(self):
        self.resultado = [entrada.get().strip() for entrada in self.entries]
        self.grab_release()
        self.destroy()

    def _cancelar(self):
        self.resultado = None
        self.grab_release()
        self.destroy()


def perguntar(master, titulo: str, texto: str, rotulos: list[str], confirmar: str = "Criar"):
    dialogo = Dialogo(master, titulo, texto, rotulos, confirmar)
    master.wait_window(dialogo)
    return dialogo.resultado


class RunnerCampanha:
    def __init__(self, campanha_id: str):
        self.campanha_id = campanha_id
        self.processo: subprocess.Popen | None = None
        self.logs: deque[str] = deque(maxlen=MAX_LINHAS_LOG)
        self.ao_receber_log = None
        self.ao_encerrar = None

    @property
    def rodando(self) -> bool:
        return self.processo is not None and self.processo.poll() is None

    def iniciar(self) -> None:
        if self.rodando:
            return
        script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "automacao_linkedin", "run_bot.py"
        )
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.processo = subprocess.Popen(
            [sys.executable, "-u", script, f"--conta={self.campanha_id}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=sm.ambiente_para_conta(self.campanha_id),
            creationflags=flags,
        )
        threading.Thread(target=self._ler_saida, args=(self.processo,), daemon=True).start()

    def _ler_saida(self, processo: subprocess.Popen) -> None:
        try:
            for linha in iter(processo.stdout.readline, b""):
                texto = linha.decode("utf-8", errors="replace").rstrip()
                if not texto:
                    continue
                self.logs.append(texto)
                if self.ao_receber_log:
                    self.ao_receber_log(texto)
        except Exception:
            pass
        finally:
            try:
                processo.stdout.close()
            except Exception:
                pass
            processo.wait()
            if self.ao_encerrar:
                self.ao_encerrar()

    def parar(self) -> None:
        processo = self.processo
        if processo is None:
            return
        self.processo = None
        if processo.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(processo.pid)],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
            else:
                processo.terminate()
        except Exception:
            try:
                processo.kill()
            except Exception:
                pass


class PaginaCampanha(ctk.CTkFrame):
    def __init__(self, master, app: "App", campanha_id: str):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.campanha_id = campanha_id
        self.dados = repositorio.obter_campanha(campanha_id) or {}
        self.visivel = False
        self._job_metricas = None
        self.menu_aberto = False

        self.runner = app.runner(campanha_id)
        self.runner.ao_receber_log = self._log_do_processo
        self.runner.ao_encerrar = self._processo_encerrou

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._montar()
        for linha in self.runner.logs:
            self._escrever_log(linha)
        self._atualizar_estado_botoes()

    def _montar(self):
        barra = ctk.CTkFrame(self, fg_color="transparent")
        barra.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        barra.grid_columnconfigure(2, weight=1)

        self.lbl_nome = ctk.CTkLabel(
            barra, text=self.dados.get("nome") or self.campanha_id,
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        self.lbl_nome.grid(row=0, column=0, sticky="w")
        self.lbl_perfil = ctk.CTkLabel(
            barra,
            text=f"  {self.dados.get('perfil_nome') or ''}  ",
            fg_color="#3A2A16",
            text_color="#F0C48A",
            corner_radius=12,
        )
        self.lbl_perfil.grid(row=0, column=1, padx=10)
        self.lbl_status = ctk.CTkLabel(barra, text="Parada", text_color=COR_MUTED)
        self.lbl_status.grid(row=0, column=2, sticky="w")

        acoes = ctk.CTkFrame(barra, fg_color="transparent")
        acoes.grid(row=0, column=3, sticky="e")
        self.btn_salvar = ctk.CTkButton(acoes, text="Salvar", width=90, command=self.salvar)
        self.btn_salvar.pack(side="left", padx=4)
        self.btn_iniciar = ctk.CTkButton(
            acoes, text="Iniciar", width=100, fg_color=COR_OK, hover_color=COR_OK_HOVER,
            command=self.iniciar,
        )
        self.btn_iniciar.pack(side="left", padx=4)
        self.btn_parar = ctk.CTkButton(
            acoes, text="Parar", width=90, fg_color=COR_PARAR, hover_color=COR_PARAR_HOVER,
            command=self.parar, state="disabled",
        )
        self.btn_parar.pack(side="left", padx=4)
        self.btn_menu = ctk.CTkButton(acoes, text="···", width=42, fg_color=COR_CARD, command=self._alternar_menu)
        self.btn_menu.pack(side="left", padx=(4, 0))

        self.menu = ctk.CTkFrame(self, fg_color="#222B38", border_width=1, border_color=COR_LINHA, corner_radius=10)
        for texto, comando, cor in (
            ("Duplicar", self.duplicar, COR_TEXTO),
            ("Renomear", self.renomear, COR_TEXTO),
            ("Limpar histórico deste post", self.limpar_historico, COR_TEXTO),
            ("Excluir", self.excluir, "#FFB0AA"),
        ):
            ctk.CTkButton(
                self.menu, text=texto, anchor="w", fg_color="transparent", text_color=cor,
                hover_color="#2C3848", command=comando,
            ).pack(fill="x", padx=6, pady=2)

        faixa = ctk.CTkFrame(self, fg_color="transparent")
        faixa.grid(row=1, column=0, sticky="ew", padx=16, pady=4)
        faixa.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.cards = {
            "respondidos": self._card(faixa, "Respondidos hoje", 0),
            "reconhecidos": self._card(faixa, "Reconhecidos", 1),
            "editados": self._card(faixa, "Editados", 2),
            "pulados": self._card(faixa, "Pulados", 3),
        }

        corpo = ctk.CTkFrame(self, fg_color="transparent")
        corpo.grid(row=2, column=0, sticky="nsew", padx=16, pady=(4, 14))
        corpo.grid_columnconfigure((0, 1), weight=1)
        corpo.grid_rowconfigure(0, weight=1)

        form = ctk.CTkFrame(corpo, fg_color=COR_PAINEL, corner_radius=14, border_width=1, border_color=COR_LINHA)
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        form.grid_columnconfigure(0, weight=1)
        perfil = self.dados.get("perfil_nome") or "perfil"
        ctk.CTkLabel(
            form,
            text=f"Cookie e mensagens vêm do perfil {perfil}.",
            text_color=COR_MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        self.entry_url = self._campo(form, 1, "URL do post", self.dados.get("url_post") or "")
        self.entry_palavras = self._campo(form, 3, "Palavras-chave", self.dados.get("palavras_chave") or "")
        ctk.CTkLabel(form, text="Posts extras", anchor="w", text_color=COR_MUTED).grid(
            row=5, column=0, sticky="w", padx=14, pady=(8, 2)
        )
        self.txt_extras = ctk.CTkTextbox(form, height=120)
        self.txt_extras.grid(row=6, column=0, sticky="ew", padx=14, pady=(0, 14))
        self.txt_extras.insert("1.0", self.dados.get("urls_extras") or "")

        logs = ctk.CTkFrame(corpo, fg_color=COR_PAINEL, corner_radius=14, border_width=1, border_color=COR_LINHA)
        logs.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        logs.grid_columnconfigure(0, weight=1)
        logs.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(logs, text="Logs desta campanha", text_color=COR_MUTED, anchor="w").grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 4)
        )
        self.box_logs = ctk.CTkTextbox(logs, font=("Consolas", 12), fg_color="#0C1118")
        self.box_logs.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.box_logs.configure(state="disabled")

    def _card(self, master, titulo: str, coluna: int):
        frame = ctk.CTkFrame(master, fg_color=COR_CARD, corner_radius=12, border_width=1, border_color=COR_LINHA)
        frame.grid(row=0, column=coluna, sticky="ew", padx=4)
        ctk.CTkLabel(frame, text=titulo.upper(), text_color=COR_MUTED, font=ctk.CTkFont(size=11)).pack(
            anchor="w", padx=12, pady=(8, 0)
        )
        valor = ctk.CTkLabel(frame, text="0", font=ctk.CTkFont(size=26, weight="bold"), text_color=COR_AZUL)
        valor.pack(anchor="w", padx=12, pady=(0, 8))
        return valor

    def _campo(self, master, linha: int, titulo: str, valor: str):
        ctk.CTkLabel(master, text=titulo, anchor="w", text_color=COR_MUTED).grid(
            row=linha, column=0, sticky="w", padx=14, pady=(6, 2)
        )
        entrada = ctk.CTkEntry(master, height=36)
        entrada.insert(0, valor)
        entrada.grid(row=linha + 1, column=0, sticky="ew", padx=14)
        return entrada

    def _alternar_menu(self):
        self.menu_aberto = not self.menu_aberto
        if self.menu_aberto:
            self.menu.place(in_=self.btn_menu, relx=1, rely=1, anchor="ne", y=6)
        else:
            self.menu.place_forget()

    def _fechar_menu(self):
        self.menu_aberto = False
        self.menu.place_forget()

    def coletar(self) -> dict:
        return {
            "nome": self.lbl_nome.cget("text"),
            "url_post": self.entry_url.get().strip(),
            "palavras_chave": self.entry_palavras.get().strip(),
            "urls_extras": self.txt_extras.get("1.0", "end").strip(),
        }

    def salvar(self, silencioso: bool = False) -> bool:
        try:
            repositorio.salvar_campanha(self.campanha_id, self.coletar())
            self.dados = repositorio.obter_campanha(self.campanha_id) or self.dados
        except Exception as e:
            self._escrever_log(f"[ERRO] Não foi possível salvar: {e}")
            return False
        if not silencioso:
            self._escrever_log("[SISTEMA] Campanha salva.")
            self.btn_salvar.configure(text="Salvo", fg_color=COR_OK)
            self.after(1600, lambda: self.btn_salvar.configure(text="Salvar", fg_color=["#3B8ED0", "#1F6AA5"]))
        return True

    def renomear(self):
        self._fechar_menu()
        resposta = perguntar(
            self.app, "Renomear", "O novo nome aparece na lista.", ["Nome"], confirmar="Salvar"
        )
        if not resposta or not resposta[0]:
            return
        self.lbl_nome.configure(text=resposta[0])
        self.salvar(silencioso=True)
        self.app.atualizar_lista()

    def duplicar(self):
        self._fechar_menu()
        self.salvar(silencioso=True)
        sugestao = f"{self.lbl_nome.cget('text')} (2)"
        resposta = perguntar(
            self.app, "Duplicar campanha",
            "A cópia usa o mesmo perfil, então já entra com o cookie e as mensagens.",
            ["Nome da cópia"],
        )
        if not resposta:
            return
        nome = resposta[0] or sugestao
        self.app.criar_campanha_de(self.dados, nome)

    def excluir(self):
        self._fechar_menu()
        nome = self.lbl_nome.cget("text")
        if not messagebox.askyesno("Excluir", f"Excluir '{nome}' e o histórico dela?"):
            return
        self.app.excluir_campanha(self.campanha_id)

    def limpar_historico(self):
        self._fechar_menu()
        url = self.entry_url.get().strip()
        if not url:
            messagebox.showinfo("Histórico", "Preencha a URL do post primeiro.")
            return
        os.environ[sm.ENV_CONTA_ID] = self.campanha_id
        try:
            comentarios, perfis = historico.contar_post("", url)
        finally:
            os.environ.pop(sm.ENV_CONTA_ID, None)
        if not (comentarios or perfis):
            messagebox.showinfo("Histórico", "Este post ainda não tem histórico salvo.")
            return
        if not messagebox.askyesno(
            "Limpar histórico",
            f"Há {comentarios} comentário(s) e {perfis} perfil(is) neste post.\n\n"
            "Apagar faz a automação poder responder essas pessoas de novo. Continuar?",
        ):
            return
        os.environ[sm.ENV_CONTA_ID] = self.campanha_id
        try:
            historico.limpar_post("", url)
        finally:
            os.environ.pop(sm.ENV_CONTA_ID, None)
        self._escrever_log("[SISTEMA] Histórico deste post apagado.")

    def iniciar(self):
        if self.runner.rodando:
            return
        if not self.entry_url.get().strip():
            messagebox.showwarning("Falta a URL", "Informe a URL do post antes de iniciar.")
            return
        self.salvar(silencioso=True)
        self._escrever_log("[SISTEMA] Iniciando. Cookie e mensagens vêm do perfil.")
        try:
            self.runner.iniciar()
        except Exception as e:
            self._escrever_log(f"[ERRO] Não foi possível iniciar: {e}")
            return
        self._atualizar_estado_botoes()
        self.app.atualizar_lista()

    def parar(self):
        self._escrever_log("[SISTEMA] Encerrando automação e fechando o navegador...")
        self.runner.parar()
        self._atualizar_estado_botoes()
        self.app.atualizar_lista()

    def _processo_encerrou(self):
        def _ui():
            self._escrever_log("[SISTEMA] Automação encerrada.")
            self._atualizar_estado_botoes()
            self.app.atualizar_lista()
        try:
            if self.winfo_exists():
                self.after(0, _ui)
        except Exception:
            pass

    def _atualizar_estado_botoes(self):
        rodando = self.runner.rodando
        self.btn_iniciar.configure(state="disabled" if rodando else "normal")
        self.btn_parar.configure(state="normal" if rodando else "disabled")
        self.lbl_status.configure(
            text="Rodando" if rodando else "Parada",
            text_color=COR_OK if rodando else COR_MUTED,
        )

    def _escrever_log(self, texto: str):
        try:
            self.box_logs.configure(state="normal")
            self.box_logs.insert("end", f"{texto}\n")
            if int(self.box_logs.index("end-1c").split(".")[0]) > MAX_LINHAS_LOG:
                self.box_logs.delete("1.0", "100.0")
            self.box_logs.see("end")
            self.box_logs.configure(state="disabled")
        except Exception:
            pass

    def _log_do_processo(self, texto: str):
        if self.visivel:
            try:
                self.after(0, lambda: self._escrever_log(texto))
            except Exception:
                pass

    def ao_exibir(self):
        self.visivel = True
        self.box_logs.configure(state="normal")
        self.box_logs.delete("1.0", "end")
        self.box_logs.configure(state="disabled")
        for linha in list(self.runner.logs):
            self._escrever_log(linha)
        self._atualizar_estado_botoes()
        self._atualizar_metricas()

    def ao_ocultar(self):
        self.visivel = False
        self._fechar_menu()
        if self._job_metricas is not None:
            try:
                self.after_cancel(self._job_metricas)
            except Exception:
                pass
            self._job_metricas = None

    def _atualizar_metricas(self):
        if not self.visivel:
            return
        try:
            resumo = repositorio.resumo_do_dia(self.campanha_id)
            for chave, label in self.cards.items():
                label.configure(text=str(resumo.get(chave, 0)))
        except Exception:
            pass
        self._job_metricas = self.after(INTERVALO_METRICAS_MS, self._atualizar_metricas)


class PaginaPerfil(ctk.CTkFrame):
    def __init__(self, master, app: "App", perfil_id: str):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.perfil_id = perfil_id
        self.perfil = repositorio.obter_perfil(perfil_id) or {}
        self.sessao = repositorio.ler_sessao(perfil_id)
        self._montar()

    def _montar(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        barra = ctk.CTkFrame(self, fg_color="transparent")
        barra.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        barra.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            barra, text=f"Perfil {self.perfil.get('nome') or self.perfil_id}",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        slug = self.perfil.get("slug_linkedin") or "a detectar"
        ctk.CTkLabel(
            barra, text=f"  {slug}  ", fg_color="#3A2A16", text_color="#F0C48A", corner_radius=12
        ).grid(row=0, column=1, padx=10, sticky="w")
        ctk.CTkButton(barra, text="Voltar à campanha", fg_color=COR_CARD, command=self.app.voltar_campanha).grid(
            row=0, column=2, padx=4
        )
        ctk.CTkButton(
            barra, text="Salvar perfil", fg_color=COR_OK, hover_color=COR_OK_HOVER, command=self.salvar
        ).grid(row=0, column=3)

        corpo = ctk.CTkFrame(self, fg_color="transparent")
        corpo.grid(row=1, column=0, sticky="nsew", padx=16, pady=(4, 14))
        corpo.grid_columnconfigure((0, 1), weight=1)
        corpo.grid_rowconfigure(0, weight=1)

        esquerda = ctk.CTkFrame(corpo, fg_color=COR_PAINEL, corner_radius=14, border_width=1, border_color=COR_LINHA)
        esquerda.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        esquerda.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            esquerda,
            text="Vale para todas as campanhas deste perfil. O cookie fica só neste computador. A chave da OpenAI fica no .env e vale para todos.",
            text_color=COR_MUTED, wraplength=420, justify="left",
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        self.entry_li = self._senha(esquerda, 1, "Cookie li_at", self.sessao.get("li_at") or "")
        self.entry_js = self._senha(esquerda, 3, "JSESSIONID", self.sessao.get("jsessionid") or "")
        ctk.CTkLabel(esquerda, text="Navegador", anchor="w", text_color=COR_MUTED).grid(
            row=5, column=0, sticky="w", padx=14, pady=(8, 2)
        )
        self.combo_nav = ctk.CTkComboBox(esquerda, values=["edge", "chrome", "brave", "firefox"])
        self.combo_nav.set(self.perfil.get("navegador") or "edge")
        self.combo_nav.grid(row=6, column=0, sticky="ew", padx=14)
        self.entry_slug = self._senha(esquerda, 7, "Forçar perfil (/in/), opcional", self.perfil.get("slug_manual") or "")
        self.entry_slug.configure(show="")

        direita = ctk.CTkFrame(corpo, fg_color=COR_PAINEL, corner_radius=14, border_width=1, border_color=COR_LINHA)
        direita.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        direita.grid_columnconfigure(0, weight=1)
        direita.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(direita, text="Mensagens sorteadas", anchor="w", text_color=COR_MUTED).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 2)
        )
        self.txt_msgs = ctk.CTkTextbox(direita)
        self.txt_msgs.grid(row=1, column=0, sticky="nsew", padx=14)
        self.txt_msgs.insert("1.0", self.perfil.get("respostas_simples") or "")
        ctk.CTkLabel(direita, text="Mensagem de DM", anchor="w", text_color=COR_MUTED).grid(
            row=2, column=0, sticky="w", padx=14, pady=(10, 2)
        )
        self.txt_dm = ctk.CTkTextbox(direita, height=90)
        self.txt_dm.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 14))
        self.txt_dm.insert("1.0", self.perfil.get("mensagem_perfil_template") or "")

    def _senha(self, master, linha: int, titulo: str, valor: str):
        ctk.CTkLabel(master, text=titulo, anchor="w", text_color=COR_MUTED).grid(
            row=linha, column=0, sticky="w", padx=14, pady=(8, 2)
        )
        entrada = ctk.CTkEntry(master, height=36, show="*")
        if valor:
            entrada.insert(0, valor)
        entrada.grid(row=linha + 1, column=0, sticky="ew", padx=14)
        return entrada

    def salvar(self):
        try:
            repositorio.salvar_perfil(
                self.perfil_id,
                {
                    "nome": self.perfil.get("nome") or self.perfil_id,
                    "slug_manual": self.entry_slug.get().strip(),
                    "respostas_simples": self.txt_msgs.get("1.0", "end").strip(),
                    "mensagem_perfil_template": self.txt_dm.get("1.0", "end").strip(),
                    "resposta_publica_dm_sucesso": self.perfil.get("resposta_publica_dm_sucesso") or "",
                    "resposta_publica_dm_falha": self.perfil.get("resposta_publica_dm_falha") or "",
                    "navegador": self.combo_nav.get().strip() or "edge",
                    "perfil_navegador_subpasta": self.perfil.get("perfil_navegador_subpasta") or "Default",
                },
            )
            repositorio.salvar_sessao(self.perfil_id, self.entry_li.get().strip(), self.entry_js.get().strip())
        except Exception as e:
            messagebox.showerror("Perfil", f"Não foi possível salvar: {e}")
            return
        messagebox.showinfo("Perfil", "Perfil salvo. As campanhas dele passam a usar estes dados.")
        self.app.atualizar_lista()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Resposta de Iscas")
        self.geometry("1400x860+40+30")
        self.minsize(1100, 700)
        self.configure(fg_color=COR_FUNDO)

        self.runners: dict[str, RunnerCampanha] = {}
        self.paginas: dict[str, PaginaCampanha] = {}
        self.botoes: dict[str, ctk.CTkButton] = {}
        self.perfil_atual = ""
        self.campanha_atual = ""
        self.pagina_perfil: PaginaPerfil | None = None
        self._busca_job = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._montar_lateral()
        self.area = ctk.CTkFrame(self, fg_color=COR_FUNDO)
        self.area.grid(row=0, column=1, sticky="nsew")
        self.area.grid_columnconfigure(0, weight=1)
        self.area.grid_rowconfigure(0, weight=1)

        try:
            repositorio.garantir_schema()
            perfis = repositorio.listar_perfis()
        except Exception as e:
            messagebox.showerror("Banco", f"Não consegui abrir o Neon:\n{e}")
            raise
        preferido = repositorio.ler_config("ui_perfil") or "paulo"
        self.perfil_atual = preferido if any(p["id"] == preferido for p in perfis) else (perfis[0]["id"] if perfis else "")
        self._desenhar_pills()
        self.atualizar_lista()
        campanhas = self._campanhas_visiveis()
        preferida = repositorio.ler_config("ui_campanha")
        if any(c["id"] == preferida for c in campanhas):
            self.abrir_campanha(preferida)
        elif campanhas:
            self.abrir_campanha(campanhas[0]["id"])

        self.protocol("WM_DELETE_WINDOW", self.ao_fechar)
        self.after(2000, self._pulso)

    def _montar_lateral(self):
        lateral = ctk.CTkFrame(self, width=340, fg_color=COR_LATERAL, corner_radius=0)
        lateral.grid(row=0, column=0, sticky="nsew")
        lateral.grid_propagate(False)
        lateral.grid_columnconfigure(0, weight=1)
        lateral.grid_rowconfigure(4, weight=1)

        topo = ctk.CTkFrame(lateral, fg_color="transparent")
        topo.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        topo.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(topo, text="CAMPANHAS", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            topo, text="+ Nova", width=90, fg_color=COR_OK, hover_color=COR_OK_HOVER, command=self.nova_campanha
        ).grid(row=0, column=1)

        busca = ctk.CTkFrame(lateral, fg_color="#0E141C", border_width=1, border_color=COR_LINHA, corner_radius=10)
        busca.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))
        busca.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(busca, text="⌕", width=28, text_color=COR_MUTED, font=ctk.CTkFont(size=18)).grid(
            row=0, column=0, padx=(8, 0)
        )
        self.entry_busca = ctk.CTkEntry(
            busca, height=36, border_width=0, fg_color="#0E141C", placeholder_text="Buscar campanha..."
        )
        self.entry_busca.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=2)
        self.entry_busca.bind("<KeyRelease>", self._agendar_busca)

        self.pills = ctk.CTkFrame(lateral, fg_color="transparent")
        self.pills.grid(row=2, column=0, sticky="ew", padx=12)

        resumo = ctk.CTkFrame(lateral, fg_color="transparent")
        resumo.grid(row=3, column=0, sticky="ew", padx=16, pady=(8, 4))
        resumo.grid_columnconfigure(0, weight=1)
        self.lbl_contagem = ctk.CTkLabel(resumo, text="", text_color=COR_MUTED, font=ctk.CTkFont(size=12))
        self.lbl_contagem.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            resumo, text="Editar perfil", width=110, height=24, fg_color="transparent",
            text_color=COR_AZUL, hover_color=COR_CARD, command=self.editar_perfil,
        ).grid(row=0, column=1)

        self.lista = ctk.CTkScrollableFrame(lateral, fg_color="transparent")
        self.lista.grid(row=4, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self.lista.grid_columnconfigure(0, weight=1)

    def _agendar_busca(self, _event=None):
        if self._busca_job:
            self.after_cancel(self._busca_job)
        self._busca_job = self.after(180, self.atualizar_lista)

    def _desenhar_pills(self):
        for widget in self.pills.winfo_children():
            widget.destroy()
        self.pills.grid_columnconfigure((0, 1), weight=1)
        perfis = repositorio.listar_perfis()
        for indice, perfil in enumerate(perfis):
            ativo = perfil["id"] == self.perfil_atual
            botao = ctk.CTkButton(
                self.pills,
                text=f"{perfil['nome']} ({perfil['campanhas']})",
                height=30,
                fg_color=COR_AMBER if ativo else "transparent",
                text_color="#1A1208" if ativo else COR_MUTED,
                border_width=0 if ativo else 1,
                border_color=COR_LINHA,
                hover_color="#C97828" if ativo else COR_CARD,
                command=lambda pid=perfil["id"]: self.trocar_perfil(pid),
            )
            botao.grid(row=indice // 2, column=indice % 2, padx=3, pady=2, sticky="ew")
        ctk.CTkButton(
            self.pills, text="+ Perfil", height=30, fg_color="transparent",
            border_width=1, border_color=COR_LINHA, text_color=COR_MUTED,
            hover_color=COR_CARD, command=self.novo_perfil,
        ).grid(row=len(perfis) // 2, column=len(perfis) % 2, padx=3, pady=2, sticky="ew")

    def _campanhas_visiveis(self) -> list[dict]:
        return repositorio.listar_campanhas(self.perfil_atual, self.entry_busca.get().strip())

    def runner(self, campanha_id: str) -> RunnerCampanha:
        if campanha_id not in self.runners:
            self.runners[campanha_id] = RunnerCampanha(campanha_id)
        return self.runners[campanha_id]

    def atualizar_lista(self):
        campanhas = self._campanhas_visiveis()
        for widget in self.lista.winfo_children():
            widget.destroy()
        self.botoes.clear()
        if not campanhas:
            ctk.CTkLabel(
                self.lista, text="Nenhuma campanha com esse filtro.", text_color=COR_MUTED
            ).grid(row=0, column=0, pady=20)
        for linha, campanha in enumerate(campanhas):
            selecionada = campanha["id"] == self.campanha_atual and self.pagina_perfil is None
            rodando = self.runner(campanha["id"]).rodando
            botao = ctk.CTkButton(
                self.lista,
                text=f"{'●' if rodando else '○'}  {campanha['nome']}\n{_subtitulo(campanha)}",
                anchor="w",
                height=52,
                fg_color="#243044" if selecionada else "transparent",
                border_width=1 if selecionada else 0,
                border_color=COR_AMBER if selecionada else COR_LINHA,
                text_color=COR_TEXTO,
                hover_color="#1A222D",
                command=lambda cid=campanha["id"]: self.abrir_campanha(cid),
            )
            botao.grid(row=linha, column=0, sticky="ew", pady=2)
            self.botoes[campanha["id"]] = botao
        rodando = sum(1 for c in campanhas if self.runner(c["id"]).rodando)
        self.lbl_contagem.configure(text=f"{len(campanhas)} campanha(s) · {rodando} rodando")
        self._desenhar_pills()

    def trocar_perfil(self, perfil_id: str):
        if perfil_id == self.perfil_atual and self.pagina_perfil is None:
            return
        self._guardar_pagina_atual()
        self.perfil_atual = perfil_id
        self._ocultar_perfil()
        campanhas = self._campanhas_visiveis()
        self.campanha_atual = ""
        self.atualizar_lista()
        if campanhas:
            self.abrir_campanha(campanhas[0]["id"])

    def abrir_campanha(self, campanha_id: str):
        if campanha_id == self.campanha_atual and self.pagina_perfil is None:
            return
        self._guardar_pagina_atual()
        self._ocultar_perfil()
        for cid, pagina in self.paginas.items():
            if cid == campanha_id:
                continue
            pagina.ao_ocultar()
            pagina.grid_remove()
        if campanha_id not in self.paginas:
            self.paginas[campanha_id] = PaginaCampanha(self.area, self, campanha_id)
        pagina = self.paginas[campanha_id]
        pagina.grid(row=0, column=0, sticky="nsew")
        pagina.ao_exibir()
        self.campanha_atual = campanha_id
        self.perfil_atual = str((pagina.dados or {}).get("perfil_id") or self.perfil_atual)
        self.atualizar_lista()

    def _guardar_pagina_atual(self):
        pagina = self.paginas.get(self.campanha_atual)
        if pagina and self.pagina_perfil is None:
            pagina.salvar(silencioso=True)

    def _ocultar_perfil(self):
        if self.pagina_perfil is not None:
            self.pagina_perfil.destroy()
            self.pagina_perfil = None

    def editar_perfil(self):
        if not self.perfil_atual:
            return
        self._guardar_pagina_atual()
        if self.campanha_atual and self.campanha_atual in self.paginas:
            self.paginas[self.campanha_atual].ao_ocultar()
            self.paginas[self.campanha_atual].grid_remove()
        self._ocultar_perfil()
        self.pagina_perfil = PaginaPerfil(self.area, self, self.perfil_atual)
        self.pagina_perfil.grid(row=0, column=0, sticky="nsew")
        self.atualizar_lista()

    def voltar_campanha(self):
        self._ocultar_perfil()
        if self.campanha_atual and self.campanha_atual in self.paginas:
            pagina = self.paginas[self.campanha_atual]
            pagina.grid(row=0, column=0, sticky="nsew")
            pagina.ao_exibir()
        self.atualizar_lista()

    def nova_campanha(self):
        if not self.perfil_atual:
            messagebox.showinfo("Campanha", "Crie um perfil antes.")
            return
        perfil = repositorio.obter_perfil(self.perfil_atual) or {}
        resposta = perguntar(
            self,
            "Nova campanha",
            f"Entra no perfil {perfil.get('nome') or self.perfil_atual} já com o cookie e as mensagens dele.",
            ["Nome", "URL do post", "Palavras-chave"],
        )
        if not resposta or not resposta[0]:
            return
        nome, url, palavras = (resposta + ["", "", ""])[:3]
        try:
            campanha_id = repositorio.criar_campanha(self.perfil_atual, nome, url, palavras)
        except Exception as e:
            messagebox.showerror("Campanha", f"Não foi possível criar: {e}")
            return
        self._copiar_navegador(self.perfil_atual, campanha_id)
        self.atualizar_lista()
        self.abrir_campanha(campanha_id)

    def criar_campanha_de(self, origem: dict, nome: str):
        try:
            campanha_id = repositorio.criar_campanha(
                origem["perfil_id"],
                nome,
                origem.get("url_post") or "",
                origem.get("palavras_chave") or "",
            )
            if origem.get("urls_extras"):
                repositorio.salvar_campanha(
                    campanha_id,
                    {
                        "nome": nome,
                        "url_post": origem.get("url_post") or "",
                        "palavras_chave": origem.get("palavras_chave") or "",
                        "urls_extras": origem.get("urls_extras") or "",
                    },
                )
        except Exception as e:
            messagebox.showerror("Duplicar", f"Não foi possível duplicar: {e}")
            return
        copia = sm.copiar_perfil_navegador(origem["id"], campanha_id)
        self.atualizar_lista()
        self.abrir_campanha(campanha_id)
        if copia == "sem_origem":
            self.paginas[campanha_id]._escrever_log(
                "[SISTEMA] A campanha original ainda não tem pasta de navegador. O cookie do perfil será usado."
            )

    def _copiar_navegador(self, perfil_id: str, campanha_id: str):
        origem = sm.origem_navegador_perfil(perfil_id, ignorar=campanha_id)
        if not origem:
            return
        sm.copiar_perfil_navegador(origem, campanha_id)

    def novo_perfil(self):
        resposta = perguntar(
            self,
            "Novo perfil",
            "Um perfil guarda o login e as mensagens. As campanhas dele herdam isso. O cookie começa vazio.",
            ["Nome do perfil"],
        )
        if not resposta or not resposta[0]:
            return
        base = repositorio.obter_perfil(self.perfil_atual) if self.perfil_atual else None
        try:
            perfil_id = repositorio.criar_perfil(resposta[0], base)
        except Exception as e:
            messagebox.showerror("Perfil", f"Não foi possível criar: {e}")
            return
        self.perfil_atual = perfil_id
        self.campanha_atual = ""
        self._ocultar_perfil()
        self.atualizar_lista()
        self.editar_perfil()

    def excluir_campanha(self, campanha_id: str):
        runner = self.runners.pop(campanha_id, None)
        if runner:
            runner.ao_receber_log = None
            runner.ao_encerrar = None
            runner.parar()
        pagina = self.paginas.pop(campanha_id, None)
        if pagina:
            pagina.ao_ocultar()
            pagina.destroy()
        try:
            repositorio.excluir_campanha(campanha_id)
        except Exception as e:
            messagebox.showerror("Excluir", f"Não foi possível excluir no banco: {e}")
            return
        sm.remover_pasta_navegador(campanha_id)
        self.campanha_atual = ""
        restantes = self._campanhas_visiveis()
        self.atualizar_lista()
        if restantes:
            self.abrir_campanha(restantes[0]["id"])

    def _pulso(self):
        try:
            for campanha_id, botao in self.botoes.items():
                if not botao.winfo_exists():
                    continue
                rodando = self.runner(campanha_id).rodando
                texto = botao.cget("text")
                marcador = "●" if rodando else "○"
                if texto[:1] in ("●", "○") and texto[:1] != marcador:
                    botao.configure(text=marcador + texto[1:])
        except Exception:
            pass
        self.after(2000, self._pulso)

    def ao_fechar(self):
        for pagina in self.paginas.values():
            try:
                pagina.salvar(silencioso=True)
            except Exception:
                pass
        try:
            repositorio.gravar_config("ui_perfil", self.perfil_atual)
            repositorio.gravar_config("ui_campanha", self.campanha_atual)
        except Exception:
            pass
        ativos = [r for r in self.runners.values() if r.rodando]
        if ativos and not messagebox.askyesno(
            "Fechar", f"{len(ativos)} automação(ões) ainda em execução. Encerrar tudo e fechar?"
        ):
            return
        for runner in self.runners.values():
            try:
                runner.ao_receber_log = None
                runner.ao_encerrar = None
                runner.parar()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
