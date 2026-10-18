# Changelog

## 0.2.0

### Sound Monitor

- Lista `allowed_sources`: UDP de outros IPs é ignorado (um roteador chegou a reenviar pacotes e gerar falso alarme de interfone).
- `min_fresh_fraction`: o YAMNet só roda quando a janela chegou de áudio contínuo, não de pacotes soltos sobre um buffer velho.
- Veto por categoria (`veto_labels`, `veto_min`, `veto_ratio`) para pássaros, com `strong_labels` / `strong_threshold` para um toque claro continuar valendo.
- `near_miss_fraction`: com o log em INFO, registra as cinco classes principais quando uma categoria chega perto do limiar.
- As chaves novas são opcionais. Um `config.toml` antigo continua válido.
- Versão do app em `sound-monitor/config.yaml` sobe para **0.2.0**, para o Home Assistant oferecer a atualização.

### StickS3

- Ganho do microfone ES8311 (`mic_gain`) de 0 dB para 24 dB. A sequência de enable do M5Unified também regrava o registrador de escala (0x16 = 24 dB).
- Entidade botão **Reiniciar**.
- Entidade **Nível do microfone** (RMS; 0 dB = fundo de escala) e o mesmo valor na tela, em dBFS, enquanto transmite.
- Auto-recuperação: se o nível fica abaixo de −90 dBFS por 3 minutos com o stream ligado, o firmware reinicializa o ES8311 e o microfone. Se a escrita I2C falha, a captura não volta, ou o silêncio continua por mais 3 minutos, o StickS3 reinicia.
