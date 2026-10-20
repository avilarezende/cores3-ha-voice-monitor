# Manual: criar, configurar e executar o monitor no M5Stack CoreS3

Este manual cobre a parte do dispositivo: preparar o assistente de voz do Home Assistant, compilar
o firmware ESPHome, gravá-lo no **M5Stack CoreS3** (ESP32-S3) e conectá-lo ao Home Assistant.
A parte do servidor (app Sound Monitor, reações e ajuste da detecção) está em
[manual-app-ha-os.md](manual-app-ha-os.md). O firmware do **M5Stack StickS3**
(só o monitor, sem Assist) está no [README](../README.md). O ganho do microfone
desse firmware (`mic_gain`, e o registrador 0x16 na auto-recuperação do ES8311) é **30 dB**.
A tela do StickS3 apaga sozinha; a seção [Tela do StickS3](#tela-do-sticks3) descreve o ponto vermelho, as entidades e o pacote que a acorda.

## O que o firmware faz

- **Assistente de voz:** transmite o áudio do microfone continuamente para o pipeline do Assist.
  O Home Assistant detecta a palavra de ativação (por exemplo, "Ok Nabu"), transcreve o comando,
  executa e responde pelo alto-falante. A tela mostra o estado (ouvindo, pensando, respondendo).
- **Monitor de sons:** envia o mesmo áudio, por UDP, para o app Sound Monitor, que procura sons perigosos.
- **Entidades no Home Assistant:**

| Entidade | Para que serve |
|---|---|
| Monitor de sons perigosos (switch) | Liga/desliga o envio de áudio ao monitor sem afetar a voz |
| Monitor de sons transmitindo (diagnóstico) | Ligada quando o áudio está de fato saindo para o monitor |
| Microphone Mute (switch) | Muta o microfone para tudo: voz e monitor |
| Última transcrição / Última resposta | Texto do último comando e da resposta, para usar em automações |
| Wake word engine location | Fica fixado em "In Home Assistant" (detecção da palavra no servidor) |
| Battery Level, LCD Backlight etc. | Vêm do pacote base da M5Stack |

## Requisitos

- **M5Stack CoreS3** e um cabo **USB-C de dados**. Cabos só de carga não funcionam.
- Home Assistant OS com o app **Sound Monitor** instalado (veja o outro manual).
- App **ESPHome Device Builder** na versão **2026.1 ou mais recente**. O firmware foi validado com a 2026.9.1.
- Navegador **Chrome ou Edge** num computador com porta USB, para a primeira gravação.
- Rede Wi-Fi de **2,4 GHz**: o ESP32-S3 não usa 5 GHz.

## 1. Preparar o assistente de voz no Home Assistant

O CoreS3 só envia o áudio. Quem reconhece a fala e responde é o pipeline do Assist.

1. Em **Configurações → Apps → Instalar app**, instale e inicie estes três apps oficiais:
   - **openWakeWord**: detecta a palavra de ativação.
   - **Whisper**: converte fala em texto. Na aba Configuração, defina `language: pt`. Em
     máquinas modestas (Raspberry Pi), use um modelo pequeno, como `base-int8` ou `small-int8`.
   - **Piper**: converte texto em fala. Na aba Configuração, escolha uma voz `pt_BR`, por exemplo `pt_BR-faber-medium`.
2. Em **Configurações → Dispositivos e serviços**, a integração **Wyoming Protocol** aparece como
   descoberta para cada um dos três. Clique em **Configurar** em todos.
3. Em **Configurações → Assistentes de voz → Adicionar assistente**:
   - Idioma: **Português (Brasil)**
   - Agente de conversação: **Home Assistant**, ou um LLM, se você tiver
   - Fala para texto: **faster-whisper**
   - Texto para fala: **piper**, com a voz em português
4. Salve e marque esse assistente como **preferido**.

## 2. Instalar o ESPHome Device Builder

1. Em **Configurações → Apps → Instalar app**, instale o **ESPHome Device Builder**.
2. Ative **Mostrar na barra lateral** e inicie o app.

Os arquivos de configuração do ESPHome ficam em `/config/esphome/` do Home Assistant.

## 3. Copiar o projeto para o ESPHome

Baixe o repositório: em
[github.com/avilarezende/cores3-ha-voice-monitor](https://github.com/avilarezende/cores3-ha-voice-monitor),
clique em **Code → Download ZIP**. Copie a pasta `esphome/` do ZIP para `/config/esphome/`,
mantendo a estrutura:

```text
/config/esphome/
├── cores3-assist.yaml
├── utf8_truncate.h
├── secrets.yaml                    (você cria no passo 4)
└── components/
    └── audio_udp_stream/
        ├── __init__.py
        ├── audio_udp_stream.cpp
        └── audio_udp_stream.h
```

Para copiar, use o app **Studio Code Server** (arrastar e soltar na árvore de arquivos) ou o app
**Samba share** (a pasta aparece como `\\<ip-do-ha>\config\esphome` no Windows).

O `cores3-assist.yaml` depende de `components/` e de `utf8_truncate.h` na mesma pasta. Copiar só
o YAML faz a compilação falhar.

## 4. Configurar os segredos

No ESPHome Device Builder, clique em **Secrets** (canto superior direito). Isso edita
`/config/esphome/secrets.yaml`. Acrescente estas chaves; o modelo está em `secrets.example.yaml`:

```yaml
wifi_ssid: "NomeDaSuaRede"
wifi_password: "senha-do-wifi"
fallback_ap_password: "senha-do-ponto-de-acesso-de-emergência"
api_encryption_key: "CHAVE_BASE64_DE_32_BYTES"
ota_password: "senha-para-atualizações-pela-rede"
```

Para gerar a `api_encryption_key` no Windows (PowerShell):

```powershell
$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b)
```

Guarde a chave: o Home Assistant vai pedi-la ao adicionar o dispositivo. O `fallback_ap_password`
precisa ter pelo menos 8 caracteres. Ele protege a rede "CoreS3 Assist Fallback", que o CoreS3 cria
quando não consegue se conectar ao seu Wi-Fi.

## 5. Ajustar o endereço do monitor

Abra `cores3-assist.yaml` no ESPHome Device Builder (**Edit** no cartão do dispositivo) e ajuste as
substituições no topo:

```yaml
substitutions:
  name: cores3-assist
  friendly_name: CoreS3 Assist
  sound_monitor_host: 192.168.1.100   # IP do Home Assistant OS
  sound_monitor_port: "5050"
```

- **`sound_monitor_host`**: o IP do Home Assistant, que está em **Configurações → Sistema → Rede**.
  Precisa ser IP: nomes como `homeassistant.local` não funcionam. Reserve esse IP no roteador
  (DHCP fixo) para ele não mudar.
- **`sound_monitor_port`**: só mude se tiver trocado a porta na aba Rede do app.
- **`name` / `friendly_name`**: opcionais. O ESPHome acrescenta ao nome um sufixo com o fim do
  endereço MAC, então dá para ter vários CoreS3 com a mesma configuração.

## 6. Compilar o firmware

No ESPHome Device Builder, no cartão **cores3-assist**:

1. Menu **⋮ → Validate**. Deve terminar com `Configuration is valid!`.
2. **Install → Manual download**. A compilação começa.
3. Ao terminar, escolha **Factory format** para baixar o arquivo `.bin` completo.

A primeira compilação baixa o ESP-IDF, as imagens da tela e os modelos de palavra de ativação.
Num Raspberry Pi 4 ela pode levar de 20 a 40 minutos, e num mini-PC uns 5. As seguintes são bem mais rápidas.

### Alternativa: compilar no PC com Docker

Se o Home Assistant estiver num Raspberry Pi com pouca memória, compile no PC. Na raiz do
repositório, depois de criar `esphome/secrets.yaml` e ajustar o IP:

```powershell
docker run --rm -v "${PWD}\esphome:/config" -v esphome-cache:/cache esphome/esphome:2026.9.1 compile cores3-assist.yaml
```

O arquivo para gravação fica em `esphome/.esphome/build/cores3-assist/build/firmware.factory.bin`.

## 7. Gravar o firmware pela primeira vez (USB)

A primeira gravação precisa ser por cabo, porque o firmware de fábrica do CoreS3 não é ESPHome.

1. Conecte o CoreS3 ao computador pelo cabo USB-C.
2. **Entre no modo de gravação:** segure o botão **RST** do CoreS3 por cerca de **3 segundos**,
   até o **LED verde interno acender**, e solte. O LED apaga ao soltar.
   Isso confirma que o CoreS3 está aguardando o firmware.
3. No Chrome ou Edge, abra [web.esphome.io](https://web.esphome.io) e clique em **Connect**.
   Escolha a porta serial do CoreS3 (algo como *USB JTAG/serial debug unit*).
4. Clique em **Install**, escolha o `.bin` em *Factory format* e confirme.
   **Não marque a opção de apagar o dispositivo** se quiser manter dados de uma gravação anterior.
   Na primeira vez, tanto faz.
5. Ao terminar, pressione **RST** uma vez para reiniciar.

Na tela aparece a animação de carregamento e, depois de conectar ao Wi-Fi, a imagem de "sem Home Assistant", até o passo 8.

Se a porta não aparecer, troque o cabo (provavelmente é só de carga) ou repita o modo de gravação.
No Windows, a porta aparece em **Gerenciador de Dispositivos → Portas (COM e LPT)**.

## 8. Adicionar ao Home Assistant

1. Uma notificação **Novo dispositivo descoberto: CoreS3 Assist** aparece em **Configurações**.
   Se não aparecer, vá em **Dispositivos e serviços → Adicionar integração → ESPHome** e informe o IP do CoreS3.
2. Clique em **Configurar**, informe a `api_encryption_key` do passo 4 e escolha a área (por exemplo, *Sala*).
3. Abra o dispositivo em **Configurações → Dispositivos e serviços → ESPHome → CoreS3 Assist**.
   Na seção **Configuração** da página do dispositivo, ajuste:
   - **Assistente:** o pipeline em português do passo 1, ou *Preferido*.
   - **Palavra de ativação:** uma das oferecidas pelo openWakeWord, por exemplo **ok nabu** ou **hey jarvis**.

Com o Home Assistant conectado, a tela mostra a casinha em repouso: o CoreS3 está escutando.

## 9. Verificar se tudo funciona

1. **Voz:** diga "Ok Nabu" e, depois do som de confirmação, "que horas são?". A tela passa por
   *ouvindo → pensando → respondendo*, e a entidade **Última transcrição** mostra o que foi entendido.
2. **Monitor:** na página do dispositivo, **Monitor de sons perigosos** e
   **Monitor de sons transmitindo** devem estar ligados.
3. **App:** no log do Sound Monitor deve aparecer `Novo dispositivo transmitindo: <IP do CoreS3>`.
   A partir daí, siga a seção *Testar* do [manual do app](manual-app-ha-os.md#3-testar).

## 10. Atualizações e logs

- **Atualizar o firmware:** depois da primeira gravação, use **Install → Wirelessly** no ESPHome
  Device Builder (OTA). A senha é a `ota_password`.
- **Logs ao vivo:** **Logs → Wirelessly** no cartão do dispositivo. Mensagens do monitor aparecem
  com o prefixo `audio_udp_stream`. Por exemplo, `Dropped N bytes of audio` indica Wi-Fi sem
  vazão suficiente para o áudio. O resumo da configuração aparece no boot:

  ```text
  [C][audio_udp_stream]: Audio UDP stream:
  [C][audio_udp_stream]:   Destination: 192.168.1.100:5050
  ```

- **Mudou o IP do Home Assistant?** Ajuste `sound_monitor_host` e reinstale por OTA.

## Consumo de rede e recursos

- **Rede:** cerca de 32 kB/s (256 kbit/s) constantes para o monitor, PCM 16 kHz/16 bit/mono sem codificação, e o mesmo
  para o assistente de voz enquanto ele escuta. É pouco para uma rede doméstica, mas evite Wi-Fi no limite do alcance.
- **CoreS3:** o firmware compilado usa 41% da RAM interna e 53% da flash. O buffer de áudio do
  monitor (16 KB) fica na PSRAM.

## Solução de problemas

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Compilação falha com `audio_udp_stream` não encontrado | faltou copiar `components/` | Refaça o passo 3 com a pasta completa |
| Compilação falha com `utf8_truncate.h` | faltou o arquivo `.h` | Copie-o para `/config/esphome/` |
| Erro de versão mínima | ESPHome Device Builder anterior a 2026.1 | Atualize o app |
| web.esphome.io não acha a porta | cabo só de carga, ou fora do modo de gravação | Troque o cabo; segure RST até o LED verde |
| CoreS3 cria a rede "CoreS3 Assist Fallback" | Wi-Fi errado ou de 5 GHz | Conecte-se a essa rede pelo celular, informe o Wi-Fi certo, ou corrija o `secrets.yaml` e regrave |
| Tela "sem Home Assistant" | dispositivo não adicionado, ou chave de criptografia errada | Passo 8; confira a `api_encryption_key` |
| Não reage à palavra de ativação | pipeline sem openWakeWord, ou palavra não escolhida | Passo 1 e configuração do *Assist satellite* no passo 8 |
| Entende errado o português | modelo do Whisper pequeno, ou idioma errado | `language: pt` no Whisper; teste um modelo maior |
| "Monitor de sons transmitindo" desligado | microfone mutado, HA desconectado, ou switch do monitor desligado | Confira **Microphone Mute** e **Monitor de sons perigosos** |
| Logs com `Dropped N bytes` | Wi-Fi fraco | Aproxime o CoreS3 do roteador |
| CoreS3 reinicia sozinho | fonte USB fraca | Use um carregador de pelo menos 5 V / 1 A |

## Tela do StickS3

O `esphome/sticks3.yaml` mostra logo, Wi-Fi/IP, Home Assistant, áudio e o último alerta. Sem atividade, essa imagem apaga.

O número **Tela: apagar após** define o prazo em minutos (padrão **5**, de 0,5 a 60, passo 0,5, gravado no aparelho). Esgotado o prazo, a tela fica preta com um ponto vermelho no canto superior direito, 1 s aceso e 1 s apagado. O backlight desce para `screen_dot_brightness` (**25%**). Com a imagem ligada, o brilho é `screen_on_brightness` (**70%**). Qualquer despertar zera o cronômetro.

A tela volta a mostrar a imagem quando acontece um destes:

- Toque no botão **A** (GPIO11) ou **B** (GPIO12).
- O sensor `sensor.ultimo_perigo_detectado` muda de valor.
- O **Nível do microfone** sobe pelo menos `wake_jump_db` (**15 dB**) acima do fundo. O fundo é uma média lenta e anda enquanto o nível fica a até 6 dB dele, acompanhando o ambiente calmo.
- Toque no botão **Acordar tela**.
- Chamada da ação `esphome.<nó>_acordar_tela`.

**Primeiro toque de A.** Com o ponto de gravação na tela, o primeiro toque do botão A só acorda a imagem. O toque seguinte, com a tela já ligada, liga ou desliga o **Monitor de sons perigosos**. O botão B só acorda a tela ou renova o prazo.

| Entidade | Tipo | Valores / função |
|---|---|---|
| **Estado da tela** | sensor de texto | `ligada` ou `ponto de gravação` |
| **Tela: apagar após** | número, em min, categoria configuração | Prazo até apagar; 0,5–60, padrão 5 |
| **Acordar tela** | botão | Executa a mesma ação de acordar |

No YAML a ação se chama `acordar_tela`. O Home Assistant publica `esphome.<nó>_acordar_tela`. O nome do firmware é `sticks3-monitor` com sufixo de MAC (`name_add_mac_suffix`). No aparelho de exemplo, MAC `70:04:1d:dc:4b:40`, a ação é `esphome.sticks3_monitor_dc4b40_acordar_tela`.

O arquivo [`homeassistant/packages/sticks3_tela.yaml`](../homeassistant/packages/sticks3_tela.yaml) escuta o evento `monitor_perigo` e chama essa ação (modo `queued`, até 3). Assim a tela acorda em interfone, campainha, latido e nas outras categorias, inclusive quando a categoria se repete e o sensor `ultimo_perigo_detectado` permanece igual.

Instalação do pacote (o bloco de `packages` é o mesmo da [seção de reações](manual-app-ha-os.md#2-configurar-as-reações-no-home-assistant)):

1. No `configuration.yaml`, deixe os pacotes ativos:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

2. Copie `sticks3_tela.yaml` para `/config/packages/`. Pode ficar junto de `monitor_perigo.yaml`.
3. Se o seu StickS3 tiver outra MAC, abra o arquivo e troque `esphome.sticks3_monitor_dc4b40_acordar_tela` pela ação listada em **Ferramentas de desenvolvedor → Ações** (procure `acordar_tela`).
4. **Verificar configuração** e **Reiniciar** em **Configurações → Ferramentas de desenvolvedor → YAML**.

Estas três linhas no topo de `sticks3.yaml` mudam brilho e sensibilidade do salto; um valor novo pede recompilação e OTA:

```yaml
screen_on_brightness: 70%      # imagem ligada
screen_dot_brightness: 25%     # ponto vermelho
wake_jump_db: "15.0"           # dB acima do fundo que acorda a tela
```

## Para ir além

- **Vários cômodos:** grave o mesmo firmware em mais CoreS3. O sufixo do MAC diferencia os nomes,
  e o app identifica cada um pelo IP (atributo `device` do evento).
- **Mais sons:** novas categorias são criadas só no `config.toml` do app, sem mexer no firmware
  (veja *Ajustar a detecção* no manual do app).
- **Pinagem e drivers:** vêm do pacote oficial
  [m5stack/esphome-yaml](https://github.com/m5stack/esphome-yaml), fixado num commit específico
  em `cores3-assist.yaml` para que atualizações da M5Stack não quebrem o firmware sem aviso.
