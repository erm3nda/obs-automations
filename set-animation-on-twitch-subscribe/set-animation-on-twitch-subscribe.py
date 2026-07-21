import obspython as obs

source_name = ""

def script_description():
    return (
        "<b>Set-Animation-On-Twitch-Subscribe</b><br>"
        "Dispara una animación o visibilidad de fuente en OBS cuando ocurre una suscripción en Twitch.<br>"
    )

def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_text(props, "source_name", "Nombre de la fuente/animación", obs.OBS_TEXT_DEFAULT)
    return props

def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "source_name", "")

def script_update(settings):
    global source_name
    source_name = obs.obs_data_get_string(settings, "source_name")

def script_load(settings):
    obs.script_log(obs.LOG_INFO, "Set-Animation-On-Twitch-Subscribe cargado.")

def script_unload():
    obs.script_log(obs.LOG_INFO, "Set-Animation-On-Twitch-Subscribe descargado.")
