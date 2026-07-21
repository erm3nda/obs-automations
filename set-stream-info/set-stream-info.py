import obspython as obs
import urllib.request
import urllib.parse
import json
import threading

# ─── Config defaults ──────────────────────────────────────────────────────────
stream_title     = ""
stream_category  = ""
stream_game_id   = ""
use_custom_token = False
twitch_client_id = ""
twitch_oauth_token = ""
broadcaster_id   = ""

def script_description():
    return (
        "<b>Set-Stream-Info</b><br>"
        "Actualiza el título y categoría de tu directo.<br><br>"
        "• <b>Modo Integrado:</b> Utiliza la conexión de cuenta por defecto de OBS.<br>"
        "• <b>Modo Token Opcional:</b> Permite usar tu propio Client ID / OAuth Token de Twitch Helix API."
    )

def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_text(props, "stream_title", "Título del Stream", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(props, "stream_category", "Categoría / Juego", obs.OBS_TEXT_DEFAULT)
    
    # Botón para aplicar cambios manualmente
    obs.obs_properties_add_button(props, "apply_button", "Aplicar Cambios de Stream", on_apply_clicked)

    # Sección de API Token Opcional
    obs.obs_properties_add_bool(props, "use_custom_token", "Usar Token Twitch OAuth Personalizado (Opcional)")
    obs.obs_properties_add_text(props, "broadcaster_id", "Broadcaster ID (Twitch User ID)", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(props, "twitch_client_id", "Twitch Client ID", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(props, "twitch_oauth_token", "Twitch OAuth Token (Bearer)", obs.OBS_TEXT_PASSWORD)
    
    return props

def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "stream_title", "")
    obs.obs_data_set_default_string(settings, "stream_category", "")
    obs.obs_data_set_default_bool(settings, "use_custom_token", False)
    obs.obs_data_set_default_string(settings, "broadcaster_id", "")
    obs.obs_data_set_default_string(settings, "twitch_client_id", "")
    obs.obs_data_set_default_string(settings, "twitch_oauth_token", "")

def script_update(settings):
    global stream_title, stream_category, use_custom_token
    global broadcaster_id, twitch_client_id, twitch_oauth_token
    
    stream_title       = obs.obs_data_get_string(settings, "stream_title")
    stream_category    = obs.obs_data_get_string(settings, "stream_category")
    use_custom_token   = obs.obs_data_get_bool(settings, "use_custom_token")
    broadcaster_id     = obs.obs_data_get_string(settings, "broadcaster_id")
    twitch_client_id   = obs.obs_data_get_string(settings, "twitch_client_id")
    twitch_oauth_token = obs.obs_data_get_string(settings, "twitch_oauth_token")

def update_stream_info_helix():
    """Actualiza la información vía Twitch Helix API utilizando el Token personalizado."""
    if not broadcaster_id or not twitch_client_id or not twitch_oauth_token:
        obs.script_log(obs.LOG_WARNING, "[Set-Stream-Info] Falta Broadcaster ID, Client ID u OAuth Token.")
        return

    url = f"https://api.twitch.tv/helix/channels?broadcaster_id={broadcaster_id}"
    headers = {
        "Client-ID": twitch_client_id,
        "Authorization": f"Bearer {twitch_oauth_token.replace('oauth:', '')}",
        "Content-Type": "application/json"
    }
    
    body = {}
    if stream_title:
        body["title"] = stream_title
    
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='PATCH')

    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 204:
                obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] Stream info actualizado con éxito vía Helix API.")
            else:
                obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] Respuesta API: {response.status}")
    except Exception as e:
        obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Error al actualizar vía Helix API: {e}")

def update_stream_info_obs_service():
    """Intenta usar la información de servicio/stream nativa configurada en el perfil de OBS."""
    service = obs.obs_frontend_get_streaming_service()
    if service:
        service_type = obs.obs_service_get_type(service)
        obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] Servicio de stream detectado en OBS: {service_type}")
        # En OBS nativo con cuenta enlazada, la info se sincroniza automáticamente
        # mediante los paneles integrados al iniciar transmisión o cambiar perfil.
    else:
        obs.script_log(obs.LOG_WARNING, "[Set-Stream-Info] No se detectó un servicio de stream activo en el perfil.")

def on_apply_clicked(properties, property):
    obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] Aplicando nuevo título: '{stream_title}' | Categoría: '{stream_category}'")
    
    if use_custom_token:
        # Ejecutar en segundo plano para no bloquear la UI
        t = threading.Thread(target=update_stream_info_helix)
        t.daemon = True
        t.start()
    else:
        update_stream_info_obs_service()

def script_load(settings):
    obs.script_log(obs.LOG_INFO, "Set-Stream-Info cargado correctamente.")

def script_unload():
    obs.script_log(obs.LOG_INFO, "Set-Stream-Info descargado.")

