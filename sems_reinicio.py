"""
Reinicia o inversor WEG SIW200 (GoodWe) pela nuvem SEMS quando ele trava.

Regra: entre 9h e 15h (horário de Cuiabá), se a potência de saída
estiver abaixo de 60 W, manda o inversor parar, espera 1 minuto e
manda ligar de novo.

Usa a API da SEMS Portal, que NÃO é oficial nem documentada pela GoodWe
e pode mudar sem aviso. A conta precisa ter permissão de controle remoto.

Variáveis de ambiente (configure como "Secrets" no GitHub):
  SEMS_USUARIO       e-mail/usuário do SEMS
  SEMS_SENHA         senha do SEMS
  INVERSOR_SN        (opcional) número de série do inversor; se vazio, usa o primeiro
  TELEGRAM_TOKEN     (opcional) token do bot do Telegram para avisos
  TELEGRAM_CHAT_ID   (opcional) seu chat id no Telegram
  SIMULAR            (opcional) "1" = só mostra o que faria, sem reiniciar
"""

import base64
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, time as hora
from zoneinfo import ZoneInfo

# ---------------- Configurações ----------------
FUSO = ZoneInfo("America/Cuiaba")
INICIO = hora(9, 0)
FIM = hora(15, 0)
LIMITE_W = 60
ESPERA_REINICIO_S = 500

LOGIN_URL = "https://www.semsportal.com/api/v2/Common/CrossLogin"
TOKEN_INICIAL = {
    "uid": "", "timestamp": 0, "token": "",
    "client": "web", "version": "", "language": "en",
}
# -----------------------------------------------


def b64(dados: dict) -> str:
    return base64.b64encode(json.dumps(dados).encode()).decode()


def post(url: str, corpo: dict, token: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(corpo).encode(),
        headers={"Content-Type": "application/json", "Token": b64(token)},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read().decode())
    if resp.get("hasError") or str(resp.get("code")) not in ("0", "None"):
        raise RuntimeError(f"Erro da API em {url}: {resp.get('msg')} ({resp.get('code')})")
    return resp


def avisar(msg: str) -> None:
    print(msg)
    tk, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tk and chat:
        try:
            url = f"https://api.telegram.org/bot{tk}/sendMessage"
            dados = json.dumps({"chat_id": chat, "text": msg}).encode()
            urllib.request.urlopen(
                urllib.request.Request(url, data=dados, headers={"Content-Type": "application/json"}),
                timeout=15,
            )
        except Exception as e:  # aviso não pode derrubar o script
            print(f"(Falha ao enviar Telegram: {e})")


def login(usuario: str, senha: str) -> tuple[str, dict]:
    resp = post(
        LOGIN_URL,
        {"account": usuario, "pwd": senha, "agreement_agreement": 0, "is_local": False},
        TOKEN_INICIAL,
    )
    base = resp.get("api") or "https://www.semsportal.com/api/"
    if not base.endswith("/"):
        base += "/"
    return base, resp["data"]


def main() -> int:
    agora = datetime.now(FUSO)
    if not (INICIO <= agora.time() <= FIM):
        print(f"{agora:%H:%M} fora da janela 09:00–15:00. Nada a fazer.")
        return 0

    usuario, senha = os.getenv("SEMS_USUARIO"), os.getenv("SEMS_SENHA")
    if not usuario or not senha:
        print("Defina SEMS_USUARIO e SEMS_SENHA.")
        return 1
    simular = os.getenv("SIMULAR") == "1"

    base, token = login(usuario, senha)

    usina_id = post(base + "PowerStation/GetPowerStationIdByOwner", {}, token)["data"]
    pontos = post(
        base + "v3/PowerStation/GetInverterAllPoint", {"powerStationId": usina_id}, token
    )["data"]["inverterPoints"]
    if not pontos:
        print("Nenhum inversor encontrado nessa conta.")
        return 1

    sn_alvo = os.getenv("INVERSOR_SN")
    inv = next((p for p in pontos if p["sn"] == sn_alvo), pontos[0]) if sn_alvo else pontos[0]
    potencia = float(inv.get("out_pac") or 0)

    print(
        f"{agora:%d/%m %H:%M} | {inv.get('name')} ({inv['sn']}) | "
        f"potência {potencia:.0f} W | status {inv.get('status')} | "
        f"último dado {inv.get('last_refresh_time')}"
    )

    if potencia >= LIMITE_W:
        print("Inversor gerando normalmente.")
        return 0

    if simular:
        avisar(f"[SIMULAÇÃO] Potência {potencia:.0f} W — o inversor seria reiniciado agora.")
        return 0

    avisar(f"Inversor solar: potência {potencia:.0f} W às {agora:%H:%M}. Reiniciando...")

    url_ctrl = base + "PowerStation/SaveRemoteControlInverter"
    comando = {"inverterSN": inv["sn"], "powerStationId": usina_id, "InverterStatusSettingMark": 1}

    post(url_ctrl, {**comando, "InverterStatus": 2}, token)  # 2 = parar / em espera
    print("Comando de PARAR enviado. Aguardando...")
    time.sleep(ESPERA_REINICIO_S)
    post(url_ctrl, {**comando, "InverterStatus": 1}, token)  # 1 = ligar
    print("Comando de LIGAR enviado.")

    avisar(
        "Inversor solar: comandos de reinício enviados. "
        "Ele leva 1–2 minutos para voltar a gerar; confira no SEMS+."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        avisar(f"Inversor solar: ERRO na automação — {e}")
        sys.exit(1)
