
App **Sound Monitor** 0.3.0 (YAMNet no Home Assistant OS) e firmware ESPHome do M5Stack CoreS3 e do StickS3. Manuais: [app no HA OS](docs/manual-app-ha-os.md), [CoreS3](docs/manual-cores3-esphome.md). Histórico: [CHANGELOG.md](CHANGELOG.md).

## M5Stack StickS3 (`esphome/sticks3.yaml`)

Firmware dedicado ao **M5Stack StickS3** (ESP32-S3-PICO-1-N8R8: 8 MB flash, 8 MB PSRAM octal) que só transmite o microfone para o app Sound Monitor (UDP 5050, PCM 16 kHz/16 bit/mono, cerca de **32 kB/s**, sem codificação) e mostra uma tela de status (logo, Wi-Fi/IP, HA, áudio, último alerta).

O microfone inicia sozinho (intervalo de 5 s) e o ES8311 recebe a sequência de enable do M5Unified. O ganho do ADC (`mic_gain`) fica em **30 dB**. A tela mostra o RMS em dBFS (0 = fundo de escala). A entidade **Nível do microfone** publica o mesmo número em dB relativo (também 0 = fundo de escala). Se esse nível permanece abaixo de **−90 dBFS por 3 minutos** enquanto o áudio está transmitindo, o firmware regrava o ES8311 e reabre o microfone; se a escrita I2C falhar, a captura não voltar, ou o silêncio continuar por mais 3 minutos, o StickS3 reinicia. A entidade **Reiniciar** faz o mesmo reboot sob demanda.

Pinos (docs.m5stack.com/en/core/StickS3 + M5GFX/M5Unified):

| Função | Pinos |
|---|---|
| LCD ST7789P3 135x240 (offset 52/40, invert) | MOSI G39, SCK G40, DC G45, CS G41, RST G21, backlight PWM G38 |
| I2C interno (M5PM1 0x6E, ES8311 0x18, BMI270 0x68) | SDA G47, SCL G48 |
| ES8311 I2S | MCLK G18, BCLK G17, LRCK G15, mic → ESP G16 |
| Botões | A = G11 (liga/desliga áudio), B = G12 (atualiza tela) |
| Entidades extra | Reiniciar (botão), Nível do microfone (dBFS, diagnóstico) |

No boot o firmware configura o M5PM1 (G2/L3B alto = alimentação do LCD; G3 = amplificador desligado).

Gravação (primeira vez):
1. Copie `esphome/secrets.yaml.example` para `esphome/secrets.yaml` e preencha (os binários não são publicados porque embutem Wi-Fi e chaves).
2. `esphome compile esphome/sticks3.yaml`.
3. Conecte por USB-C, abra https://web.esphome.io (Chrome/Edge). Modo download: segure o botão lateral até o LED verde interno piscar.
4. Connect → Install → escolha o `.factory.bin`. Depois aperte o botão lateral uma vez e abra **Logs**.
5. Atualizações seguintes: OTA com o `.ota.bin` ou pelo ESPHome.

Botão lateral: clique = liga/reset; duplo clique = desliga; pressionar longo = modo download.

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
