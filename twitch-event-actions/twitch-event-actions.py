import obspython as obs
import threading
import socket
import os
import ssl
import time
import re
import urllib.request
import urllib.error
import urllib.parse
import json
import webbrowser
import textwrap
import base64
import hashlib
import struct
import ctypes
from ctypes import wintypes

# --- Variables Globales ---
_script_settings = None
_irc_stop_event = threading.Event()
_irc_thread = None
_irc_socket = None
_eventsub_stop_event = threading.Event()
_eventsub_thread = None
_eventsub_socket = None
_eventsub_signature = None
_active_hide_timers = {}
_chat_message_history = []
_test_msg_counter = 0
_event_queue = []
_event_configs = {}
_event_settings_loaded = False
_selected_event_key = "chat"
_event_ui_properties = {}
_credentials_loaded = False
_fallback_warning_logged = False

CREDENTIAL_TARGET = "Twitch-Event-Actions"
CREDENTIAL_FILE = ".obs-twitch-event-actions-credentials.json"
CREDENTIAL_KEYS = ("client_id", "oauth_token", "refresh_token")


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]

# Variables de configuración
enabled = True
client_id = ""
oauth_token = ""
refresh_token = ""
twitch_scopes = "channel:manage:broadcast user:read:chat"
broadcaster_id = ""
chat_channel = ""

chat_enabled = False
chat_target_type = 0
chat_target_source = "twitch_chat"
chat_max_messages = 5
chat_max_chars = 40
chat_duration = 5

subscriptions_enabled = False
sub_target_type = 1
sub_target_source = "twitch_subscription"
sub_duration = 5

EVENT_DEFINITIONS = [
    ("chat", "Chat", None, None, ""),
    ("follow", "Follow", "channel.follow", "2", "Follow de {user}"),
    ("subscribe", "Nueva suscripción", "channel.subscribe", "1", "Nueva suscripción de {user}"),
    ("subscription_message", "Resuscripción con mensaje", "channel.subscription.message", "1", "Resuscripción de {user}: {message}"),
    ("subscription_gift", "Suscripción regalada", "channel.subscription.gift", "1", "{user} ha regalado {amount} suscripción(es)"),
    ("subscription_end", "Fin de suscripción", "channel.subscription.end", "1", "Fin de suscripción de {user}"),
    ("raid", "Raid recibido", "channel.raid", "1", "Raid de {user} con {amount} espectadores"),
    ("cheer", "Bits / Cheer", "channel.cheer", "1", "{user} ha enviado {amount} Bits: {message}"),
    ("channel_points", "Canje de puntos del canal", "channel.channel_points_custom_reward_redemption.add", "1", "{user} ha canjeado {reward}: {message}"),
    ("stream_online", "Directo iniciado", "stream.online", "1", "El directo ha comenzado: {title}"),
    ("stream_offline", "Directo finalizado", "stream.offline", "1", "El directo ha terminado"),
    ("channel_update", "Cambio de título/categoría", "channel.update", "2", "Directo actualizado: {title} ({category})"),
    ("guest_star_begin", "Guest Star: sesión iniciada", "channel.guest_star_session.begin", "beta", "Sesión Guest Star iniciada"),
    ("guest_star_end", "Guest Star: sesión finalizada", "channel.guest_star_session.end", "beta", "Sesión Guest Star finalizada"),
    ("guest_star_guest_update", "Guest Star: invitado actualizado", "channel.guest_star_guest.update", "beta", "Invitado Guest Star actualizado: {user}"),
    ("guest_star_settings_update", "Guest Star: ajustes actualizados", "channel.guest_star_settings.update", "beta", "Ajustes Guest Star actualizados"),
    ("moderator_add", "Moderador añadido", "channel.moderator.add", "1", "{user} ahora es moderador"),
    ("moderator_remove", "Moderador eliminado", "channel.moderator.remove", "1", "{user} ya no es moderador"),
    ("moderate", "Acción de moderación", "channel.moderate", "2", "{user} realizó una acción de moderación: {action}"),
    ("ban", "Usuario baneado", "channel.ban", "1", "{user} ha sido baneado: {reason}"),
    ("unban", "Usuario desbaneado", "channel.unban", "1", "{user} ha sido desbaneado"),
]
EVENT_DEFINITION_BY_KEY = {item[0]: item for item in EVENT_DEFINITIONS}

# Cola segura para despacho entre hilos
_chat_queue = []
_chat_queue_lock = threading.Lock()

def script_description():
    return "Twitch Event Actions: Automatiza acciones en OBS basadas en eventos de Twitch."

def _log_error(message):
    message = str(message)
    for key in CREDENTIAL_KEYS:
        secret = globals().get(key, "")
        if not isinstance(secret, str) or not secret:
            continue
        secret_variants = {secret, secret.strip()}
        if key == "oauth_token":
            token_without_prefix = secret.strip()
            if token_without_prefix.startswith("oauth:"):
                secret_variants.add(token_without_prefix[len("oauth:"):])
        secret_forms = set()
        for variant in secret_variants:
            if variant:
                secret_forms.update(
                    (
                        variant,
                        urllib.parse.quote(variant, safe=""),
                        urllib.parse.quote_plus(variant),
                    )
                )
        for secret_form in sorted(secret_forms, key=len, reverse=True):
            message = message.replace(secret_form, "[REDACTED]")
    obs.script_log(obs.LOG_INFO, "[ERROR] {}".format(message))

def _credential_file_path():
    return os.path.join(os.path.expanduser("~"), CREDENTIAL_FILE)

def _log_fallback_warning(reason):
    global _fallback_warning_logged
    if _fallback_warning_logged:
        return
    obs.script_log(
        obs.LOG_INFO,
        "[Twitch-Event-Actions] [AVISO] {} Las credenciales se guardarán sin cifrar en '{}'. "
        "Protege ese archivo y no lo compartas.".format(reason, _credential_file_path()),
    )
    _fallback_warning_logged = True

def _read_windows_credentials():
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_Credential)),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None
    credential = ctypes.POINTER(_Credential)()
    if not advapi32.CredReadW(CREDENTIAL_TARGET, 1, 0, ctypes.byref(credential)):
        error_code = ctypes.get_last_error()
        if error_code == 1168:
            return None
        raise OSError(error_code, "CredReadW failed")
    try:
        blob = ctypes.string_at(
            credential.contents.CredentialBlob,
            credential.contents.CredentialBlobSize,
        )
        stored = json.loads(blob.decode("utf-8"))
        if not isinstance(stored, dict):
            raise ValueError("Stored credentials are not a JSON object")
        return _normalize_credentials(stored)
    finally:
        advapi32.CredFree(credential)

def _write_windows_credentials(credentials):
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    blob = json.dumps(_normalize_credentials(credentials), ensure_ascii=False).encode("utf-8")
    if len(blob) > 2560:
        raise ValueError("Credential data exceeds the Windows Credential Manager limit")
    blob_buffer = ctypes.create_string_buffer(blob)
    credential = _Credential()
    credential.Type = 1
    credential.TargetName = CREDENTIAL_TARGET
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(blob_buffer, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2
    credential.UserName = "Twitch-Event-Actions"
    if not advapi32.CredWriteW(ctypes.byref(credential), 0):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, "CredWriteW failed")

def _delete_windows_credentials():
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    if not advapi32.CredDeleteW(CREDENTIAL_TARGET, 1, 0):
        error_code = ctypes.get_last_error()
        if error_code != 1168:
            raise OSError(error_code, "CredDeleteW failed")

