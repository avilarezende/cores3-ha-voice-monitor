
## M5Stack StickS3 (`esphome/sticks3.yaml`)

Firmware dedicado ao **M5Stack StickS3** (ESP32-S3-PICO-1-N8R8: 8 MB flash, 8 MB PSRAM octal) que só transmite o microfone para o app Sound Monitor (UDP 5050, PCM 16 kHz/16 bit/mono) e mostra uma tela de status (logo, Wi-Fi/IP, HA, áudio, último alerta).

**v2:** o microfone inicia sozinho (intervalo de 5 s), o ES8311 recebe a sequência de enable do M5Unified e o ganho fica em 0 dB. O áudio foi verificado transmitindo para o Sound Monitor.

Pinos (docs.m5stack.com/en/core/StickS3 + M5GFX/M5Unified):

| Função | Pinos |
|---|---|
| LCD ST7789P3 135x240 (offset 52/40, invert) | MOSI G39, SCK G40, DC G45, CS G41, RST G21, backlight PWM G38 |
| I2C interno (M5PM1 0x6E, ES8311 0x18, BMI270 0x68) | SDA G47, SCL G48 |
| ES8311 I2S | MCLK G18, BCLK G17, LRCK G15, mic → ESP G16 |
| Botões | A = G11 (liga/desliga áudio), B = G12 (atualiza tela) |

No boot o firmware configura o M5PM1 (G2/L3B alto = alimentação do LCD; G3 = amplificador desligado).

Gravação (primeira vez):
1. Copie `esphome/secrets.yaml.example` para `esphome/secrets.yaml` e preencha (os binários não são publicados porque embutem Wi-Fi e chaves).
2. `esphome compile esphome/sticks3.yaml`.
3. Conecte por USB-C, abra https://web.esphome.io (Chrome/Edge). Modo download: segure o botão lateral até o LED verde interno piscar.
4. Connect → Install → escolha o `.factory.bin`. Depois aperte o botão lateral uma vez e abra **Logs**.
5. Atualizações seguintes: OTA com o `.ota.bin` ou pelo ESPHome.

Botão lateral: clique = liga/reset; duplo clique = desliga; pressionar longo = modo download.
