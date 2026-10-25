#!/data/data/com.termux/files/usr/bin/env python
"""
Sentinela J4 — transforma um celular Android antigo num monitor 24/7 da casa.

O que faz:
  - Detecta queda de energia (o celular sai da tomada e passa a usar a bateria)
  - Detecta queda do roteador e da internet, e avisa quando voltam
  - Envia alertas push pelo ntfy.sh; guarda os que não saírem por falta de
    internet e reenvia quando ela voltar
  - Opcional (celular com chip): alertas por SMS e comandos por SMS
    (status, bateria, log, ping)
  - Lê a tensão real da bateria e avisa se esquentar ou ficar fraca sem energia
  - Opcional (root, Samsung): limita a carga em ~70% via store_mode

Requisitos (Termux + Termux:API, instalados pelo F-Droid):
  pkg install python termux-api
Configurações pessoais: copie sentinela.exemplo.json para sentinela.json.
"""
import datetime
import json
import os
import subprocess
import time
import urllib.request

CONFIG = {
    "dono": "",                      # seu número com DDI/DDD (+55...); só ele pode mandar comandos por SMS
    "roteador": "192.168.0.1",       # IP do roteador de casa (veja com: ip route)
    "url_internet": "http://connectivitycheck.gstatic.com/generate_204",
    "usar_sms": False,               # True só se o celular tiver chip (alertas e comandos por SMS)
    "ntfy_topico": "",               # nome difícil de adivinhar, ex. "sentinela-k8w2q9zt" (assine no app ntfy)
    "intervalo": 30,                 # segundos entre checagens
    "confirmacoes": 3,               # leituras seguidas iguais antes de alertar (evita falso alarme)
    "temp_max": 45.0,                # °C da bateria para alerta de superaquecimento
    "bateria_baixa": 20,             # % para alerta quando estiver sem energia
    "tensao_baixa": 3.60,            # V; mais confiável que a % neste aparelho
    "limitar_carga": False,          # True = liga o store_mode (carga ~60-70%). Exige root. Teste antes!
}

# Configurações pessoais (tópico do ntfy, número de telefone...) ficam em
# sentinela.json, ao lado deste script. Esse arquivo NÃO vai para o Git.
ARQ_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sentinela.json")
if os.path.exists(ARQ_CONFIG):
    with open(ARQ_CONFIG) as f:
        CONFIG.update(json.load(f))

SYS_BAT = "/sys/class/power_supply/battery/"

LOG = os.path.expanduser("~/sentinela.log")
vistos = set()        # ids de SMS já processados
alertas_unicos = set()  # alertas que só disparam uma vez até a condição normalizar
pendentes = []        # alertas que não saíram por falta de internet; reenviados quando ela voltar


def sh(*cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def log(msg):
    linha = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(linha, flush=True)
    with open(LOG, "a") as f:
        f.write(linha + "\n")


def enviar_ntfy(texto, titulo="Sentinela J4"):
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{CONFIG['ntfy_topico']}",
            data=texto.encode("utf-8"), method="POST",
            headers={"Title": titulo, "Priority": "high"})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False


def reenviar_pendentes():
    if not (pendentes and CONFIG["ntfy_topico"]):
        return
    corpo = "\n".join(pendentes)
    if enviar_ntfy(corpo, titulo=f"Sentinela J4: {len(pendentes)} alerta(s) atrasado(s)"):
        log(f"{len(pendentes)} alerta(s) pendente(s) enviados.")
        pendentes.clear()


def avisar(texto):
    log("ALERTA: " + texto)
    if CONFIG["ntfy_topico"]:
        if not enviar_ntfy(texto):
            pendentes.append(f"[{datetime.datetime.now():%d/%m %H:%M}] {texto}")
            log("Sem internet para o ntfy; alerta guardado para reenvio.")
    if CONFIG["usar_sms"]:
        sh("termux-sms-send", "-n", CONFIG["dono"], texto)


# ---------- leituras ----------

def bateria():
    try:
        return json.loads(sh("termux-battery-status"))
    except ValueError:
        return {}


def ler_sys(nome):
    """Lê um arquivo do driver de bateria; se o Android bloquear, tenta via root."""
    caminho = SYS_BAT + nome
    try:
        with open(caminho) as f:
            return f.read().strip()
    except OSError:
        return sh("su", "-c", f"cat {caminho}").strip()


def tensao():
    # voltage_now desta ROM reporta errado (~2,56 V); batt_inbat_voltage vem em mV e é o valor real
    try:
        return int(ler_sys("batt_inbat_voltage")) / 1000
    except ValueError:
        return -1.0