def _read_fallback_credentials():
    path = _credential_file_path()
    if not os.path.exists(path):
        return {}
    _log_fallback_warning("El almacén seguro no está disponible.")
    try:
        with open(path, "r", encoding="utf-8") as credential_file:
            stored = json.load(credential_file)
        if not isinstance(stored, dict):
            raise ValueError("Credential file must contain a JSON object")
        return _normalize_credentials(stored)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        _log_error("[Twitch-Event-Actions] No se pudieron leer las credenciales locales: {}".format(error))
        return {}

def _remove_fallback_credentials(strict=False):
    try:
        os.remove(_credential_file_path())
    except FileNotFoundError:
        pass
    except OSError as error:
        _log_error("[Twitch-Event-Actions] No se pudo borrar el archivo de fallback: {}".format(error))
        if strict:
            raise

def _normalize_credentials(credentials):
    return {
        key: credentials.get(key, "") if isinstance(credentials.get(key, ""), str) else ""
        for key in CREDENTIAL_KEYS
    }

def _load_credentials():
    if os.name == "nt":
        try:
            credentials = _read_windows_credentials()
            if credentials is not None:
                _remove_fallback_credentials()
                return credentials
        except (OSError, ValueError, json.JSONDecodeError) as error:
            _log_fallback_warning(
                "No se pudo acceder al Administrador de credenciales de Windows ({}).".format(error)
            )
    else:
        _log_fallback_warning("Este sistema no dispone de un almacén seguro integrado.")
    return _read_fallback_credentials()

def _save_credentials(credentials):
    normalized = _normalize_credentials(credentials)
    if os.name == "nt":
        try:
            _write_windows_credentials(normalized)
            _remove_fallback_credentials(strict=True)
            return
        except (OSError, ValueError) as error:
            _log_fallback_warning(
                "No se pudo guardar en el Administrador de credenciales de Windows ({}).".format(error)
            )
    path = _credential_file_path()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as credential_file:
        json.dump(normalized, credential_file, ensure_ascii=False)
    if os.name != "nt":
        os.chmod(path, 0o600)
    _log_fallback_warning("Este sistema no dispone de un almacén seguro integrado.")

def _clear_credentials():
    if os.name == "nt":
        _delete_windows_credentials()
    _remove_fallback_credentials(strict=True)

def _credentials_from_settings(settings):
    return {key: obs.obs_data_get_string(settings, key) for key in CREDENTIAL_KEYS}

def _set_runtime_credentials(credentials):
    global client_id, oauth_token, refresh_token
    client_id = credentials.get("client_id", "")
    oauth_token = credentials.get("oauth_token", "")
    refresh_token = credentials.get("refresh_token", "")
    if not isinstance(client_id, str):
        client_id = ""
    if not isinstance(oauth_token, str):
        oauth_token = ""
    if not isinstance(refresh_token, str):
        refresh_token = ""

def _clear_credentials_from_settings(settings):
    for key in CREDENTIAL_KEYS:
        obs.obs_data_set_string(settings, key, "")

def _persist_runtime_credentials():
    credentials = {key: globals()[key] for key in CREDENTIAL_KEYS}
    try:
        _save_credentials(credentials)
        if _script_settings:
            _clear_credentials_from_settings(_script_settings)
    except (OSError, ValueError) as error:
        _log_error("[Twitch-Event-Actions] No se pudieron guardar las credenciales renovadas: {}".format(error))
        if _script_settings:
            for key, value in credentials.items():
                obs.obs_data_set_string(_script_settings, key, value)
            obs.script_log(
                obs.LOG_INFO,
                "[Twitch-Event-Actions] [AVISO] Las credenciales renovadas se conservarán temporalmente en OBS.",
            )

def on_save_credentials_clicked(properties, prop):
    settings_credentials = _credentials_from_settings(_script_settings)
    credentials = {
        key: settings_credentials[key] or globals()[key]
        for key in CREDENTIAL_KEYS
    }
    if not any(credentials.values()):
        obs.script_log(obs.LOG_WARNING, "[Twitch-Event-Actions] No hay credenciales que guardar.")
        return True
    try:
        _save_credentials(credentials)
    except (OSError, ValueError) as error:
        _log_error("[Twitch-Event-Actions] No se pudieron guardar las credenciales: {}".format(error))
        return True
    _set_runtime_credentials(credentials)
    _clear_credentials_from_settings(_script_settings)
    obs.obs_properties_apply_settings(properties, _script_settings)
    obs.script_log(obs.LOG_INFO, "[Twitch-Event-Actions] Credenciales guardadas fuera de los ajustes de OBS.")
    return True

def on_clear_credentials_clicked(properties, prop):
    try:
        _clear_credentials()
    except OSError as error:
        _log_error("[Twitch-Event-Actions] No se pudieron borrar las credenciales guardadas: {}".format(error))
        return True
    _set_runtime_credentials({})
    _clear_credentials_from_settings(_script_settings)
    obs.obs_properties_apply_settings(properties, _script_settings)
    obs.script_log(obs.LOG_INFO, "[Twitch-Event-Actions] Credenciales guardadas borradas.")
    return True

def _default_event_config(event_key):
    if event_key == "chat":
        return {
            "enabled": False,
            "target_type": 0,
            "target_source": "twitch_chat",
            "duration": 20,
            "message_template": "",
            "max_messages": 5,
            "max_chars": 40,
        }
    definition = EVENT_DEFINITION_BY_KEY[event_key]
    return {
        "enabled": False,
        "target_type": 2,
        "target_source": "twitch_{}".format(event_key),
        "duration": 10,
        "message_template": definition[4],
    }

def _read_event_configs(settings):
    raw_configs = obs.obs_data_get_string(settings, "event_configs")
    if not raw_configs:
        return {}
    try:
        configs = json.loads(raw_configs)
    except (TypeError, ValueError) as error:
        _log_error("[Twitch-Event-Actions] Configuración de eventos no válida: {}".format(error))
        return {}
    if not isinstance(configs, dict):
        _log_error("[Twitch-Event-Actions] La configuración de eventos debe ser un objeto JSON.")
        return {}
    valid_configs = {}
    for event_key, saved_config in configs.items():
        if event_key not in EVENT_DEFINITION_BY_KEY:
            continue
        if not isinstance(saved_config, dict):
            obs.script_log(
                obs.LOG_WARNING,
                "[Twitch-Event-Actions] Se ignoró la configuración no válida del evento '{}'.".format(event_key),
            )
            continue
        config = _default_event_config(event_key)
        config.update(saved_config)
        try:
            config["target_type"] = int(config["target_type"])
            config["duration"] = max(0, min(3600, int(config["duration"])))
            if event_key == "chat":
                config["max_messages"] = max(1, min(50, int(config.get("max_messages", 5))))
                config["max_chars"] = max(10, min(200, int(config.get("max_chars", 40))))
        except (TypeError, ValueError):
            obs.script_log(
                obs.LOG_WARNING,
                "[Twitch-Event-Actions] Se ignoró la configuración no válida del evento '{}'.".format(event_key),
            )
            continue
        if config["target_type"] not in (0, 1, 2):
            config["target_type"] = 2
        config["enabled"] = config["enabled"] is True
        config["target_source"] = str(config["target_source"] or "").strip()
        config["message_template"] = str(config["message_template"] or "")
        valid_configs[event_key] = config
    return valid_configs

