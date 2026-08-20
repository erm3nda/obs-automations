import obspython as obs
import json
import urllib.request
import urllib.error
import webbrowser
import time

# Shared Twitch connection settings
client_id = ""
oauth_token = ""
refresh_token = ""
broadcaster_id = ""
chat_channel = ""

# Chat action settings
chat_enabled = False
chat_target_type = 0  # 0: Lista de mensajes de chat (hasta 4), 1: Mostrar/Ocultar Fuente o Escena, 2: Texto estático único
chat_target_source = ""
chat_duration = 5

# Subscription action settings
subscriptions_enabled = False
sub_target_type = 1   # 0: Texto estático único, 1: Mostrar/Ocultar Fuente o Escena
sub_target_source = ""
sub_duration = 5

_script_settings = None

# Historial de mensajes de chat con timestamps: list of (timestamp, formatted_text)
_chat_message_history = []
_test_msg_counter = 1

# Dynamic active timers dictionary to manage hide timeouts safely
# Key: source_name (str), Value: timer_callback
_active_hide_timers = {}


def script_description():
    return (
        "<b>Twitch Event Actions</b><br>"
        "Motor flexible de eventos para Twitch con acciones en OBS.<br><br>"
        "Configura la conexión de Twitch y personaliza cada acción (visibilidad o texto) y su duración."
    )


