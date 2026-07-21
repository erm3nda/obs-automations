import obspython as obs

text_source_name = ""

def script_description():
    return (
        "<b>Set-Text-On-Twitch-Chat</b><br>"
        "Interactúa con el chat de Twitch o actualiza fuentes de texto en OBS.<br>"
    )

def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_text(props, "text_source_name", "Nombre de la fuente de Texto (GDI+)", obs.OBS_TEXT_DEFAULT)
    return props

def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "text_source_name", "")

def script_update(settings):
    global text_source_name
    text_source_name = obs.obs_data_get_string(settings, "text_source_name")

def script_load(settings):
    obs.script_log(obs.LOG_INFO, "Set-Text-On-Twitch-Chat cargado.")

def script_unload():
    obs.script_log(obs.LOG_INFO, "Set-Text-On-Twitch-Chat descargado.")