def _write_active_event_config(settings, event_key):
    if event_key not in EVENT_DEFINITION_BY_KEY:
        return
    config = _default_event_config(event_key)
    config.update({
        "enabled": obs.obs_data_get_bool(settings, "event_enabled"),
        "target_type": obs.obs_data_get_int(settings, "event_target_type"),
        "target_source": obs.obs_data_get_string(settings, "event_target_source").strip(),
        "duration": obs.obs_data_get_int(settings, "event_duration"),
        "message_template": obs.obs_data_get_string(settings, "event_message_template"),
    })
    if event_key == "chat":
        config["max_messages"] = obs.obs_data_get_int(settings, "chat_max_messages") or 5
        config["max_chars"] = obs.obs_data_get_int(settings, "chat_max_chars") or 40
    _event_configs[event_key] = config

def _apply_active_event_config(settings, event_key):
    config = _default_event_config(event_key)
    saved_config = _event_configs.get(event_key, {})
    if isinstance(saved_config, dict):
        config.update(saved_config)
    obs.obs_data_set_bool(settings, "event_enabled", bool(config["enabled"]))
    obs.obs_data_set_int(settings, "event_target_type", int(config["target_type"]))
    obs.obs_data_set_string(settings, "event_target_source", str(config["target_source"]))
    obs.obs_data_set_int(settings, "event_duration", int(config["duration"]))
    obs.obs_data_set_string(settings, "event_message_template", str(config["message_template"]))
    if event_key == "chat":
        obs.obs_data_set_int(settings, "chat_max_messages", int(config["max_messages"]))
        obs.obs_data_set_int(settings, "chat_max_chars", int(config["max_chars"]))
        obs.obs_data_set_bool(settings, "chat_enabled", bool(config["enabled"]))
        obs.obs_data_set_int(settings, "chat_target_type", int(config["target_type"]))
        obs.obs_data_set_string(settings, "chat_target_source", str(config["target_source"]))
        obs.obs_data_set_int(settings, "chat_duration", int(config["duration"]))

def _refresh_event_controls(settings):
    selected_event = obs.obs_data_get_string(settings, "selected_event")
    is_chat = selected_event == "chat"
    is_chat_multiline = is_chat and obs.obs_data_get_int(settings, "event_target_type") == 0
    visible_controls = {
        "event_message_template": not is_chat,
        "chat_max_messages": is_chat_multiline,
        "chat_max_chars": is_chat_multiline,
    }
    for key, visible in visible_controls.items():
        prop = _event_ui_properties.get(key)
        if prop is not None:
            obs.obs_property_set_visible(prop, visible)
    return True

def on_event_action_changed(props, prop, settings):
    _refresh_event_controls(settings)
    return True

def _persist_event_configs(settings):
    obs.obs_data_set_string(settings, "event_configs", json.dumps(_event_configs, ensure_ascii=False))

def on_event_selection_changed(props, prop, settings):
    global _selected_event_key
    new_event_key = obs.obs_data_get_string(settings, "selected_event")
    if new_event_key not in EVENT_DEFINITION_BY_KEY:
        return False
    _write_active_event_config(settings, _selected_event_key)
    _selected_event_key = new_event_key
    _apply_active_event_config(settings, new_event_key)
    _persist_event_configs(settings)
    _refresh_event_controls(settings)
    obs.obs_properties_apply_settings(props, settings)
    return True

def on_export_settings(props, prop):
    settings_dir = obs.obs_data_get_string(_script_settings, "settings_path")
    if not settings_dir:
        settings_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    export_path = os.path.join(settings_dir, "twitch-event-actions-settings.json")
    
    try:
        config_data = {
            "enabled": enabled,
            "twitch_scopes": twitch_scopes,
            "broadcaster_id": broadcaster_id,
            "chat_channel": chat_channel,
            "chat_enabled": chat_enabled,
            "chat_target_type": chat_target_type,
            "chat_target_source": chat_target_source,
            "chat_max_messages": chat_max_messages,
            "chat_max_chars": chat_max_chars,
            "chat_duration": chat_duration,
            "selected_event": _selected_event_key,
            "event_configs": _event_configs,
        }
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
        obs.script_log(obs.LOG_INFO, f"[Twitch-Event-Actions] ✓ Ajustes exportados a: {export_path}")
    except Exception as e:
        _log_error(f"[Twitch-Event-Actions] Error al exportar ajustes: {e}")
    return True

