import obspython as obs
import json
import urllib.request
import urllib.error
import webbrowser
import threading

# Shared Twitch connection settings
client_id = ""
oauth_token = ""
refresh_token = ""
broadcaster_id = ""
chat_channel = ""

# Chat action settings
chat_enabled = False
chat_text_source = ""

# Subscription action settings
subscriptions_enabled = False
subscription_source = ""
_script_settings = None
_chat_test_timer = None
_subscription_test_timer = None
_subscription_hide_timer = None


def script_description():
    return (
        "<b>Twitch Event Actions</b><br>"
        "Motor compartido para eventos de Twitch que ejecutan acciones en OBS.<br><br>"
        "Configura primero Twitch y después activa Chat o Suscripciones.<br>"
        "El resto de eventos se añadirá sobre este mismo motor."
    )


def script_properties():
    props = obs.obs_properties_create()

    twitch_props = obs.obs_properties_create()
    obs.obs_properties_add_text(
        twitch_props, "client_id", "Twitch Client ID", obs.OBS_TEXT_DEFAULT
    )
    obs.obs_properties_add_text(
        twitch_props, "oauth_token", "Twitch OAuth Token", obs.OBS_TEXT_PASSWORD
    )
    obs.obs_properties_add_text(
        twitch_props, "refresh_token", "Twitch Refresh Token", obs.OBS_TEXT_PASSWORD
    )
    obs.obs_properties_add_text(
        twitch_props, "broadcaster_id",
        "Broadcaster ID (opcional; se obtiene automáticamente)",
        obs.OBS_TEXT_DEFAULT
    )
    obs.obs_properties_add_text(
        twitch_props, "chat_channel", "Canal de chat", obs.OBS_TEXT_DEFAULT
    )
    obs.obs_properties_add_button(
        twitch_props, "refresh_token_button", "Refrescar token", on_refresh_token
    )
    obs.obs_properties_add_button(
        twitch_props, "broadcaster_id_button", "Obtener Broadcaster ID",
        on_get_broadcaster_id
    )
    obs.obs_properties_add_button(
        twitch_props, "generate_token_button", "Abrir generador de token", on_generate_token
    )
    obs.obs_properties_add_group(
        props, "twitch_connection", "Conexión Twitch",
        obs.OBS_GROUP_NORMAL, twitch_props
    )

    chat_props = obs.obs_properties_create()
    obs.obs_properties_add_bool(
        chat_props, "chat_enabled", "Activar eventos de chat"
    )
    obs.obs_properties_add_text(
        chat_props, "chat_text_source", "Fuente de texto del chat",
        obs.OBS_TEXT_DEFAULT
    )
    obs.obs_properties_add_button(
        chat_props, "test_chat_button", "Probar chat (en 2 s)", on_test_chat
    )
    obs.obs_properties_add_group(
        props, "chat_actions", "Chat",
        obs.OBS_GROUP_NORMAL, chat_props
    )

    subscription_props = obs.obs_properties_create()
    obs.obs_properties_add_bool(
        subscription_props, "subscriptions_enabled",
        "Activar eventos de suscripciones"
    )
    obs.obs_properties_add_text(
        subscription_props, "subscription_source",
        "Fuente o escena de suscripción", obs.OBS_TEXT_DEFAULT
    )
    obs.obs_properties_add_button(
        subscription_props, "test_subscription_button",
        "Probar suscripción (2 s / 5 s visible)", on_test_subscription
    )
    obs.obs_properties_add_group(
        props, "subscription_actions", "Suscripciones",
        obs.OBS_GROUP_NORMAL, subscription_props
    )

    return props


def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "client_id", "")
    obs.obs_data_set_default_string(settings, "oauth_token", "")
    obs.obs_data_set_default_string(settings, "refresh_token", "")
    obs.obs_data_set_default_string(settings, "broadcaster_id", "")
    obs.obs_data_set_default_string(settings, "chat_channel", "")
    obs.obs_data_set_default_bool(settings, "chat_enabled", False)
    obs.obs_data_set_default_string(settings, "chat_text_source", "")
    obs.obs_data_set_default_bool(settings, "subscriptions_enabled", False)
    obs.obs_data_set_default_string(settings, "subscription_source", "")


