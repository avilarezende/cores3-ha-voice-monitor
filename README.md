
App **Sound Monitor** 0.3.0 (YAMNet no Home Assistant OS) e firmware ESPHome do M5Stack CoreS3 e do StickS3. Manuais: [app no HA OS](docs/manual-app-ha-os.md), [CoreS3](docs/manual-cores3-esphome.md). Histórico: [CHANGELOG.md](CHANGELOG.md).

## M5Stack StickS3 (`esphome/sticks3.yaml`)

Firmware dedicado ao **M5Stack StickS3** (ESP32-S3-PICO-1-N8R8: 8 MB flash, 8 MB PSRAM octal) que só transmite o microfone para o app Sound Monitor (UDP 5050, PCM 16 kHz/16 bit/mono, cerca de **32 kB/s**, sem codificação) e mostra uma tela de status (logo, Wi-Fi/IP, HA, áudio, último alerta). Sem atividade, a imagem apaga e fica um ponto vermelho piscando; os detalhes estão em [Tela inativa](#tela-inativa).

O microfone inicia sozinho (intervalo de 5 s) e o ES8311 recebe a sequência de enable do M5Unified. O ganho do ADC (`mic_gain`) fica em **30 dB**. A tela mostra o RMS em dBFS (0 = fundo de escala). A entidade **Nível do microfone** publica o mesmo número em dB relativo (também 0 = fundo de escala). Se esse nível permanece abaixo de **−90 dBFS por 3 minutos** enquanto o áudio está transmitindo, o firmware regrava o ES8311 e reabre o microfone; se a escrita I2C falhar, a captura não voltar, ou o silêncio continuar por mais 3 minutos, o StickS3 reinicia. A entidade **Reiniciar** faz o mesmo reboot sob demanda.

Pinos (docs.m5stack.com/en/core/StickS3 + M5GFX/M5Unified):

| Função | Pinos |
|---|---|
| LCD ST7789P3 135x240 (offset 52/40, invert) | MOSI G39, SCK G40, DC G45, CS G41, RST G21, backlight PWM G38 |
| I2C interno (M5PM1 0x6E, ES8311 0x18, BMI270 0x68) | SDA G47, SCL G48 |
| ES8311 I2S | MCLK G18, BCLK G17, LRCK G15, mic → ESP G16 |
| Botões | A = G11 (acorda a tela; com a imagem ligada, liga/desliga áudio), B = G12 (acorda a tela) |
| Entidades extra | Reiniciar, Nível do microfone (dBFS, diagnóstico), Estado da tela, Tela: apagar após, Acordar tela |

No boot o firmware configura o M5PM1 (G2/L3B alto = alimentação do LCD; G3 = amplificador desligado).

Gravação (primeira vez):
1. Copie `esphome/secrets.yaml.example` para `esphome/secrets.yaml` e preencha (os binários não são publicados porque embutem Wi-Fi e chaves).
2. `esphome compile esphome/sticks3.yaml`.
3. Conecte por USB-C, abra https://web.esphome.io (Chrome/Edge). Modo download: segure o botão lateral até o LED verde interno piscar.
4. Connect → Install → escolha o `.factory.bin`. Depois aperte o botão lateral uma vez e abra **Logs**.
5. Atualizações seguintes: OTA com o `.ota.bin` ou pelo ESPHome.

Botão lateral: clique = liga/reset; duplo clique = desliga; pressionar longo = modo download.

## Tela inativa

Depois de **Tela: apagar após** minutos sem atividade a imagem some. Fica só um ponto vermelho no canto superior direito, piscando 1 s aceso e 1 s apagado, com o backlight em `screen_dot_brightness` (**25%**). Com a imagem ligada o backlight fica em `screen_on_brightness` (**70%**). O prazo padrão é **5 min** (ajustável de 0,5 a 60, em passos de 0,5). Cada vez que a tela acorda, o prazo recomeça.

A tela acorda com qualquer um destes:

- Botão **A** ou **B** no StickS3.
- Mudança de `sensor.ultimo_perigo_detectado`.
- Salto do **Nível do microfone** de pelo menos `wake_jump_db` (**15 dB**) acima do fundo. O fundo é uma média lenta, atualizada só enquanto o nível fica a até 6 dB dele.
- Botão **Acordar tela** no Home Assistant.
- Ação da API `esphome.<nó>_acordar_tela`.

**Primeiro toque de A.** Com a tela no ponto de gravação, o primeiro toque do botão A só acorda a imagem e reinicia o prazo. O toque seguinte, já com a tela ligada, liga ou desliga o **Monitor de sons perigosos**. O botão B só acorda (ou renova o prazo).

Entidades novas no dispositivo ESPHome:

| Entidade | Tipo | O que mostra ou faz |
|---|---|---|
| **Estado da tela** | sensor de texto | `ligada` ou `ponto de gravação` |
| **Tela: apagar após** | número (min, configuração) | Prazo de inatividade, de 0,5 a 60; o valor fica gravado no aparelho |
| **Acordar tela** | botão | Acorda na hora e reinicia o prazo |

A ação no firmware se chama `acordar_tela`. No Home Assistant o nome leva o nó, com hífens virados em sublinhados: `esphome.<nó>_acordar_tela`. O YAML usa `name_add_mac_suffix`, então o StickS3 de exemplo (`70:04:1d:dc:4b:40`) publica `esphome.sticks3_monitor_dc4b40_acordar_tela`. Outra MAC pede o nome correspondente (em **Ferramentas de desenvolvedor → Ações**, procure `acordar_tela`).

O pacote [`homeassistant/packages/sticks3_tela.yaml`](homeassistant/packages/sticks3_tela.yaml) chama essa ação a cada evento `monitor_perigo` (interfone, campainha, latido e as outras categorias). O sensor `ultimo_perigo_detectado` só muda quando a categoria muda; o pacote acorda a tela também quando o mesmo tipo de som se repete.

Para instalar, com os pacotes já ativos no `configuration.yaml` (o mesmo bloco do [manual do app](docs/manual-app-ha-os.md)):

```yaml
homeassistant:
  packages: !include_dir_named packages
```

1. Copie `homeassistant/packages/sticks3_tela.yaml` para `/config/packages/` do Home Assistant.
2. Se a MAC do StickS3 não termina em `dc:4b:40`, troque `esphome.sticks3_monitor_dc4b40_acordar_tela` pela ação do seu nó.
3. Em **Configurações → Ferramentas de desenvolvedor → YAML**, clique em **Verificar configuração** e depois em **Reiniciar**.

No topo de `esphome/sticks3.yaml`, três substituições ajustam o comportamento sem mudar a lógica:

| Substituição | Padrão | Efeito |
|---|---|---|
| `screen_on_brightness` | `70%` | Backlight com a imagem ligada |
| `screen_dot_brightness` | `25%` | Backlight no ponto vermelho de gravação |
| `wake_jump_db` | `"15.0"` | Salto, em dB acima do fundo, que acorda a tela |

## Escuta ao vivo

O app 0.3.0 reencaminha o PCM que já chegou por UDP para uma página HTTP no **ingress** do Home Assistant. A escuta usa a sessão do HA (também pelo app e pelo túnel) e não abre porta nova no host. Sem ouvinte, o laço UDP só testa um booleano. Com ouvinte, o áudio sai como chegou: 16 kHz, 16 bit, mono, cerca de **32 kB/s**, sem codificação. O atraso fica em torno de 0,5 s.

- O painel da barra lateral se chama **StickS3 ao vivo**. O botão **Ouvir** toca com Web Audio (app do HA e iOS). O link `stream.wav` serve o mesmo fluxo para o VLC.
- A opção **`live_device_mac`** (aba Configuração; exemplo `70:04:1d:dc:4b:40`) amarra essa página ao StickS3. Com o **Mosquitto** instalado, o app publica por MQTT discovery o sensor **Ouvintes ao vivo** no dispositivo dessa MAC e grava o link **Visitar** (`homeassistant://app/<slug>`). A ESPHome regrava o dispositivo ao reconectar; o app republica o link depois. Sem Mosquitto a página do painel continua disponível e o sensor e o link Visitar não são criados. MAC vazio desliga o vínculo.

## Chaves do config.toml (0.2.1)

Quem já editou o `config.toml` acrescenta estas chaves à mão e reinicia o app. Todas são opcionais; o padrão desliga o efeito. O manual do app detalha o ajuste.

| Chave | Onde | Padrão | Efeito |
|---|---|---|---|
| `log_file` | global | `""` | Log rotativo (5 MB × 3), por exemplo `/media/sound-monitor/monitor.log`, que sobrevive à janela curta de log do app |
| `near_miss_clips` | global | `[]` | Categorias cujo quase-acerto também grava um WAV `quase_<categoria>_<pontuação>` |
| `near_miss_clip_cooldown` | global | `10` | Intervalo mínimo, em segundos, entre dois clipes de quase |
| `min_active_ms` | por categoria | `0` | Com valor acima de 0, a janela precisa ter pelo menos esse tempo ativo (blocos de 50 ms a até `active_db` dB do pico, padrão 10) para contar como acerto. Bipes curtos ficam de fora, salvo um rótulo em `strong_labels` acima de `strong_threshold` |

Com `near_miss_fraction` acima de 0, a linha de quase lista **todas** as categorias que chegaram perto do limiar, mais os milissegundos ativos da janela.