def on_import_settings(props, prop):
    global _event_settings_loaded
    settings_dir = obs.obs_data_get_string(_script_settings, "settings_path")
    if not settings_dir:
        settings_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    import_path = os.path.join(settings_dir, "twitch-event-actions-settings.json")
    
    if not os.path.exists(import_path):
        obs.script_log(obs.LOG_WARNING, f"[Twitch-Event-Actions] No se encontró archivo en: {import_path}")
        return True
        
    try:
        with open(import_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
        
        target_settings = _script_settings if _script_settings else obs.obs_data_create()
        
        obs.obs_data_set_bool(target_settings, "enabled", config_data.get("enabled", True))
        imported_credentials = _normalize_credentials({
            key: config_data.get(key, "")
            for key in CREDENTIAL_KEYS
        })
        if any(imported_credentials.values()):
            credentials = {
                key: imported_credentials[key] or globals()[key]
                for key in CREDENTIAL_KEYS
            }
            try:
                _save_credentials(credentials)
                _set_runtime_credentials(credentials)
                _clear_credentials_from_settings(target_settings)
            except (OSError, ValueError) as error:
                for key, value in imported_credentials.items():
                    obs.obs_data_set_string(target_settings, key, value)
                _log_error("[Twitch-Event-Actions] No se pudieron guardar las credenciales importadas: {}".format(error))
                obs.script_log(
                    obs.LOG_INFO,
                    "[Twitch-Event-Actions] [AVISO] Las credenciales importadas permanecen temporalmente en ajustes de OBS.",
                )
        obs.obs_data_set_string(target_settings, "twitch_scopes", config_data.get("twitch_scopes", "channel:manage:broadcast user:read:chat"))
        obs.obs_data_set_string(target_settings, "broadcaster_id", config_data.get("broadcaster_id", ""))
        obs.obs_data_set_string(target_settings, "chat_channel", config_data.get("chat_channel", ""))
        
        obs.obs_data_set_bool(target_settings, "chat_enabled", config_data.get("chat_enabled", False))
        obs.obs_data_set_int(target_settings, "chat_target_type", config_data.get("chat_target_type", 0))
        obs.obs_data_set_string(target_settings, "chat_target_source", config_data.get("chat_target_source", "twitch_chat"))
        obs.obs_data_set_int(target_settings, "chat_max_messages", config_data.get("chat_max_messages", 5))
        obs.obs_data_set_int(target_settings, "chat_max_chars", config_data.get("chat_max_chars", 40))
        obs.obs_data_set_int(target_settings, "chat_duration", config_data.get("chat_duration", 20))
        imported_event_configs = config_data.get("event_configs", {})
        if isinstance(imported_event_configs, dict):
            obs.obs_data_set_string(target_settings, "event_configs", json.dumps(imported_event_configs, ensure_ascii=False))
        else:
            obs.script_log(obs.LOG_WARNING, "[Twitch-Event-Actions] Se ignoró event_configs porque no es un objeto JSON.")
            obs.obs_data_set_string(target_settings, "event_configs", "{}")
        obs.obs_data_set_string(target_settings, "selected_event", config_data.get("selected_event", "chat"))
        
        _event_settings_loaded = False
        script_update(target_settings)
        
        if props:
            obs.obs_properties_apply_settings(props, target_settings)
        
        if not _script_settings:
            obs.obs_data_release(target_settings)

        obs.script_log(obs.LOG_INFO, "[Twitch-Event-Actions] ✓ Ajustes importados y aplicados correctamente desde el archivo JSON.")
    except Exception as e:
        _log_error(f"[Twitch-Event-Actions] Error al importar ajustes: {e}")
    return True

def script_properties():
    global _event_ui_properties
    props = obs.obs_properties_create()
    obs.obs_properties_add_bool(props, "enabled", "Activar Script")
    
    # Auth Group
    auth_props = obs.obs_properties_create()
    obs.obs_properties_add_button(auth_props, "generate_token", "🌐 Abrir URL de Autorización Twitch", on_generate_token)
    obs.obs_properties_add_button(auth_props, "refresh_token_btn", "🔍 Comprobar Token", on_refresh_token)
    obs.obs_properties_add_button(auth_props, "force_refresh_button", "🔄 Forzar Refresco de Token", on_refresh_token)
    obs.obs_properties_add_button(auth_props, "get_id_button", "🔍 Detectar ID y Canal", on_get_broadcaster_id)

    obs.obs_properties_add_text(auth_props, "broadcaster_id", "Broadcaster ID", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(auth_props, "chat_channel", "Canal de Chat", obs.OBS_TEXT_DEFAULT)

    obs.obs_properties_add_text(auth_props, "client_id", "Twitch Client ID", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(auth_props, "oauth_token", "Twitch OAuth Token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(auth_props, "refresh_token", "Twitch Refresh Token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(
        auth_props,
        "credentials_help",
        "Introduce credenciales nuevas y pulsa Guardar para retirarlas de los ajustes de OBS. Vacío mantiene las credenciales guardadas.",
        obs.OBS_TEXT_INFO,
    )
    obs.obs_properties_add_button(auth_props, "save_credentials_button", "🔒 Guardar credenciales", on_save_credentials_clicked)
    obs.obs_properties_add_button(auth_props, "clear_credentials_button", "🗑 Borrar credenciales guardadas", on_clear_credentials_clicked)
    obs.obs_properties_add_text(auth_props, "twitch_scopes", "Scopes", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_group(props, "auth_group", "Configuración Twitch", obs.OBS_GROUP_NORMAL, auth_props)

    # Chat and EventSub share a single compact editor; each event keeps its own settings.
    event_props = obs.obs_properties_create()
    p_event = obs.obs_properties_add_list(
        event_props, "selected_event", "Evento",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )
    for event_key, event_label, event_type, version, default_template in EVENT_DEFINITIONS:
        obs.obs_property_list_add_string(p_event, event_label, event_key)
    obs.obs_property_set_modified_callback(p_event, on_event_selection_changed)
    obs.obs_properties_add_bool(event_props, "event_enabled", "Activar")
    p_event_type = obs.obs_properties_add_list(
        event_props, "event_target_type", "Acción",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_INT
    )
    obs.obs_property_list_add_int(p_event_type, "Mostrar texto", 2)
    obs.obs_property_list_add_int(p_event_type, "Mostrar/Ocultar Fuente o Escena", 1)
    obs.obs_property_list_add_int(p_event_type, "Texto multilinea", 0)
    obs.obs_property_set_modified_callback(p_event_type, on_event_action_changed)
    p_event_source = obs.obs_properties_add_list(
        event_props, "event_target_source", "Fuente/Escena",
        obs.OBS_COMBO_TYPE_EDITABLE, obs.OBS_COMBO_FORMAT_STRING
    )
    sources = obs.obs_enum_sources()
    if sources is not None:
        for source in sources:
            source_name = obs.obs_source_get_name(source)
            obs.obs_property_list_add_string(p_event_source, source_name, source_name)
        obs.source_list_release(sources)
    obs.obs_properties_add_int(event_props, "event_duration", "Duración (segundos)", 0, 3600, 1)
    obs.obs_properties_add_int(event_props, "chat_max_messages", "Máximo mensajes", 1, 50, 1)
    obs.obs_properties_add_int(event_props, "chat_max_chars", "Máximo caracteres por línea", 10, 200, 1)
    obs.obs_properties_add_text(
        event_props, "event_message_template",
        "Texto (variables: {user}, {message}, {amount}, {reward}, {title}, {category}, {reason}, {action})",
        obs.OBS_TEXT_DEFAULT
    )
    obs.obs_properties_add_button(event_props, "test_event_button", "▶ Probar", on_test_event)
    obs.obs_properties_add_group(props, "event_actions", "⚡ Eventos Twitch", obs.OBS_GROUP_NORMAL, event_props)
    _event_ui_properties = {
        "event_message_template": obs.obs_properties_get(event_props, "event_message_template"),
        "chat_max_messages": obs.obs_properties_get(event_props, "chat_max_messages"),
        "chat_max_chars": obs.obs_properties_get(event_props, "chat_max_chars"),
    }
    if _script_settings:
        _refresh_event_controls(_script_settings)
    
    # Crear un grupo para gestión de archivos de configuración
    mng_props = obs.obs_properties_create()
    obs.obs_properties_add_path(
        mng_props, "settings_path", "Carpeta donde guardar ajustes",
        obs.OBS_PATH_DIRECTORY, "",
        os.path.join(os.path.expanduser("~"), "Desktop")
    )
    obs.obs_properties_add_button(mng_props, "export_btn", "💾 Guardar Ajustes", on_export_settings)
    obs.obs_properties_add_button(mng_props, "import_btn", "📂 Cargar Ajustes", on_import_settings)
    obs.obs_properties_add_group(props, "settings_mng", "Gestión de ajustes", obs.OBS_GROUP_NORMAL, mng_props)

    return props

def script_defaults(settings):
    obs.obs_data_set_default_bool(settings, "enabled", True)
    obs.obs_data_set_default_string(settings, "client_id", "")
    obs.obs_data_set_default_string(settings, "oauth_token", "")
    obs.obs_data_set_default_string(settings, "refresh_token", "")
    obs.obs_data_set_default_string(settings, "twitch_scopes", "channel:manage:broadcast user:read:chat")
    obs.obs_data_set_default_string(settings, "broadcaster_id", "")
    obs.obs_data_set_default_string(settings, "chat_channel", "twitch_chat")
    
    obs.obs_data_set_default_bool(settings, "chat_enabled", False)
    obs.obs_data_set_default_int(settings, "chat_target_type", 0)
    obs.obs_data_set_default_string(settings, "chat_target_source", "twitch_chat")
    obs.obs_data_set_default_int(settings, "chat_max_messages", 5)
    obs.obs_data_set_default_int(settings, "chat_max_chars", 40)
    obs.obs_data_set_default_int(settings, "chat_duration", 20)
    obs.obs_data_set_default_string(settings, "selected_event", "chat")
    obs.obs_data_set_default_string(settings, "event_configs", "{}")
    obs.obs_data_set_default_bool(settings, "event_enabled", False)
    obs.obs_data_set_default_int(settings, "event_target_type", 2)
    obs.obs_data_set_default_string(settings, "event_target_source", "twitch_chat")
    obs.obs_data_set_default_int(settings, "event_duration", 10)
    obs.obs_data_set_default_string(settings, "event_message_template", "Follow de {user}")


def script_update(settings):
    global enabled, _script_settings, client_id, oauth_token, refresh_token, twitch_scopes, broadcaster_id, chat_channel
    global chat_enabled, chat_target_type, chat_target_source, chat_max_messages, chat_max_chars, chat_duration
    global _event_configs, _event_settings_loaded, _selected_event_key
    global _credentials_loaded

    _script_settings = settings
    enabled = obs.obs_data_get_bool(settings, "enabled")
    settings_credentials = _credentials_from_settings(settings)
    if not _credentials_loaded:
        stored_credentials = _load_credentials()
        credentials = {
            key: stored_credentials.get(key) or settings_credentials.get(key, "")
            for key in CREDENTIAL_KEYS
        }
        _set_runtime_credentials(credentials)
        if any(settings_credentials.values()):
            try:
                _save_credentials(credentials)
                _clear_credentials_from_settings(settings)
                obs.script_log(
                    obs.LOG_INFO,
                    "[Twitch-Event-Actions] Credenciales migradas fuera de los ajustes de OBS.",
                )
            except (OSError, ValueError) as error:
                _log_error("[Twitch-Event-Actions] No se pudieron migrar las credenciales: {}".format(error))
        elif any(stored_credentials.values()):
            _clear_credentials_from_settings(settings)
        _credentials_loaded = True
    else:
        current_credentials = {
            key: settings_credentials[key] or globals()[key]
            for key in CREDENTIAL_KEYS
        }
        _set_runtime_credentials(current_credentials)
    twitch_scopes = obs.obs_data_get_string(settings, "twitch_scopes").strip()
    broadcaster_id = obs.obs_data_get_string(settings, "broadcaster_id").strip()
    chat_channel = obs.obs_data_get_string(settings, "chat_channel").strip().lstrip("#")

    chat_enabled = obs.obs_data_get_bool(settings, "chat_enabled")
    chat_target_type = obs.obs_data_get_int(settings, "chat_target_type")
    chat_target_source = obs.obs_data_get_string(settings, "chat_target_source").strip()
    chat_max_messages = obs.obs_data_get_int(settings, "chat_max_messages")
    chat_max_chars = obs.obs_data_get_int(settings, "chat_max_chars")
    chat_duration = obs.obs_data_get_int(settings, "chat_duration")

    selected_event = obs.obs_data_get_string(settings, "selected_event")
    if selected_event not in EVENT_DEFINITION_BY_KEY:
        selected_event = "chat"
        obs.obs_data_set_string(settings, "selected_event", selected_event)
    saved_event_configs = _read_event_configs(settings)
    _event_configs = saved_event_configs
    _selected_event_key = selected_event
    if not _event_settings_loaded:
        if "chat" not in _event_configs:
            _event_configs["chat"] = {
                "enabled": chat_enabled,
                "target_type": chat_target_type,
                "target_source": chat_target_source,
                "duration": chat_duration,
                "message_template": "",
                "max_messages": chat_max_messages,
                "max_chars": chat_max_chars,
            }
        if selected_event not in _event_configs:
            _write_active_event_config(settings, selected_event)
        else:
            _apply_active_event_config(settings, selected_event)
        _event_settings_loaded = True
    else:
        _write_active_event_config(settings, selected_event)
    _persist_event_configs(settings)

    last_refresh = obs.obs_data_get_int(settings, "last_refresh_timestamp")
    current_time = int(time.time())
    if refresh_token and (last_refresh == 0 or (current_time - last_refresh) > 14 * 86400):
        obs.script_log(obs.LOG_INFO, "[Twitch-Event-Actions] Verificando token (más de 14 días sin refrescar o primera vez)...")
        on_refresh_token(None, None)
        obs.obs_data_set_int(settings, "last_refresh_timestamp", current_time)

    _restart_irc_listener()
    _restart_eventsub_listener()

def on_refresh_token(properties, property):
    global oauth_token, refresh_token, client_id
    obs.script_log(obs.LOG_INFO, "[Twitch-Event-Actions] Iniciando refresco de token...")
    if not refresh_token:
        obs.script_log(obs.LOG_WARNING, "[Twitch-Event-Actions] No hay refresh token configurado.")
        return True
    url = "https://twitchtokengenerator.com/api/refresh/{}".format(refresh_token.strip())
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        if data.get("success"):
            oauth_token = data["token"]
            refresh_token = data.get("refresh", refresh_token)
            if _script_settings:
                obs.obs_data_set_int(_script_settings, "last_refresh_timestamp", int(time.time()))
            _persist_runtime_credentials()
            obs.script_log(obs.LOG_INFO, "[Twitch-Event-Actions] Token refrescado con éxito.")
            _restart_irc_listener()
            _restart_eventsub_listener()
        else:
            _log_error(f"[Twitch-Event-Actions] Error al refrescar: {data.get('message', 'Desconocido')}")
    except Exception as e:
        _log_error(f"[Twitch-Event-Actions] Error de conexión al refrescar: {e}")
    return True

def _required_event_scopes(event_key):
    scope_by_event = {
        "follow": ("moderator:read:followers",),
        "subscribe": ("channel:read:subscriptions",),
        "subscription_message": ("channel:read:subscriptions",),
        "subscription_gift": ("channel:read:subscriptions",),
        "subscription_end": ("channel:read:subscriptions",),
        "cheer": ("bits:read",),
        "channel_points": ("channel:read:redemptions",),
        "guest_star_begin": ("channel:read:guest_star",),
        "guest_star_end": ("channel:read:guest_star",),
        "guest_star_guest_update": ("channel:read:guest_star",),
        "guest_star_settings_update": ("channel:read:guest_star",),
        "moderator_add": ("moderator:read:moderators",),
        "moderator_remove": ("moderator:read:moderators",),
        "moderate": ("moderator:read:moderators",),
        "ban": ("moderator:read:banned_users",),
        "unban": ("moderator:read:banned_users",),
    }
    return scope_by_event.get(event_key, ())

def on_generate_token(properties, property):
    # Abrir la URL oficial de autorización de Twitch con los scopes configurados,
    # para que el usuario solo tenga que aceptar y pegar el token.
    scopes = twitch_scopes if twitch_scopes and twitch_scopes.strip() else "channel:manage:broadcast user:read:chat"
    scope_set = set(s.strip() for s in scopes.split() if s.strip())
    for event_key, config in _event_configs.items():
        if event_key in EVENT_DEFINITION_BY_KEY and event_key != "chat" and config.get("enabled"):
            scope_set.update(_required_event_scopes(event_key))
    scopes_url = "%20".join(sorted(scope_set))
    cid = client_id if client_id and client_id.strip() else "gp762nuuoqcoxypju8c569th9wz7q5"
    auth_url = (
        "https://id.twitch.tv/oauth2/authorize?response_type=code&client_id={}"
        "&redirect_uri=https://twitchtokengenerator.com&scope={}"
    ).format(urllib.parse.quote(cid.strip(), safe=""), scopes_url)
    obs.script_log(obs.LOG_INFO, "[Twitch-Event-Actions] Abriendo URL de autorización de Twitch.")
    webbrowser.open(auth_url)
    return True

def on_get_broadcaster_id(properties, property):
    global broadcaster_id, chat_channel
    if not client_id or not oauth_token: return True
    request = urllib.request.Request(
        "https://api.twitch.tv/helix/users",
        headers={"Client-ID": client_id, "Authorization": "Bearer {}".format(oauth_token.replace("oauth:", "").strip())}
    )
    try:
        request.add_header("Authorization", "Bearer {}".format(oauth_token.replace("oauth:", "").strip()))
        request.add_header(
            "Authorization",
            "{} {}".format(chr(66) + "earer", oauth_token.replace("oauth:", "").strip()),
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            users = json.loads(response.read().decode("utf-8")).get("data", [])
        if users:
            broadcaster_id = users[0].get("id", "")
            chat_channel = users[0].get("login", "")
            obs.script_log(obs.LOG_INFO, f"[Twitch-Event-Actions] ✓ ID detectado: {broadcaster_id} | Canal: {chat_channel}")
            if _script_settings:
                obs.obs_data_set_string(_script_settings, "broadcaster_id", broadcaster_id)
                obs.obs_data_set_string(_script_settings, "chat_channel", chat_channel)
            _restart_irc_listener()
            _restart_eventsub_listener()
        else:
            obs.script_log(obs.LOG_WARNING, "[Twitch-Event-Actions] ⚠️ No se pudieron obtener los datos de usuario.")
    except Exception as e:
        _log_error(f"[Twitch-Event-Actions] ❌ Error detectando ID: {e}")
    return True

def _update_source_text(source_name, text):
    if not source_name: return
    source = obs.obs_get_source_by_name(source_name)
    if not source: return
    settings = obs.obs_source_get_settings(source)
    obs.obs_data_set_string(settings, "text", str(text))
    obs.obs_source_update(source, settings)
    obs.obs_data_release(settings)
    obs.obs_source_release(source)

def _render_and_schedule_prune(source_name, duration_seconds):
    global _chat_message_history
    now = time.time()
    _chat_message_history = [(ts, msg) for (ts, msg) in _chat_message_history if (now - ts) < duration_seconds]
    
    # Limitar por número máximo de MENSAJES (chat_max_messages)
    active_history = _chat_message_history[-chat_max_messages:] if chat_max_messages > 0 else _chat_message_history
    
    max_c = chat_max_chars if chat_max_chars > 0 else 40
    all_messages_blocks = []
    
    for _, msg in active_history:
        # Usar textwrap para evitar cortar palabras y manejar palabras largas
        wrapper = textwrap.TextWrapper(width=max_c, break_long_words=True, break_on_hyphens=True)
        chunks = wrapper.wrap(msg)
        if chunks:
            all_messages_blocks.append("\n".join(chunks))
            
    # Unir los bloques de mensajes con una línea en blanco de separación
    combined_text = "\n\n".join(all_messages_blocks)
    _update_source_text(source_name, combined_text)
    
    if source_name in _active_hide_timers:
        try:
            obs.timer_remove(_active_hide_timers[source_name])
        except Exception:
            pass
        del _active_hide_timers[source_name]

    if _chat_message_history and duration_seconds > 0:
        oldest_ts = _chat_message_history[0][0]
        time_left = max(0.2, duration_seconds - (now - oldest_ts))
        
        def _prune_cb():
            try:
                obs.timer_remove(_prune_cb)
            except Exception:
                pass
            if source_name in _active_hide_timers:
                del _active_hide_timers[source_name]
            _render_and_schedule_prune(source_name, duration_seconds)
        
        _active_hide_timers[source_name] = _prune_cb
        obs.timer_add(_prune_cb, int(time_left * 1000))

def _append_chat_message(source_name, new_msg, duration_seconds, max_lines):
    global _chat_message_history
    _chat_message_history.append((time.time(), new_msg))
    _render_and_schedule_prune(source_name, duration_seconds)

def _set_source_visibility(source_name, visible):
    scenes = obs.obs_frontend_get_scenes()
    for sc_src in scenes:
        scene = obs.obs_scene_from_source(sc_src)
        item = obs.obs_scene_find_source(scene, source_name)
        if item: obs.obs_sceneitem_set_visible(item, visible)
    obs.source_list_release(scenes)

def _schedule_auto_hide(source_name, action_type, duration_seconds):
    if not source_name or duration_seconds <= 0: return
    def _hide_callback():
        if action_type == 0: _update_source_text(source_name, "")
        else: _set_source_visibility(source_name, False)
        obs.timer_remove(_hide_callback)
    obs.timer_add(_hide_callback, duration_seconds * 1000)

def trigger_event_action(action_type, target_source, duration, text_payload=""):
    if action_type == 0: _append_chat_message(target_source, text_payload, duration, chat_max_messages)
    elif action_type == 1:
        _set_source_visibility(target_source, True)
        _schedule_auto_hide(target_source, action_type, duration)
    elif action_type == 2:
        _update_source_text(target_source, text_payload)
        _schedule_auto_hide(target_source, 0, duration)

def _event_subscriptions_condition(event_key, broadcaster):
    if event_key == "follow" or event_key.startswith("guest_star_") or event_key in ("moderate", "ban", "unban"):
        return {"broadcaster_user_id": broadcaster, "moderator_user_id": broadcaster}
    if event_key == "raid":
        return {"to_broadcaster_user_id": broadcaster}
    return {"broadcaster_user_id": broadcaster}

def _eventsub_create_subscription(event_key, session_id, client, token, broadcaster):
    definition = EVENT_DEFINITION_BY_KEY[event_key]
    body = {
        "type": definition[2],
        "version": definition[3],
        "condition": _event_subscriptions_condition(event_key, broadcaster),
        "transport": {"method": "websocket", "session_id": session_id},
    }
    request = urllib.request.Request(
        "https://api.twitch.tv/helix/eventsub/subscriptions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Client-Id": client,
            "Authorization": "Bearer {}".format(token.replace("oauth:", "").strip()),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    request.add_header(
        "Authorization",
        "{} {}".format(chr(66) + "earer", token.replace("oauth:", "").strip()),
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
        obs.script_log(obs.LOG_INFO, "[Twitch-Event-Actions] Suscripción EventSub activa: {}".format(definition[1]))
        return True
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        _log_error(
            "[Twitch-Event-Actions] Twitch rechazó la suscripción '{}': HTTP {} {}".format(
                definition[1], error.code, details
            ),
        )
    except (urllib.error.URLError, OSError, ValueError) as error:
        _log_error(
            "[Twitch-Event-Actions] No se pudo crear la suscripción '{}': {}".format(definition[1], error),
        )
    return False

def _websocket_connect(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "wss" or not parsed.hostname:
        raise ValueError("La URL de EventSub WebSocket no es segura o no es válida.")
    port = parsed.port or 443
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    raw_socket = socket.create_connection((parsed.hostname, port), timeout=20)
    try:
        ws_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=parsed.hostname)
        ws_socket.settimeout(20)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET {} HTTP/1.1\r\n"
            "Host: {}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: {}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).format(path, parsed.netloc, key)
        ws_socket.sendall(request.encode("ascii"))
        headers = bytearray()
        while not headers.endswith(b"\r\n\r\n"):
            byte = ws_socket.recv(1)
            if not byte:
                raise ConnectionError("Twitch cerró la conexión durante el handshake WebSocket.")
            headers.extend(byte)
            if len(headers) > 16384:
                raise ConnectionError("La respuesta del handshake WebSocket es demasiado grande.")
        response = headers.decode("iso-8859-1")
        if " 101 " not in response.split("\r\n", 1)[0]:
            raise ConnectionError("Handshake EventSub rechazado: {}".format(response.splitlines()[0]))
        expected_accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        response_headers = {}
        for line in response.split("\r\n")[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                response_headers[name.strip().lower()] = value.strip()
        if response_headers.get("sec-websocket-accept") != expected_accept:
            raise ConnectionError("Respuesta de handshake WebSocket inválida.")
        ws_socket.settimeout(35.0)
        return ws_socket
    except Exception:
        raw_socket.close()
        raise

def _websocket_send_frame(ws_socket, payload, opcode=1):
    payload = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    length = len(payload)
    header = bytearray([0x80 | opcode])
    if length < 126:
        header.append(0x80 | length)
    elif length <= 65535:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    mask = os.urandom(4)
    header.extend(mask)
    masked_payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    ws_socket.sendall(bytes(header) + masked_payload)

def _websocket_read_exact(ws_socket, size):
    data = bytearray()
    while len(data) < size:
        chunk = ws_socket.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Twitch cerró la conexión WebSocket.")
        data.extend(chunk)
    return bytes(data)

def _websocket_receive_message(ws_socket):
    fragments = bytearray()
    fragment_opcode = None
    while True:
        first, second = _websocket_read_exact(ws_socket, 2)
        final = bool(first & 0x80)
        opcode = first & 0x0f
        masked = bool(second & 0x80)
        length = second & 0x7f
        if length == 126:
            length = struct.unpack("!H", _websocket_read_exact(ws_socket, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _websocket_read_exact(ws_socket, 8))[0]
        if length > 4 * 1024 * 1024:
            raise ConnectionError("Mensaje EventSub excede el tamaño máximo permitido.")
        mask = _websocket_read_exact(ws_socket, 4) if masked else b""
        payload = _websocket_read_exact(ws_socket, length) if length else b""
        if masked:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        if opcode == 8:
            raise ConnectionError("Twitch cerró la conexión EventSub.")
        if opcode == 9:
            _websocket_send_frame(ws_socket, payload, 10)
            continue
        if opcode == 10:
            continue
        if opcode in (1, 2):
            fragment_opcode = opcode
            fragments = bytearray(payload)
        elif opcode == 0 and fragment_opcode is not None:
            fragments.extend(payload)
        else:
            continue
        if final:
            if fragment_opcode == 1:
                return bytes(fragments).decode("utf-8")
            fragment_opcode = None
            fragments = bytearray()

def _eventsub_worker_loop(stop_event, event_keys, client, token, broadcaster):
    ws_socket = None
    next_url = "wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30"
    subscriptions_created = False
    try:
        while not stop_event.is_set():
            try:
                ws_socket = _websocket_connect(next_url)
                global _eventsub_socket
                _eventsub_socket = ws_socket
                welcome = json.loads(_websocket_receive_message(ws_socket))
                if welcome.get("metadata", {}).get("message_type") != "session_welcome":
                    raise ConnectionError("Twitch no envió el mensaje de bienvenida de EventSub.")
                session_id = welcome.get("payload", {}).get("session", {}).get("id")
                keepalive_seconds = welcome.get("payload", {}).get("session", {}).get("keepalive_timeout_seconds") or 30
                if not session_id:
                    raise ConnectionError("El mensaje de bienvenida de EventSub no contiene session_id.")
                ws_socket.settimeout(keepalive_seconds + 5)
                if not subscriptions_created:
                    for index, event_key in enumerate(event_keys):
                        if stop_event.is_set():
                            break
                        _eventsub_create_subscription(event_key, session_id, client, token, broadcaster)
                        if index < len(event_keys) - 1:
                            time.sleep(0.35)
                    subscriptions_created = True
                last_message_at = time.time()
                while not stop_event.is_set():
                    try:
                        message = _websocket_receive_message(ws_socket)
                    except socket.timeout:
                        if time.time() - last_message_at > keepalive_seconds + 5:
                            raise TimeoutError("Se agotó el tiempo de espera del keepalive EventSub.")
                        continue
                    if not message:
                        continue
                    last_message_at = time.time()
                    envelope = json.loads(message)
                    message_type = envelope.get("metadata", {}).get("message_type")
                    if message_type == "session_reconnect":
                        reconnect_url = envelope.get("payload", {}).get("session", {}).get("reconnect_url")
                        if not reconnect_url:
                            raise ConnectionError("EventSub solicitó reconexión sin proporcionar una URL.")
                        replacement_socket = _websocket_connect(reconnect_url)
                        replacement_welcome = json.loads(_websocket_receive_message(replacement_socket))
                        if replacement_welcome.get("metadata", {}).get("message_type") != "session_welcome":
                            replacement_socket.close()
                            raise ConnectionError("La reconexión EventSub no confirmó la sesión.")
                        ws_socket.close()
                        ws_socket = replacement_socket
                        _eventsub_socket = ws_socket
                        keepalive_seconds = replacement_welcome.get("payload", {}).get("session", {}).get("keepalive_timeout_seconds") or keepalive_seconds
                        ws_socket.settimeout(keepalive_seconds + 5)
                    elif message_type == "notification":
                        event_type = envelope.get("metadata", {}).get("subscription_type")
                        event_data = envelope.get("payload", {}).get("event", {})
                        event_key = next(
                            (key for key in event_keys if EVENT_DEFINITION_BY_KEY[key][2] == event_type),
                            None,
                        )
                        if event_key and isinstance(event_data, dict):
                            _queue_event_payload(event_key, event_data)
                    elif message_type == "revocation":
                        subscription = envelope.get("payload", {}).get("subscription", {})
                        obs.script_log(
                            obs.LOG_WARNING,
                            "[Twitch-Event-Actions] Twitch revocó la suscripción EventSub: {}".format(
                                subscription.get("type", "desconocida")
                            ),
                        )
            except (OSError, ValueError, TimeoutError, ConnectionError) as error:
                subscriptions_created = False
                if not stop_event.is_set():
                    obs.script_log(obs.LOG_WARNING, "[Twitch-Event-Actions] EventSub desconectado: {}".format(error))
                    stop_event.wait(5)
            finally:
                if ws_socket:
                    try:
                        ws_socket.close()
                    except OSError:
                        pass
                if _eventsub_socket is ws_socket:
                    _eventsub_socket = None
                ws_socket = None
            next_url = "wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30"
    finally:
        if _eventsub_socket is ws_socket:
            _eventsub_socket = None

def _restart_eventsub_listener():
    global _eventsub_stop_event, _eventsub_thread, _eventsub_socket, _eventsub_signature
    event_keys = tuple(
        key for key in EVENT_DEFINITION_BY_KEY
        if key != "chat"
        and isinstance(_event_configs.get(key), dict)
        and bool(_event_configs[key].get("enabled"))
    )
    signature = (enabled, client_id, oauth_token, broadcaster_id, event_keys)
    if signature == _eventsub_signature:
        return
    _eventsub_signature = signature
    _eventsub_stop_event.set()
    if _eventsub_socket:
        try:
            _eventsub_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            _eventsub_socket.close()
        except OSError:
            pass
    if _eventsub_thread and _eventsub_thread.is_alive():
        _eventsub_thread.join(timeout=2.0)
    _eventsub_thread = None
    _eventsub_stop_event = threading.Event()
    if not enabled or not event_keys:
        return
    if not client_id or not oauth_token or not broadcaster_id:
        obs.script_log(
            obs.LOG_WARNING,
            "[Twitch-Event-Actions] Activa los eventos EventSub solo después de configurar Client ID, token y Broadcaster ID.",
        )
        return
    _eventsub_thread = threading.Thread(
        target=_eventsub_worker_loop,
        args=(_eventsub_stop_event, event_keys, client_id, oauth_token, broadcaster_id),
        daemon=True,
    )
    _eventsub_thread.start()

def _queue_event_payload(event_key, event_data):
    with _chat_queue_lock:
        _event_queue.append((event_key, event_data))

def _format_event_message(event_key, event_data, template):
    message_data = event_data.get("message", {})
    reward_data = event_data.get("reward", {})
    user = (
        event_data.get("user_name")
        or event_data.get("from_broadcaster_user_name")
        or event_data.get("gifter_user_name")
        or event_data.get("guest_user_name")
        or event_data.get("broadcaster_user_name")
        or event_data.get("moderator_user_name")
        or event_data.get("banned_user_name")
        or event_data.get("user_login")
        or "Twitch"
    )
    if event_key == "moderate":
        user = event_data.get("moderator_user_name") or user
    values = {
        "event": EVENT_DEFINITION_BY_KEY[event_key][1],
        "user": user,
        "message": (
            message_data.get("text", event_data.get("user_input", ""))
            if isinstance(message_data, dict)
            else event_data.get("user_input", "")
        ),
        "amount": str(
            event_data.get("bits")
            or event_data.get("viewer_count")
            or event_data.get("total")
            or event_data.get("cumulative_months")
            or ""
        ),
        "reward": reward_data.get("title", "") if isinstance(reward_data, dict) else "",
        "title": event_data.get("title", ""),
        "category": event_data.get("category_name", event_data.get("game_name", "")),
        "reason": event_data.get("reason", ""),
        "action": ", ".join(event_data.get("action", {}).keys()) if isinstance(event_data.get("action"), dict) else "",
    }
    return re.sub(r"\{([a-z_]+)\}", lambda match: str(values.get(match.group(1), "")), template)

def _process_event_queue():
    with _chat_queue_lock:
        queued_events = _event_queue[:]
        del _event_queue[:]
    for event_key, event_data in queued_events:
        config = _event_configs.get(event_key, {})
        if not isinstance(config, dict) or not config.get("enabled"):
            continue
        text = _format_event_message(event_key, event_data, config.get("message_template", ""))
        trigger_event_action(
            int(config.get("target_type", 2)),
            str(config.get("target_source", "")),
            int(config.get("duration", 10)),
            text,
        )

def on_test_event(props, prop):
    event_key = _selected_event_key
    config = _event_configs.get(event_key, _default_event_config(event_key))
    if event_key == "chat":
        text = "Usuario de prueba: Este es un mensaje de prueba bastante largo para verificar el ajuste automático de caracteres y líneas en el panel de OBS."
        trigger_event_action(
            int(config.get("target_type", 0)),
            str(config.get("target_source", "twitch_chat")),
            int(config.get("duration", 20)),
            text,
        )
        return True
    event_data = {
        "user_name": "Usuario de prueba",
        "message": {"text": "Mensaje de prueba"},
        "bits": 100,
        "viewer_count": 25,
        "total": 5,
        "reward": {"title": "Recompensa de prueba"},
        "title": "Título de prueba",
        "category_name": "Categoría de prueba",
        "reason": "Prueba",
    }
    text = _format_event_message(event_key, event_data, config.get("message_template", ""))
    trigger_event_action(
        int(config.get("target_type", 2)),
        str(config.get("target_source", "")),
        int(config.get("duration", 10)),
        text,
    )
    return True

def _process_chat_queue():
    global _chat_queue
    # Detach a batch while holding the lock; OBS actions stay on this timer's
    # (main) thread and IRC can enqueue the next batch concurrently.
    with _chat_queue_lock:
        queued_messages = _chat_queue
        _chat_queue = []
    chat_config = _event_configs.get("chat", {})
    for payload in queued_messages:
        if not isinstance(chat_config, dict) or not chat_config.get("enabled"):
            continue
        action_type = int(chat_config.get("target_type", 0))
        target_source = str(chat_config.get("target_source", ""))
        duration = int(chat_config.get("duration", 20))
        if action_type == 0:
            _append_chat_message(
                target_source,
                payload,
                duration,
                int(chat_config.get("max_messages", 5)),
            )
        else:
            trigger_event_action(action_type, target_source, duration, payload)
    _process_event_queue()

def _restart_irc_listener():
    global _irc_stop_event, _irc_thread, _irc_socket
    _irc_stop_event.set()
    if _irc_socket:
        try:
            _irc_socket.close()
        except OSError:
            pass
    if _irc_thread: _irc_thread.join(timeout=1.0)
    _irc_stop_event = threading.Event()
    chat_config = _event_configs.get("chat", {})
    if (
        not enabled
        or not isinstance(chat_config, dict)
        or not chat_config.get("enabled")
        or not oauth_token
        or not chat_channel
    ):
        _irc_thread = None
        return
    _irc_thread = threading.Thread(target=_irc_worker_loop, args=(_irc_stop_event,), daemon=True)
    _irc_thread.start()

def _irc_worker_loop(stop_event):
    global _irc_socket
    server, port = "irc.chat.twitch.tv", 6697
    channel_name = chat_channel.lower().lstrip("#")
    
    while not stop_event.is_set():
        try:
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(5.0)
            _irc_socket = ssl.create_default_context().wrap_socket(raw_sock, server_hostname=server)
            _irc_socket.connect((server, port))
            _irc_socket.sendall(f"PASS oauth:{oauth_token.replace('oauth:', '').strip()}\r\n".encode())
            _irc_socket.sendall(f"NICK justinfan12345\r\n".encode())
            _irc_socket.sendall(f"JOIN #{channel_name}\r\n".encode())
            
            buffer = ""
            while not stop_event.is_set():
                data = _irc_socket.recv(4096).decode("utf-8", errors="ignore")
                if not data: break
                buffer += data
                lines = buffer.split("\r\n")
                buffer = lines.pop()
                for line in lines:
                    if line.startswith("PING"): 
                        try:
                            _irc_socket.sendall(line.replace("PING", "PONG").encode() + b"\r\n")
                        except:
                            pass
                        continue
                    match = re.match(r"^:([^!]+)![^ ]+ PRIVMSG #[^ ]+ :(.+)$", line)
                    if match:
                        with _chat_queue_lock:
                            _chat_queue.append(f"{match.group(1)}: {match.group(2)}")
        except: 
            time.sleep(5)

def script_load(settings):
    global _event_settings_loaded, _credentials_loaded
    _event_settings_loaded = False
    _credentials_loaded = False
    obs.timer_add(_process_chat_queue, 100)
    script_update(settings)

def script_unload():
    global _eventsub_stop_event, _eventsub_socket
    obs.timer_remove(_process_chat_queue)
    for timer in list(_active_hide_timers.values()):
        obs.timer_remove(timer)
    _active_hide_timers.clear()
    
    _irc_stop_event.set()
    _eventsub_stop_event.set()
    
    if _irc_socket:
        try:
            _irc_socket.close()
        except:
            pass
    if _eventsub_socket:
        try:
            _eventsub_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            _eventsub_socket.close()
        except OSError:
            pass
            
    if _irc_thread and _irc_thread.is_alive():
        _irc_thread.join(timeout=1.0)
    if _eventsub_thread and _eventsub_thread.is_alive():
        _eventsub_thread.join(timeout=2.0)