def ping(host):
    try:
        return subprocess.run(["ping", "-c", "1", "-W", "3", host],
                              capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def internet():
    try:
        urllib.request.urlopen(CONFIG["url_internet"], timeout=8)
        return True
    except Exception:
        return False


def estado():
    b = bateria()
    return {
        "tomada": b.get("plugged", "UNPLUGGED") != "UNPLUGGED",
        "bateria": b.get("percentage", -1),
        "temp": b.get("temperature", 0.0),
        "tensao": tensao(),
        "rede_casa": ping(CONFIG["roteador"]),
        "internet": internet(),
    }


def resumo(e):
    sim = lambda v: "ok" if v else "FORA"
    return (f"Energia:{sim(e['tomada'])} Roteador:{sim(e['rede_casa'])} "
            f"Internet:{sim(e['internet'])} Bat:{e['bateria']}% {e['tensao']:.2f}V {e['temp']}C")


# ---------- comandos por SMS ----------

erro_sms_avisado = False


def listar_sms():
    """Retorna só mensagens válidas. Se o Termux:API responder com erro
    (ex.: permissão de SMS negada), registra uma vez no log e segue sem comandos."""
    global erro_sms_avisado
    if not CONFIG["usar_sms"]:
        return []
    saida = sh("termux-sms-list", "-t", "inbox", "-l", "15")
    try:
        dados = json.loads(saida)
    except ValueError:
        dados = None
    if not isinstance(dados, list):
        if not erro_sms_avisado:
            erro_sms_avisado = True
            log(f"Não consegui ler SMS (resposta: {saida.strip()[:120]!r}). Comandos por SMS desativados.")
        return []
    return [m for m in dados if isinstance(m, dict)]


def do_dono(numero):
    dono = "".join(c for c in CONFIG["dono"] if c.isdigit())[-9:]
    if len(dono) < 8:          # sem número configurado, ninguém pode mandar comandos
        return False
    digitos = "".join(c for c in (numero or "") if c.isdigit())
    return digitos.endswith(dono)


def ler_comandos():
    cmds = []
    for m in listar_sms():
        mid = m.get("_id") or (m.get("received"), m.get("number"), m.get("body"))
        if mid in vistos:
            continue
        vistos.add(mid)
        if do_dono(m.get("number")):
            cmds.append((m.get("body") or "").strip().lower())
    return cmds


def responder(cmd, e):
    if cmd.startswith("status"):
        return resumo(e)
    if cmd.startswith("bateria"):
        return (f"Bat {e['bateria']}% | {e['tensao']:.3f}V | {e['temp']}C | "
                f"tomada:{'sim' if e['tomada'] else 'nao'} | store_mode:{ler_sys('store_mode') or '?'}")
    if cmd.startswith("ping"):
        return "pong " + datetime.datetime.now().strftime("%H:%M:%S")
    if cmd.startswith("log"):
        try:
            with open(LOG) as f:
                return " | ".join(l.strip()[11:] for l in f.readlines()[-3:])[:300]
        except OSError:
            return "sem log"
    return "Comandos: status, bateria, log, ping"


# ---------- laço principal ----------

def alerta_unico(chave, condicao, texto):
    if condicao and chave not in alertas_unicos:
        alertas_unicos.add(chave)
        avisar(texto)
    elif not condicao:
        alertas_unicos.discard(chave)


def main():
    for m in listar_sms():          # ignora SMS antigos na primeira execução
        vistos.add(m.get("_id") or (m.get("received"), m.get("number"), m.get("body")))

    if CONFIG["limitar_carga"]:
        sh("su", "-c", f"echo 1 > {SYS_BAT}store_mode")
        log(f"Limite de carga ligado (store_mode={ler_sys('store_mode')})")

    atual = estado()
    log("Sentinela iniciada. " + resumo(atual))
    if CONFIG["ntfy_topico"]:
        ok = enviar_ntfy("Sentinela iniciada. " + resumo(atual))
        log("Notificação de teste enviada ao ntfy." if ok else "Não consegui falar com o ntfy agora.")
    pendente = {}  # chave -> (valor_novo, contagem)

    transicoes = [
        ("tomada", "Energia voltou", "QUEDA DE ENERGIA: celular na bateria"),
        ("rede_casa", "Roteador de casa voltou", "Roteador de casa fora do ar"),
        ("internet", "Internet voltou", "Sem internet"),
    ]

    while True:
        time.sleep(CONFIG["intervalo"])
        novo = estado()

        for chave, msg_on, msg_off in transicoes:
            if novo[chave] != atual[chave]:
                valor, cont = pendente.get(chave, (None, 0))
                cont = cont + 1 if valor == novo[chave] else 1
                pendente[chave] = (novo[chave], cont)
                if cont >= CONFIG["confirmacoes"]:
                    atual[chave] = novo[chave]
                    pendente.pop(chave)
                    avisar(f"{msg_on if novo[chave] else msg_off}. {resumo(novo)}")
            else:
                pendente.pop(chave, None)

        atual["bateria"], atual["temp"], atual["tensao"] = novo["bateria"], novo["temp"], novo["tensao"]

        alerta_unico("quente", float(novo["temp"] or 0) >= CONFIG["temp_max"],
                     f"Bateria quente ({novo['temp']}C). Verifique o aparelho.")
        fraca = (0 < novo["tensao"] <= CONFIG["tensao_baixa"]) or (0 <= novo["bateria"] <= CONFIG["bateria_baixa"])
        alerta_unico("baixa", (not novo["tomada"]) and fraca,
                     f"Bateria fraca sem energia ({novo['bateria']}%, {novo['tensao']:.2f}V). Vou desligar em breve.")

        reenviar_pendentes()

        for cmd in ler_comandos():
            resposta = responder(cmd, novo)
            log(f"comando '{cmd}' -> {resposta}")
            sh("termux-sms-send", "-n", CONFIG["dono"], resposta)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Sentinela encerrada.")