def script_update(settings):
    global _script_settings
    global client_id, oauth_token, refresh_token, broadcaster_id, chat_channel
    global chat_enabled, chat_text_source
    global subscriptions_enabled, subscription_source

    _script_settings = settings
    client_id = obs.obs_data_get_string(settings, "client_id").strip()
    oauth_token = obs.obs_data_get_string(settings, "oauth_token").strip()
    refresh_token = obs.obs_data_get_string(settings, "refresh_token").strip()
    broadcaster_id = obs.obs_data_get_string(settings, "broadcaster_id").strip()
    chat_channel = obs.obs_data_get_string(settings, "chat_channel").strip().lstrip("#")
    chat_enabled = obs.obs_data_get_bool(settings, "chat_enabled")
    chat_text_source = obs.obs_data_get_string(settings, "chat_text_source").strip()
    subscriptions_enabled = obs.obs_data_get_bool(settings, "subscriptions_enabled")
    subscription_source = obs.obs_data_get_string(settings, "subscription_source").strip()


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
        return True
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


def _set_text_source(text):
    if not chat_text_source:
        obs.script_log(obs.LOG_WARNING, "Twitch Event Actions: falta la fuente de texto del chat.")
        return
    source = obs.obs_get_source_by_name(chat_text_source)
    if not source:
        obs.script_log(obs.LOG_WARNING, "No existe la fuente de texto '{}'.".format(chat_text_source))
        return
    settings = obs.obs_source_get_settings(source)
    obs.obs_data_set_string(settings, "text", text)
    obs.obs_source_update(source, settings)
    obs.obs_data_release(settings)
    obs.obs_source_release(source)
    obs.script_log(obs.LOG_INFO, "Texto de chat actualizado en '{}'.".format(chat_text_source))


def _set_subscription_visibility(visible):
    if not subscription_source:
        obs.script_log(obs.LOG_WARNING, "Twitch Event Actions: falta la fuente/escena de suscripción.")
        return
    scene_source = obs.obs_frontend_get_current_scene()
    if not scene_source:
        return
    scene = obs.obs_scene_from_source(scene_source)
    item = obs.obs_scene_find_source(scene, subscription_source) if scene else None
    if item:
        obs.obs_sceneitem_set_visible(item, visible)
        obs.script_log(
            obs.LOG_INFO,
            "Fuente de suscripción '{}' {}.".format(
                subscription_source, "mostrada" if visible else "ocultada"
            )
        )
    else:
        obs.script_log(obs.LOG_WARNING, "No se encontró '{}' en la escena activa.".format(subscription_source))
    obs.obs_source_release(scene_source)


def _run_chat_test():
    global _chat_test_timer
    _chat_test_timer = None
    _set_text_source("usuario_prueba: mensaje de prueba de Twitch")


def _hide_subscription_test():
    global _subscription_hide_timer
    _subscription_hide_timer = None
    _set_subscription_visibility(False)


def _run_subscription_test():
    global _subscription_test_timer, _subscription_hide_timer
    _subscription_test_timer = None
    _set_subscription_visibility(True)
    if _subscription_hide_timer:
        obs.timer_remove(_hide_subscription_test)
    _subscription_hide_timer = True
    obs.timer_add(_hide_subscription_test, 5000)


def on_test_chat(properties, property):
    global _chat_test_timer
    if _chat_test_timer:
        obs.timer_remove(_run_chat_test)
    _chat_test_timer = True
    obs.timer_add(_run_chat_test, 2000)
    obs.script_log(obs.LOG_INFO, "Prueba de chat programada para dentro de 2 segundos.")
    return True


def on_test_subscription(properties, property):
    global _subscription_test_timer
    if _subscription_test_timer:
        obs.timer_remove(_run_subscription_test)
    _subscription_test_timer = True
    obs.timer_add(_run_subscription_test, 2000)
    obs.script_log(obs.LOG_INFO, "Prueba de suscripción programada para dentro de 2 segundos.")
    return True


def script_load(settings):
    obs.script_log(obs.LOG_INFO, "Twitch Event Actions cargado.")
    obs.script_log(
        obs.LOG_INFO,
        "Paneles disponibles: Chat y Suscripciones."
    )


def script_unload():
    # Limpieza segura de temporizadores al descargar el script
    if _chat_test_timer:
        obs.timer_remove(_run_chat_test)
    if _subscription_test_timer:
        obs.timer_remove(_run_subscription_test)
    if _subscription_hide_timer:
        obs.timer_remove(_hide_subscription_test)
    
    obs.script_log(obs.LOG_INFO, "Twitch Event Actions descargado y temporizadores limpiados.")
