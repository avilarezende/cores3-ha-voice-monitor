import esphome.codegen as cg
from esphome.components import microphone
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_MICROPHONE, CONF_PORT

DEPENDENCIES = ["microphone", "network"]
CODEOWNERS = ["@avilarezende"]

CONF_HOST = "host"

audio_udp_stream_ns = cg.esphome_ns.namespace("audio_udp_stream")
AudioUdpStream = audio_udp_stream_ns.class_("AudioUdpStream", cg.Component)

CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(AudioUdpStream),
            cv.Optional(
                CONF_MICROPHONE, default={}
            ): microphone.microphone_source_schema(
                min_bits_per_sample=16,
                max_bits_per_sample=16,
                min_channels=1,
                max_channels=1,
            ),
            cv.Required(CONF_HOST): cv.ipv4address,
            cv.Optional(CONF_PORT, default=5050): cv.port,
        }
    ).extend(cv.COMPONENT_SCHEMA),
    cv.only_on_esp32,
)

FINAL_VALIDATE_SCHEMA = cv.Schema(
    {
        cv.Required(CONF_MICROPHONE): microphone.final_validate_microphone_source_schema(
            "audio_udp_stream", sample_rate=16000
        ),
    },
    extra=cv.ALLOW_EXTRA,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    # Passive: the CoreS3 mic and speaker share one I2S port, so holding the mic open would block TTS playback.
    mic_source = await microphone.microphone_source_to_code(
        config[CONF_MICROPHONE], passive=True
    )
    cg.add(var.set_microphone_source(mic_source))
    cg.add(var.set_host(str(config[CONF_HOST])))
    cg.add(var.set_port(config[CONF_PORT]))