def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_bool(props, "enabled", "✅ Plugin Activo")

    # --- Grupo Conexión Twitch ---
    twitch_props = obs.obs_properties_create()
    obs.obs_properties_add_text(twitch_props, "client_id", "Twitch Client ID", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(twitch_props, "oauth_token", "Twitch OAuth Token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(twitch_props, "refresh_token", "Twitch Refresh Token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(twitch_props, "broadcaster_id", "Broadcaster ID (auto)", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(twitch_props, "chat_channel", "Canal de chat", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_button(twitch_props, "refresh_token_button", "Refrescar token", on_refresh_token)
    obs.obs_properties_add_button(twitch_props, "broadcaster_id_button", "Obtener Broadcaster ID", on_get_broadcaster_id)
    obs.obs_properties_add_button(twitch_props, "generate_token_button", "Abrir generador de token", on_generate_token)
    
    obs.obs_properties_add_group(props, "twitch_connection", "🔐 Conexión Twitch", obs.OBS_GROUP_NORMAL, twitch_props)

    # --- Grupo Chat Actions ---
    chat_props = obs.obs_properties_create()
    obs.obs_properties_add_bool(chat_props, "chat_enabled", "Activar eventos de chat")
    
    p_chat_type = obs.obs_properties_add_list(
        chat_props, "chat_target_type", "Tipo de Acción Chat",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_INT
    )
    obs.obs_property_list_add_int(p_chat_type, "Lista de mensajes de chat (hasta 4 líneas)", 0)
    obs.obs_property_list_add_int(p_chat_type, "Mostrar/Ocultar Fuente o Escena", 1)
    obs.obs_property_list_add_int(p_chat_type, "Actualizar Texto único", 2)

    p_chat_source = obs.obs_properties_add_list(
        chat_props, "chat_target_source", "Fuente/Escena de Chat",
        obs.OBS_COMBO_TYPE_EDITABLE, obs.OBS_COMBO_FORMAT_STRING
    )
    obs.obs_properties_add_int(chat_props, "chat_duration", "Ocultar tras (segundos, solo visibilidad/texto único)", 1, 300, 1)
    obs.obs_properties_add_button(chat_props, "test_chat_button", "▶ Añadir mensaje de chat simulado", on_test_chat)
    
    obs.obs_properties_add_group(props, "chat_actions", "💬 Evento: Chat", obs.OBS_GROUP_NORMAL, chat_props)

    # --- Grupo Subscriptions Actions ---
    sub_props = obs.obs_properties_create()
    obs.obs_properties_add_bool(sub_props, "subscriptions_enabled", "Activar eventos de suscripción")
    
    p_sub_type = obs.obs_properties_add_list(
        sub_props, "sub_target_type", "Tipo de Acción Suscripción",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_INT
    )
    obs.obs_property_list_add_int(p_sub_type, "Actualizar Texto de Fuente", 0)
    obs.obs_property_list_add_int(p_sub_type, "Mostrar/Ocultar Fuente o Escena", 1)

    p_sub_source = obs.obs_properties_add_list(
        sub_props, "sub_target_source", "Fuente/Escena de Suscripción",
        obs.OBS_COMBO_TYPE_EDITABLE, obs.OBS_COMBO_FORMAT_STRING
    )

    # Rellenar listas de fuentes desde OBS
    sources = obs.obs_enum_sources()
    if sources is not None:
        for source in sources:
            name = obs.obs_source_get_name(source)
            obs.obs_property_list_add_string(p_chat_source, name, name)
            obs.obs_property_list_add_string(p_sub_source, name, name)
        obs.source_list_release(sources)
    obs.obs_properties_add_int(sub_props, "sub_duration", "Ocultar / Borrar tras (segundos)", 1, 300, 1)
    obs.obs_properties_add_button(sub_props, "test_sub_button", "▶ Probar Acción Suscripción", on_test_subscription)
    
    obs.obs_properties_add_group(props, "sub_actions", "⭐ Evento: Suscripciones", obs.OBS_GROUP_NORMAL, sub_props)

    return props


def script_defaults(settings):
    obs.obs_data_set_default_bool(settings, "enabled", True)
    obs.obs_data_set_default_string(settings, "client_id", "")
    obs.obs_data_set_default_string(settings, "oauth_token", "")
    obs.obs_data_set_default_string(settings, "refresh_token", "")
    obs.obs_data_set_default_string(settings, "broadcaster_id", "")
    obs.obs_data_set_default_string(settings, "chat_channel", "")
    
    obs.obs_data_set_default_bool(settings, "chat_enabled", False)
    obs.obs_data_set_default_int(settings, "chat_target_type", 0)
    obs.obs_data_set_default_string(settings, "chat_target_source", "twitch_chat")
    obs.obs_data_set_default_int(settings, "chat_duration", 5)

    obs.obs_data_set_default_bool(settings, "subscriptions_enabled", False)
    obs.obs_data_set_default_int(settings, "sub_target_type", 1)
    obs.obs_data_set_default_string(settings, "sub_target_source", "twitch_subscription")
    obs.obs_data_set_default_int(settings, "sub_duration", 5)


def script_update(settings):
    global enabled, _script_settings
    global client_id, oauth_token, refresh_token, broadcaster_id, chat_channel
    global chat_enabled, chat_target_type, chat_target_source, chat_duration
    global subscriptions_enabled, sub_target_type, sub_target_source, sub_duration

    _script_settings = settings
    enabled = obs.obs_data_get_bool(settings, "enabled")
    client_id = obs.obs_data_get_string(settings, "client_id").strip()
    oauth_token = obs.obs_data_get_string(settings, "oauth_token").strip()
    refresh_token = obs.obs_data_get_string(settings, "refresh_token").strip()
    broadcaster_id = obs.obs_data_get_string(settings, "broadcaster_id").strip()
    chat_channel = obs.obs_data_get_string(settings, "chat_channel").strip().lstrip("#")

    chat_enabled = obs.obs_data_get_bool(settings, "chat_enabled")
    chat_target_type = obs.obs_data_get_int(settings, "chat_target_type")
    chat_target_source = obs.obs_data_get_string(settings, "chat_target_source").strip()
    chat_duration = obs.obs_data_get_int(settings, "chat_duration")

    subscriptions_enabled = obs.obs_data_get_bool(settings, "subscriptions_enabled")
    sub_target_type = obs.obs_data_get_int(settings, "sub_target_type")
    sub_target_source = obs.obs_data_get_string(settings, "sub_target_source").strip()
    sub_duration = obs.obs_data_get_int(settings, "sub_duration")


def on_refresh_token(properties, property):
    global oauth_token, refresh_token
    if not refresh_token or not client_id:
        obs.script_log(obs.LOG_WARNING, "Twitch Event Actions: faltan Refresh Token o Client ID.")
        return True
    url = "https://twitchtokengenerator.com/api/refresh/{}".format(refresh_token)
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not data.get("success") or not data.get("token"):
            obs.script_log(obs.LOG_ERROR, "Twitch Event Actions: no se pudo refrescar el token.")
            return True
        oauth_token = data["token"]
        refresh_token = data.get("refresh", refresh_token)
        if _script_settings:
            obs.obs_data_set_string(_script_settings, "oauth_token", oauth_token)
            obs.obs_data_set_string(_script_settings, "refresh_token", refresh_token)
        obs.script_log(obs.LOG_INFO, "Twitch Event Actions: token refrescado correctamente.")
    except (urllib.error.URLError, ValueError) as error:
        obs.script_log(obs.LOG_ERROR, "Twitch Event Actions: error refrescando token: {}".format(error))
    return True


def on_generate_token(properties, property):
    scopes = "channel:manage:broadcast%20channel:read:subscriptions%20user:read:chat"
    url = (
        "https://id.twitch.tv/oauth2/authorize?response_type=code&"
        "client_id={}&redirect_uri=https://twitchtokengenerator.com&scope={}"
    ).format(client_id, scopes)
    webbrowser.open(url)
    return True


def on_get_broadcaster_id(properties, property):
    global broadcaster_id
    if not client_id or not oauth_token:
        obs.script_log(obs.LOG_WARNING, "Twitch Event Actions: faltan Client ID o OAuth Token.")
        return True
    request = urllib.request.Request(
        "https://api.twitch.tv/helix/users",
        headers={
            "Client-ID": client_id,
            "Authorization": "Bearer {}".format(oauth_token.replace("oauth:", "").strip())
        }
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            users = json.loads(response.read().decode("utf-8")).get("data", [])
        if not users:
            obs.script_log(obs.LOG_WARNING, "Twitch Event Actions: Twitch no devolvió ningún usuario.")
            return True
        broadcaster_id = users[0].get("id", "")
        if _script_settings:
            obs.obs_data_set_string(_script_settings, "broadcaster_id", broadcaster_id)
        obs.script_log(obs.LOG_INFO, "Broadcaster ID obtenido: {}".format(broadcaster_id))
    except (urllib.error.URLError, ValueError) as error:
        obs.script_log(obs.LOG_ERROR, "Twitch Event Actions: error obteniendo Broadcaster ID: {}".format(error))
    return True


# --- Funciones Genéricas de Ejecución de Acciones ---

def _update_source_text(source_name, text):
    if not source_name:
        return
    source = obs.obs_get_source_by_name(source_name)
    if not source:
        obs.script_log(obs.LOG_WARNING, "No existe la fuente de texto '{}'.".format(source_name))
        return
    settings = obs.obs_source_get_settings(source)
    obs.obs_data_set_string(settings, "text", text)
    obs.obs_source_update(source, settings)
    obs.obs_data_release(settings)
    obs.obs_source_release(source)


def _prune_expired_chat_messages(duration_seconds):
    """Elimina mensajes cuyo tiempo de vida haya superado los segundos configurados."""
    global _chat_message_history
    current_now = time.time()
    max_age = duration_seconds if duration_seconds > 0 else 300
    _chat_message_history = [
        (ts, msg) for (ts, msg) in _chat_message_history
        if (current_now - ts) < max_age
    ]


def _append_chat_message(source_name, new_msg, duration_seconds):
    """Añade un mensaje a la lista acumulativa de hasta 4 mensajes respetando el tiempo de expiración."""
    global _chat_message_history
    _prune_expired_chat_messages(duration_seconds)
    _chat_message_history.append((time.time(), new_msg))
    if len(_chat_message_history) > 4:
        _chat_message_history = _chat_message_history[-4:]
    
    combined_text = "\n".join(msg for _, msg in _chat_message_history)
    _update_source_text(source_name, combined_text)

    # Programar temporizador para forzar la actualización/limpieza cuando expire el más antiguo
    if _chat_message_history and duration_seconds > 0:
        if source_name in _active_hide_timers:
            obs.timer_remove(_active_hide_timers[source_name])
            del _active_hide_timers[source_name]

        def _chat_prune_timer_cb():
            if source_name in _active_hide_timers:
                obs.timer_remove(_active_hide_timers[source_name])
                del _active_hide_timers[source_name]
            _prune_expired_chat_messages(duration_seconds)
            updated_text = "\n".join(msg for _, msg in _chat_message_history)
            _update_source_text(source_name, updated_text)

        _active_hide_timers[source_name] = _chat_prune_timer_cb
        obs.timer_add(_chat_prune_timer_cb, duration_seconds * 1000)


def _set_source_visibility(source_name, visible):
    if not source_name:
        return
    found = False
    scenes = obs.obs_frontend_get_scenes()
    if scenes:
        for sc_src in scenes:
            scene = obs.obs_scene_from_source(sc_src)
            if scene:
                item = obs.obs_scene_find_source(scene, source_name)
                if item:
                    obs.obs_sceneitem_set_visible(item, visible)
                    found = True
        obs.source_list_release(scenes)
    if not found:
        obs.script_log(obs.LOG_WARNING, "No se encontró la fuente/escena '{}' en ninguna escena.".format(source_name))


def _schedule_auto_hide(source_name, action_type, duration_seconds):
    """Maneja el ocultado / borrado automático después del tiempo especificado."""
    if not source_name or duration_seconds <= 0:
        return

    # Limpiar temporizador previo para la misma fuente si existía
    if source_name in _active_hide_timers:
        obs.timer_remove(_active_hide_timers[source_name])
        del _active_hide_timers[source_name]

    def _hide_callback():
        if source_name in _active_hide_timers:
            obs.timer_remove(_active_hide_timers[source_name])
            del _active_hide_timers[source_name]

        if action_type == 0:  # Borrar texto
            _update_source_text(source_name, "")
        else:  # Ocultar visibilidad
            _set_source_visibility(source_name, False)

    _active_hide_timers[source_name] = _hide_callback
    obs.timer_add(_hide_callback, duration_seconds * 1000)


def trigger_event_action(action_type, target_source, duration, text_payload=""):
    """Dispara una acción de evento configurable."""
    if not target_source:
        obs.script_log(obs.LOG_WARNING, "Twitch Event Actions: No se ha especificado fuente de destino.")
        return

    if action_type == 0:  # Lista de mensajes de chat acumulativa con temporizador por duración
        _append_chat_message(target_source, text_payload, duration)
    elif action_type == 1:  # Mostrar/Ocultar Visibilidad
        _set_source_visibility(target_source, True)
        _schedule_auto_hide(target_source, action_type, duration)
    elif action_type == 2:  # Actualizar Texto único con auto-borrado
        _update_source_text(target_source, text_payload)
        _schedule_auto_hide(target_source, 0, duration)


# --- Handlers de prueba manuales ---

def on_test_chat(properties, property):
    global _test_msg_counter
    if not enabled:
        return True
    obs.script_log(obs.LOG_INFO, "Simulando nuevo mensaje de chat...")
    trigger_event_action(
        chat_target_type,
        chat_target_source,
        chat_duration,
        "Usuario_{}: Mensaje simulado #{}".format(_test_msg_counter, _test_msg_counter)
    )
    _test_msg_counter += 1
    return True


def on_test_subscription(properties, property):
    if not enabled:
        return True
    obs.script_log(obs.LOG_INFO, "Probando acción de suscripción...")
    trigger_event_action(
        sub_target_type,
        sub_target_source,
        sub_duration,
        "¡Nuevo suscriptor: UsuarioPrueba!"
    )
    return True


def script_load(settings):
    obs.script_log(obs.LOG_INFO, "Twitch Event Actions cargado correctamente.")


def script_unload():
    # Limpiar todos los temporizadores activos al descargar
    for source_name, timer_cb in list(_active_hide_timers.items()):
        obs.timer_remove(timer_cb)
    _active_hide_timers.clear()
    obs.script_log(obs.LOG_INFO, "Twitch Event Actions descargado y temporizadores limpiados.")
