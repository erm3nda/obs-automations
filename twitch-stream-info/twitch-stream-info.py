import obspython as obs
import urllib.request
import urllib.parse
import urllib.error
import json
import threading
import re
import webbrowser
import os
import time
import ctypes
from ctypes import wintypes
import tempfile

PLUGIN_NAME = "Twitch-Stream-Info"
SETTINGS_FILENAME = "twitch-stream-info.json"
ICONS = ["🎮", "🚗", "🏍️", "⚽", "✈️", "🥊", "🔫"]
HTTP_TIMEOUT_SECONDS = 10

# ─── Config defaults ──────────────────────────────────────────────────────────
block_if_streaming   = False   # Casilla: NO cambiar nada si se está emitiendo en directo
update_mode          = 1       # 0: Desactivado, 1: Inmediato, 2: Retardado
delay_seconds        = 60      # Tiempo de retardo en segundos
global_suffix        = ". No commentary. Bring your own music." # Coletilla global por defecto

twitch_client_id     = ""
twitch_client_secret = ""
twitch_oauth_token   = ""
twitch_refresh_token = ""
twitch_scopes        = "channel:manage:broadcast"

_script_settings     = None
is_first_load        = True
_is_importing_settings = False
_fallback_warning_logged = False

CREDENTIAL_TARGET = "Twitch-Stream-Info"
CREDENTIAL_FILE = ".obs-twitch-stream-info-credentials.json"
CREDENTIAL_KEYS = (
    "twitch_client_id",
    "twitch_client_secret",
    "twitch_oauth_token",
    "twitch_refresh_token",
)


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


def _credential_file_path():
    return os.path.join(os.path.expanduser("~"), CREDENTIAL_FILE)


def _log_fallback_warning(reason):
    global _fallback_warning_logged
    if _fallback_warning_logged:
        return
    obs.script_log(
        obs.LOG_INFO,
        f"[{PLUGIN_NAME}] [AVISO] {reason} Las credenciales se guardarán sin cifrar en "
        f"'{_credential_file_path()}'. Protege ese archivo y no lo compartas."
    )
    _fallback_warning_logged = True


def _safe_error(error, credentials=None):
    message = str(error)
    secrets = [globals().get(key, "") for key in CREDENTIAL_KEYS]
    if credentials:
        secrets.extend(credentials.values() if isinstance(credentials, dict) else credentials)
    for secret in secrets:
        if not isinstance(secret, str) or not secret:
            continue
        variants = {
            secret,
            secret.strip(),
            urllib.parse.quote(secret, safe=""),
            urllib.parse.quote_plus(secret),
        }
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                message = message.replace(variant, "[REDACTED]")
    return message


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
        return {
            key: value if isinstance(value, str) else ""
            for key, value in stored.items()
            if key in CREDENTIAL_KEYS
        }
    finally:
        advapi32.CredFree(credential)


def _write_windows_credentials(credentials):
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL

    blob = json.dumps(credentials, ensure_ascii=False).encode("utf-8")
    if len(blob) > 2560:
        raise ValueError("Credential data exceeds the Windows Credential Manager limit")
    blob_buffer = ctypes.create_string_buffer(blob)
    credential = _Credential()
    credential.Type = 1
    credential.TargetName = CREDENTIAL_TARGET
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(
        blob_buffer, ctypes.POINTER(ctypes.c_ubyte)
    )
    credential.Persist = 2
    credential.UserName = PLUGIN_NAME
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
    _log_fallback_warning("El sistema de credenciales seguro no está disponible.")
    try:
        if os.name != "nt":
            os.chmod(path, 0o600)
        with open(path, "r", encoding="utf-8") as credential_file:
            stored = json.load(credential_file)
        if not isinstance(stored, dict):
            raise ValueError("Credential file must contain a JSON object")
        return {
            key: value if isinstance(value, str) else ""
            for key, value in stored.items()
            if key in CREDENTIAL_KEYS
        }
    except (OSError, ValueError, json.JSONDecodeError) as error:
        obs.script_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] [ERROR] No se pudieron leer las credenciales locales: {_safe_error(error)}"
        )
        return {}


def _write_fallback_credentials(credentials):
    path = _credential_file_path()
    temp_path = None
    try:
        descriptor, temp_path = tempfile.mkstemp(
            prefix=CREDENTIAL_FILE + ".",
            suffix=".tmp",
            dir=os.path.dirname(path)
        )
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as credential_file:
            json.dump(credentials, credential_file, ensure_ascii=False)
            credential_file.flush()
            os.fsync(credential_file.fileno())
        os.replace(temp_path, path)
    except OSError as error:
        obs.script_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] [ERROR] No se pudieron guardar las credenciales locales: {_safe_error(error)}"
        )
        raise
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
    _log_fallback_warning("Este sistema no dispone de un almacén seguro integrado.")


def _remove_fallback_credentials():
    try:
        os.remove(_credential_file_path())
    except FileNotFoundError:
        pass
    except OSError as error:
        obs.script_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] [AVISO] No se pudo borrar el archivo de fallback: {_safe_error(error)}"
        )
        raise


def _load_credentials():
    if os.name == "nt":
        try:
            credentials = _read_windows_credentials()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            _log_fallback_warning(
                f"No se pudo acceder al Administrador de credenciales de Windows ({_safe_error(error)})."
            )
        else:
            if credentials is not None:
                try:
                    _remove_fallback_credentials()
                except OSError:
                    pass
                return credentials
    return _read_fallback_credentials()


def _save_credentials(credentials):
    normalized = {
        key: credentials.get(key, "") if isinstance(credentials.get(key, ""), str) else ""
        for key in CREDENTIAL_KEYS
    }
    if os.name == "nt":
        try:
            _write_windows_credentials(normalized)
        except (OSError, ValueError) as error:
            _log_fallback_warning(
                f"No se pudo guardar en el Administrador de credenciales de Windows ({_safe_error(error)})."
            )
        else:
            _remove_fallback_credentials()
            return
    _write_fallback_credentials(normalized)


def _clear_credentials():
    if os.name == "nt":
        _delete_windows_credentials()
    _remove_fallback_credentials()


def _persist_runtime_credentials():
    credentials = {
        key: globals()[key]
        for key in CREDENTIAL_KEYS
    }
    try:
        _save_credentials(credentials)
    except (OSError, ValueError) as error:
        obs.script_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] [ERROR] No se pudieron persistir las credenciales renovadas: {_safe_error(error)}"
        )
        return False
    return True


def clean_scene_name(name):
    """Extrae el nombre limpio del juego a partir de la escena."""
    if not name:
        return ""
    name = re.sub(r'[\u2122\u00ae\u00a9]', '', name)
    name = re.sub(r'\s*\((tm|r|c)\)\s*', ' ', name, flags=re.IGNORECASE)
    name = re.sub(r'[<>:"/\\|?*]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    name = re.sub(r'\s*escene\s*$', '', name, flags=re.IGNORECASE).strip()
    return name if name else "Juego"

def get_active_scene_name():
    sc = obs.obs_frontend_get_current_scene()
    if not sc:
        return ""
    name = obs.obs_source_get_name(sc)
    obs.obs_source_release(sc)
    return name

def get_scene_names():
    scenes = obs.obs_frontend_get_scenes()
    if not scenes:
        return []
    try:
        return [obs.obs_source_get_name(scene) for scene in scenes]
    finally:
        obs.source_list_release(scenes)

def on_scene_selection_changed(props, prop, settings):
    scene_name = obs.obs_data_get_string(settings, "selected_scene")
    title, category = get_scene_data_from_obs(settings, scene_name)
    obs.obs_data_set_string(settings, "current_scene_title", title)
    obs.obs_data_set_string(settings, "current_scene_category", category)
    obs.obs_properties_apply_settings(props, settings)
    return True

def get_scene_data_from_obs(settings, scene_name):
    """Obtiene el título y la categoría de una escena guardada en las settings nativas de OBS."""
    if not settings or not scene_name:
        return "", ""
    
    scene_map = obs.obs_data_get_obj(settings, "scene_map")
    if not scene_map:
        return "", ""
    
    scene_item = obs.obs_data_get_obj(scene_map, scene_name)
    title, category = "", ""
    if scene_item:
        title = obs.obs_data_get_string(scene_item, "title")
        category = obs.obs_data_get_string(scene_item, "category")
        obs.obs_data_release(scene_item)
    obs.obs_data_release(scene_map)
    return title, category

def save_scene_data_to_obs(settings, scene_name, title, category):
    """Guarda el título y la categoría de una escena directamente en el sistema de settings de OBS."""
    if not settings or not scene_name:
        return
    
    scene_map = obs.obs_data_get_obj(settings, "scene_map")
    if not scene_map:
        scene_map = obs.obs_data_create()
        
    scene_item = obs.obs_data_create()
    obs.obs_data_set_string(scene_item, "title", title)
    obs.obs_data_set_string(scene_item, "category", category)
    
    obs.obs_data_set_obj(scene_map, scene_name, scene_item)
    obs.obs_data_set_obj(settings, "scene_map", scene_map)
    
    obs.obs_data_release(scene_item)
    obs.obs_data_release(scene_map)
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] ✓ Guardado nativo en OBS para escena '{scene_name}'")

def script_description():
    scene_name = get_active_scene_name()
    return (
        f"<b>{PLUGIN_NAME}</b><br>"
        "Edita y aplica la información del directo en Twitch para cada escena sin archivos externos.<br>"
        f"• <b>Escena activa actual:</b> <code>{scene_name if scene_name else 'Ninguna'}</code><br><br>"
        "Si no defines un título específico, se usará automáticamente el nombre de la escena más la coletilla global."
    )

def on_refresh_button_clicked(properties, property):
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Iniciando verificación manual del Token...")
    check_obs_twitch_integration()
    return True

def on_force_refresh_clicked(properties, property):
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Iniciando refresco forzado del token...")
    refresh_twitch_token()
    return True

def open_manual_token_generator(properties=None, property=None):
    client_id = "gp762nuuoqcoxypju8c569th9wz7q5"
    scopes = twitch_scopes if twitch_scopes and twitch_scopes.strip() else "channel:manage:broadcast user:read:chat"
    scopes_url = "%20".join([s.strip() for s in scopes.split() if s.strip()])
    auth_url = f"https://id.twitch.tv/oauth2/authorize?response_type=code&client_id={client_id}&redirect_uri=https://twitchtokengenerator.com&scope={scopes_url}"
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Abriendo la autorización de Twitch en el navegador.")
    webbrowser.open(auth_url)
    return True


def _credentials_from_settings(settings):
    return {key: obs.obs_data_get_string(settings, key) for key in CREDENTIAL_KEYS}


def _set_runtime_credentials(credentials):
    global twitch_client_id, twitch_client_secret, twitch_oauth_token, twitch_refresh_token
    twitch_client_id = credentials.get("twitch_client_id", "")
    twitch_client_secret = credentials.get("twitch_client_secret", "")
    twitch_oauth_token = credentials.get("twitch_oauth_token", "")
    twitch_refresh_token = credentials.get("twitch_refresh_token", "")
    if not isinstance(twitch_client_id, str):
        twitch_client_id = ""
    if not isinstance(twitch_client_secret, str):
        twitch_client_secret = ""
    if not isinstance(twitch_oauth_token, str):
        twitch_oauth_token = ""
    if not isinstance(twitch_refresh_token, str):
        twitch_refresh_token = ""


def _clear_credentials_from_settings(settings):
    for key in CREDENTIAL_KEYS:
        obs.obs_data_set_string(settings, key, "")


def on_save_credentials_clicked(properties, property):
    settings_credentials = _credentials_from_settings(_script_settings)
    credentials = {
        key: settings_credentials[key] or globals()[key]
        for key in CREDENTIAL_KEYS
    }
    if not any(credentials.values()):
        obs.script_log(obs.LOG_WARNING, f"[{PLUGIN_NAME}] No hay credenciales que guardar.")
        return True

    try:
        _save_credentials(credentials)
    except (OSError, ValueError) as error:
        obs.script_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] [ERROR] No se pudieron guardar las credenciales: "
            f"{_safe_error(error, settings_credentials)}"
        )
        obs.script_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] [AVISO] Si había credenciales en los ajustes de OBS, permanecerán allí hasta que se guarden correctamente."
        )
        return True

    _set_runtime_credentials(credentials)
    _clear_credentials_from_settings(_script_settings)
    obs.obs_properties_apply_settings(properties, _script_settings)
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Credenciales guardadas fuera de los ajustes de OBS.")
    return True


def on_clear_credentials_clicked(properties, property):
    try:
        _clear_credentials()
    except OSError as error:
        obs.script_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] [ERROR] No se pudieron borrar las credenciales: {_safe_error(error)}"
        )
        return True

    _set_runtime_credentials({})
    _clear_credentials_from_settings(_script_settings)
    obs.obs_properties_apply_settings(properties, _script_settings)
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Credenciales borradas.")
    return True


def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_bool(props, "enabled", "Plugin Activo")
    
    active_scene = get_active_scene_name()
    selected_scene = obs.obs_data_get_string(_script_settings, "selected_scene") if _script_settings else ""
    if not selected_scene:
        selected_scene = active_scene
    t_saved, c_saved = get_scene_data_from_obs(_script_settings, selected_scene)

    scene_props = obs.obs_properties_create()
    p_scene = obs.obs_properties_add_list(
        scene_props, "selected_scene", "Escena para editar",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )
    for scene_name in get_scene_names():
        obs.obs_property_list_add_string(p_scene, scene_name, scene_name)
    obs.obs_property_set_modified_callback(p_scene, on_scene_selection_changed)
    p_title = obs.obs_properties_add_text(
        scene_props, "current_scene_title", "Título de la escena", obs.OBS_TEXT_DEFAULT
    )

    # Selector de iconos
    def on_add_icon(props, prop):
        # En el callback, 'props' es la instancia de obs_properties_t,
        # pero para obtener/setear datos necesitamos el objeto settings actual.
        title = obs.obs_data_get_string(_script_settings, "current_scene_title")
        icon = obs.obs_data_get_string(_script_settings, "icon_selector")
        if icon:
            new_title = f"{title}{icon}"
            obs.obs_data_set_string(_script_settings, "current_scene_title", new_title)
            # Refrescar la interfaz para que el campo de texto se actualice
            obs.obs_properties_apply_settings(props, _script_settings)
        return True

    p_icon_list = obs.obs_properties_add_list(scene_props, "icon_selector", "Selector de iconos", obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
    for ic in ICONS:
        obs.obs_property_list_add_string(p_icon_list, ic, ic)
    obs.obs_properties_add_button(scene_props, "add_icon_btn", "➕ Añadir icono al título", on_add_icon)

    p_cat = obs.obs_properties_add_list(
        scene_props, "current_scene_category", "Categoría de la escena",
        obs.OBS_COMBO_TYPE_EDITABLE, obs.OBS_COMBO_FORMAT_STRING
    )
    
    def on_refresh_categories(props, prop):
        client_id = twitch_client_id
        token = twitch_oauth_token
        if not client_id or not token:
            obs.script_log(obs.LOG_WARNING, f"[{PLUGIN_NAME}] Faltan credenciales.")
            return True
            
        cat_prop = obs.obs_properties_get(scene_props, "current_scene_category")
        current_cat = obs.obs_data_get_string(_script_settings, "current_scene_category")
        
        if not current_cat or len(current_cat) < 3:
            obs.script_log(obs.LOG_WARNING, f"[{PLUGIN_NAME}] Escribe al menos 3 letras para buscar.")
            return True

        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Buscando categorías similares a: '{current_cat}'")
        
        headers = {"Client-ID": client_id, "Authorization": f"Bearer {token}"}
        url = f"https://api.twitch.tv/helix/search/categories?query={urllib.parse.quote(current_cat)}"
        req = urllib.request.Request(url, headers=headers)
        
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                games = res_data.get("data", [])
                obs.obs_property_list_clear(cat_prop)
                for game in games:
                    obs.obs_property_list_add_string(cat_prop, game.get("name"), game.get("name"))
                obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Se encontraron {len(games)} categorías.")
        except Exception as e:
            obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error buscando: {_safe_error(e)}")
        return True
    
    obs.obs_properties_add_button(scene_props, "refresh_cat_button", "🔍 Cargar/Refrescar categorías", on_refresh_categories)
    
    if c_saved:
        obs.obs_property_list_add_string(p_cat, c_saved, c_saved)

    # Inicializa los controles a partir de la escena seleccionada, no necesariamente la activa.
    if _script_settings:
        obs.obs_data_set_string(_script_settings, "selected_scene", selected_scene)
        obs.obs_data_set_string(_script_settings, "current_scene_title", t_saved)
        obs.obs_data_set_string(_script_settings, "current_scene_category", c_saved)

    obs.obs_properties_add_text(scene_props, "global_suffix", "Coletilla global", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_bool(scene_props, "block_if_streaming", "🚫 NO cambiar info automáticamente mientras emitas (Permite cambios manuales)")

    list_mode = obs.obs_properties_add_list(
        scene_props, "update_mode", "Modo de actualización",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_INT
    )
    obs.obs_property_list_add_int(list_mode, "Desactivado (Solo manual / Guardado)", 0)
    obs.obs_property_list_add_int(list_mode, "⚡ Cambiar Inmediatamente", 1)
    obs.obs_property_list_add_int(list_mode, "⏱ Cambiar con Retardo", 2)

    obs.obs_properties_add_int(scene_props, "delay_seconds", "Segundos de retardo", 5, 3600, 5)
    obs.obs_properties_add_button(scene_props, "apply_button", "⚡ Aplicar y Actualizar Info", on_apply_clicked)
    
    obs.obs_properties_add_group(props, "scene_group", "📺 Configuración de Escenas y Directo", obs.OBS_GROUP_NORMAL, scene_props)

    twitch_props = obs.obs_properties_create()
    obs.obs_properties_add_text(twitch_props, "twitch_client_id", "Twitch Client ID", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(twitch_props, "twitch_client_secret", "Twitch Client Secret", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(twitch_props, "twitch_oauth_token", "Twitch Access Token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(twitch_props, "twitch_refresh_token", "Twitch Refresh Token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(
        twitch_props,
        "credentials_help",
        "Introduce credenciales y pulsa Guardar para retirarlas de los ajustes de OBS. Vacío mantiene las credenciales guardadas.",
        obs.OBS_TEXT_INFO
    )
    obs.obs_properties_add_button(twitch_props, "save_credentials_button", "🔒 Guardar credenciales", on_save_credentials_clicked)
    obs.obs_properties_add_button(twitch_props, "clear_credentials_button", "🗑 Borrar credenciales guardadas", on_clear_credentials_clicked)
    p_scopes = obs.obs_properties_add_text(twitch_props, "twitch_scopes", "Scopes de Twitch", obs.OBS_TEXT_DEFAULT)
    obs.obs_property_set_modified_callback(p_scopes, lambda props, prop, settings: True)
    
    obs.obs_properties_add_button(twitch_props, "manual_generate_button", "🌐 Abrir TwitchTokenGenerator.com (Manual)", open_manual_token_generator)
    obs.obs_properties_add_button(twitch_props, "refresh_token_button", "🔍 Comprobar Token", on_refresh_button_clicked)
    obs.obs_properties_add_button(twitch_props, "force_refresh_button", "🔄 Forzar Refresco de Token", on_force_refresh_clicked)

    obs.obs_properties_add_group(props, "twitch_group", "🔐 Credenciales y Conexión Twitch", obs.OBS_GROUP_NORMAL, twitch_props)

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
    obs.obs_data_set_default_string(settings, "global_suffix", ". No commentary. Bring your own music.")
    obs.obs_data_set_default_bool(settings,   "block_if_streaming", False)
    obs.obs_data_set_default_int(settings,    "update_mode", 1)
    obs.obs_data_set_default_int(settings,    "delay_seconds", 60)
    obs.obs_data_set_default_string(settings, "twitch_client_id", "")
    obs.obs_data_set_default_string(settings, "twitch_client_secret", "")
    obs.obs_data_set_default_string(settings, "twitch_oauth_token", "")
    obs.obs_data_set_default_string(settings, "twitch_refresh_token", "")
    obs.obs_data_set_default_string(settings, "broadcaster_id", "")
    obs.obs_data_set_default_string(settings, "twitch_scopes", "channel:manage:broadcast user:read:chat")
    obs.obs_data_set_default_int(settings,    "last_refresh_timestamp", 0)

def run_proactive_refresh():
    obs.timer_remove(run_proactive_refresh)
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Ejecutando refresco proactivo en el hilo principal de OBS...")
    refresh_twitch_token()

def script_update(settings):
    global enabled, block_if_streaming, update_mode, delay_seconds, global_suffix
    global current_scene_title, current_scene_category
    global broadcaster_id, twitch_client_id, twitch_client_secret, twitch_oauth_token, twitch_refresh_token, twitch_scopes
    global _script_settings, is_first_load, _is_importing_settings
    
    _script_settings = settings
    enabled          = obs.obs_data_get_bool(settings, "enabled")
    active_scene = get_active_scene_name()

    global_suffix = obs.obs_data_get_string(settings, "global_suffix")
    new_title     = obs.obs_data_get_string(settings, "current_scene_title")
    new_category  = obs.obs_data_get_string(settings, "current_scene_category")
    
    target_scene = obs.obs_data_get_string(settings, "selected_scene") or active_scene
    if target_scene:
        if is_first_load:
            # On initial load the editable fields may still be empty defaults;
            # hydrate them from the per-scene map instead of erasing it.
            saved_title, saved_category = get_scene_data_from_obs(settings, target_scene)
            if saved_title or saved_category:
                new_title, new_category = saved_title, saved_category
                obs.obs_data_set_string(settings, "current_scene_title", new_title)
                obs.obs_data_set_string(settings, "current_scene_category", new_category)
        elif not _is_importing_settings:
            # Empty strings are intentional edits (clear the field), not a
            # signal to retain the previously saved value.
            save_scene_data_to_obs(settings, target_scene, new_title, new_category)
        current_scene_title    = new_title
        current_scene_category = new_category

    block_if_streaming  = obs.obs_data_get_bool(settings,   "block_if_streaming")
    update_mode        = obs.obs_data_get_int(settings,    "update_mode")
    delay_seconds      = obs.obs_data_get_int(settings,    "delay_seconds")
    broadcaster_id     = obs.obs_data_get_string(settings, "broadcaster_id")
    settings_credentials = _credentials_from_settings(settings)
    if is_first_load:
        stored_credentials = _load_credentials()
        if any(stored_credentials.values()):
            credentials = {
                key: stored_credentials.get(key) or settings_credentials[key]
                for key in CREDENTIAL_KEYS
            }
            _set_runtime_credentials(credentials)
            if credentials != stored_credentials:
                try:
                    _save_credentials(credentials)
                    _clear_credentials_from_settings(settings)
                except (OSError, ValueError) as error:
                    obs.script_log(
                        obs.LOG_INFO,
                        f"[{PLUGIN_NAME}] [ERROR] No se pudieron completar las credenciales guardadas: {_safe_error(error)}"
                    )
            else:
                _clear_credentials_from_settings(settings)
        elif any(settings_credentials.values()):
            _set_runtime_credentials(settings_credentials)
            try:
                _save_credentials(settings_credentials)
                _clear_credentials_from_settings(settings)
                obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Credenciales migradas al almacenamiento local.")
            except (OSError, ValueError) as error:
                obs.script_log(
                    obs.LOG_INFO,
                    f"[{PLUGIN_NAME}] [ERROR] No se pudieron migrar las credenciales: {_safe_error(error)}"
                )
                obs.script_log(
                    obs.LOG_INFO,
                    f"[{PLUGIN_NAME}] [AVISO] Las credenciales antiguas siguen en los ajustes de OBS."
                )
        else:
            _set_runtime_credentials({})
    else:
        current_credentials = {
            key: settings_credentials[key] or globals()[key]
            for key in CREDENTIAL_KEYS
        }
        _set_runtime_credentials(current_credentials)
    twitch_scopes      = obs.obs_data_get_string(settings, "twitch_scopes")

    if is_first_load:
        is_first_load = False
        execute_stream_info_update(update_category=False)
        last_refresh = obs.obs_data_get_int(settings, "last_refresh_timestamp")
        current_time = int(time.time())
        if twitch_refresh_token and (last_refresh == 0 or (current_time - last_refresh) > 14 * 86400):
            obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Programando refresco proactivo diferido...")
            obs.timer_add(run_proactive_refresh, 30000)

def execute_stream_info_update(update_category=True, allow_during_streaming=False):
    if not enabled: return
    obs.timer_remove(execute_stream_info_update)
    is_streaming = obs.obs_frontend_streaming_active()
    if is_streaming and block_if_streaming and not allow_during_streaming:
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Transmisión activa detectada. Cambio bloqueado.")
        return

    active_scene = get_active_scene_name()
    if not active_scene: return

    saved_title, saved_cat = get_scene_data_from_obs(_script_settings, active_scene)
    final_title = f"{saved_title if saved_title else clean_scene_name(active_scene)}{global_suffix}"
    final_category = saved_cat if saved_cat else clean_scene_name(active_scene)
    
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Aplicando info para escena '{active_scene}' -> Título: '{final_title}' | Categoría: '{final_category}'")

    category_to_update = final_category if update_category else ""
    worker = threading.Thread(
        target=update_stream_info_helix,
        args=(final_title, category_to_update),
        daemon=True
    )
    worker.start()

def get_game_id_by_name(game_name, headers):
    if not game_name or game_name.lower() == "juego": return None
    url = f"https://api.twitch.tv/helix/games?name={urllib.parse.quote(game_name)}"
    req = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            games = res_data.get("data", [])
            if games: return games[0].get("id")
    except Exception as e:
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error buscando categoría: {_safe_error(e)}")
    return None

def get_broadcaster_id_automatically(headers):
    url = "https://api.twitch.tv/helix/users"
    req = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            users = res_data.get("data", [])
            if users: return users[0].get("id")
    except Exception as e:
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error obteniendo Broadcaster ID: {_safe_error(e)}")
    return None

def refresh_twitch_token():
    global twitch_oauth_token, twitch_refresh_token, twitch_client_id, twitch_client_secret
    
    if not twitch_refresh_token: return False

    if twitch_client_secret and twitch_client_id:
        url = "https://id.twitch.tv/oauth2/token"
        params = {"grant_type": "refresh_token", "refresh_token": twitch_refresh_token.strip(), "client_id": twitch_client_id.strip(), "client_secret": twitch_client_secret.strip()}
        data = urllib.parse.urlencode(params).encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                if res_data.get("access_token"):
                    twitch_oauth_token = res_data["access_token"]
                    if res_data.get("refresh_token"): twitch_refresh_token = res_data["refresh_token"]
                    if _script_settings:
                        obs.obs_data_set_int(_script_settings, "last_refresh_timestamp", int(time.time()))
                    _persist_runtime_credentials()
                    return True
        except Exception as e:
            obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error API oficial: {_safe_error(e)}")
    else:
        url = f"https://twitchtokengenerator.com/api/refresh/{twitch_refresh_token.strip()}"
        req = urllib.request.Request(url, method='GET')
        req.add_header("User-Agent", "Mozilla/5.0")
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                if res_data.get("success"):
                    twitch_oauth_token = res_data.get("token")
                    twitch_refresh_token = res_data.get("refresh")
                    if _script_settings:
                        obs.obs_data_set_int(_script_settings, "last_refresh_timestamp", int(time.time()))
                    _persist_runtime_credentials()
                    return True
        except Exception as e:
            obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error API gen: {_safe_error(e)}")
    return False

def update_stream_info_helix(title, category):
    global twitch_oauth_token, twitch_refresh_token
    if not twitch_client_id or not twitch_oauth_token: return

    headers = {"Client-ID": twitch_client_id, "Authorization": f"Bearer {twitch_oauth_token.replace('oauth:', '').strip()}", "Content-Type": "application/json"}
    current_broadcaster_id = broadcaster_id or get_broadcaster_id_automatically(headers)
    if not current_broadcaster_id: return

    game_id = get_game_id_by_name(category, headers) if category else None
    url = f"https://api.twitch.tv/helix/channels?broadcaster_id={current_broadcaster_id}"
    body = {}
    if title: body["title"] = title
    if game_id: body["game_id"] = game_id

    if not body: return

    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='PATCH')

    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
            if response.status in (204, 200):
                obs.script_log(obs.LOG_INFO, f"✓ [{PLUGIN_NAME}] ¡Información actualizada!")
                return
    except urllib.error.HTTPError as he:
        if he.code == 401 and refresh_twitch_token():
            headers["Authorization"] = f"Bearer {twitch_oauth_token.strip()}"
            req_retry = urllib.request.Request(url, data=data, headers=headers, method='PATCH')
            try:
                with urllib.request.urlopen(req_retry, timeout=HTTP_TIMEOUT_SECONDS) as res_retry:
                    if res_retry.status in (204, 200):
                        obs.script_log(obs.LOG_INFO, f"✓ [{PLUGIN_NAME}] ¡Información actualizada tras refrescar el token!")
                        return
            except urllib.error.HTTPError as retry_error:
                obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error en el reintento: {_safe_error(retry_error)}")
            except urllib.error.URLError as retry_error:
                obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Fallo de red en el reintento: {_safe_error(retry_error)}")
            except TimeoutError as retry_error:
                obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Timeout en el reintento: {_safe_error(retry_error)}")
            except OSError as retry_error:
                obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error de socket en el reintento: {_safe_error(retry_error)}")
        elif he.code == 401:
            obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Token rechazado y no se pudo refrescar.")
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error: {_safe_error(he.reason)}")
    except urllib.error.URLError as error:
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Fallo de red actualizando la información: {_safe_error(error)}")
    except TimeoutError as error:
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Timeout actualizando la información: {_safe_error(error)}")
    except OSError as error:
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error de socket actualizando la información: {_safe_error(error)}")

def on_apply_clicked(properties, property):
    execute_stream_info_update(allow_during_streaming=True)
    return True

def on_export_settings(props, prop):
    settings_dir = obs.obs_data_get_string(_script_settings, "settings_path") or os.path.join(os.path.expanduser("~"), "Desktop")
    export_path = os.path.join(settings_dir, SETTINGS_FILENAME)
    try:
        scene_map = obs.obs_data_get_obj(_script_settings, "scene_map")
        scene_map_json = obs.obs_data_get_json(scene_map) if scene_map else "{}"
        if scene_map: obs.obs_data_release(scene_map)
        config_data = {
            "enabled": enabled, "block_if_streaming": block_if_streaming, "update_mode": update_mode,
            "delay_seconds": delay_seconds, "global_suffix": global_suffix, "twitch_scopes": twitch_scopes,
            "broadcaster_id": broadcaster_id, "scene_map": json.loads(scene_map_json)
        }
        with open(export_path, "w", encoding="utf-8") as f: json.dump(config_data, f, indent=4, ensure_ascii=False)
        obs.script_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] ✓ Ajustes exportados sin secretos a: {export_path}"
        )
    except Exception as e: obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error exportar: {_safe_error(e)}")
    return True

def on_import_settings(props, prop):
    global _is_importing_settings
    settings_dir = obs.obs_data_get_string(_script_settings, "settings_path") or os.path.join(os.path.expanduser("~"), "Desktop")
    import_path = os.path.join(settings_dir, SETTINGS_FILENAME)
    if not os.path.exists(import_path):
        obs.script_log(obs.LOG_WARNING, f"[{PLUGIN_NAME}] No se encontró ajustes en: {import_path}")
        return True
    try:
        with open(import_path, "r", encoding="utf-8") as f: config_data = json.load(f)
        target_settings = _script_settings or obs.obs_data_create()
        obs.obs_data_set_bool(target_settings, "enabled", config_data.get("enabled", True))
        obs.obs_data_set_bool(target_settings, "block_if_streaming", config_data.get("block_if_streaming", False))
        obs.obs_data_set_int(target_settings, "update_mode", config_data.get("update_mode", 1))
        obs.obs_data_set_int(target_settings, "delay_seconds", config_data.get("delay_seconds", 60))
        obs.obs_data_set_string(target_settings, "global_suffix", config_data.get("global_suffix", ". No commentary. Bring your own music."))
        imported_credentials = {}
        for key in CREDENTIAL_KEYS:
            value = config_data.get(key)
            if isinstance(value, str) and value:
                imported_credentials[key] = value
        if imported_credentials:
            credentials = {
                key: imported_credentials.get(key, globals()[key])
                for key in CREDENTIAL_KEYS
            }
            try:
                _save_credentials(credentials)
                _set_runtime_credentials(credentials)
                _clear_credentials_from_settings(target_settings)
            except (OSError, ValueError) as error:
                for key, value in credentials.items():
                    obs.obs_data_set_string(target_settings, key, value)
                obs.script_log(
                    obs.LOG_INFO,
                    f"[{PLUGIN_NAME}] [ERROR] No se pudieron guardar las credenciales importadas: "
                    f"{_safe_error(error, credentials)}"
                )
                obs.script_log(
                    obs.LOG_INFO,
                    f"[{PLUGIN_NAME}] [AVISO] Las credenciales antiguas permanecen en los ajustes de OBS."
                )
        else:
            _clear_credentials_from_settings(target_settings)
        obs.obs_data_set_string(target_settings, "twitch_scopes", config_data.get("twitch_scopes", "channel:manage:broadcast user:read:chat"))
        obs.obs_data_set_string(target_settings, "broadcaster_id", config_data.get("broadcaster_id", ""))
        if "scene_map" in config_data:
            scene_map = obs.obs_data_create_from_json(json.dumps(config_data["scene_map"]))
            obs.obs_data_set_obj(target_settings, "scene_map", scene_map)
            obs.obs_data_release(scene_map)
        scene_name = obs.obs_data_get_string(target_settings, "selected_scene") or get_active_scene_name()
        imported_title, imported_category = get_scene_data_from_obs(target_settings, scene_name)
        obs.obs_data_set_string(target_settings, "selected_scene", scene_name)
        obs.obs_data_set_string(target_settings, "current_scene_title", imported_title)
        obs.obs_data_set_string(target_settings, "current_scene_category", imported_category)
        _is_importing_settings = True
        try:
            script_update(target_settings)
        finally:
            _is_importing_settings = False
        if props: obs.obs_properties_apply_settings(props, target_settings)
        if not _script_settings: obs.obs_data_release(target_settings)
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] ✓ Ajustes importados correctamente.")
    except Exception as e: obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error importar: {_safe_error(e)}")
    return True

def handle_scene_changed():
    active_scene = get_active_scene_name()
    t_saved, c_saved = get_scene_data_from_obs(_script_settings, active_scene)
    if _script_settings:
        obs.obs_data_set_string(_script_settings, "selected_scene", active_scene)
        obs.obs_data_set_string(_script_settings, "current_scene_title", t_saved)
        obs.obs_data_set_string(_script_settings, "current_scene_category", c_saved)
    if update_mode == 1:
        obs.timer_remove(execute_stream_info_update)
        execute_stream_info_update()
    elif update_mode == 2:
        obs.timer_remove(execute_stream_info_update)
        obs.timer_add(execute_stream_info_update, delay_seconds * 1000)

def on_event(event):
    if not enabled: return
    if event == obs.OBS_FRONTEND_EVENT_SCENE_CHANGED: handle_scene_changed()

def check_obs_twitch_integration():
    if not twitch_oauth_token: return
    headers = {"Client-ID": twitch_client_id, "Authorization": f"Bearer {twitch_oauth_token.replace('oauth:', '').strip()}", "Content-Type": "application/json"}
    url = "https://api.twitch.tv/helix/users"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers),
            timeout=HTTP_TIMEOUT_SECONDS
        ) as response:
            obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [Check] ✓ Token VÁLIDO.")
    except urllib.error.HTTPError as he:
        if he.code == 401 and refresh_twitch_token(): return
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] [Check] Error token: {_safe_error(he)}")
    except urllib.error.URLError as error:
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] [Check] Fallo de red comprobando el token: {_safe_error(error)}")
    except TimeoutError as error:
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] [Check] Timeout comprobando el token: {_safe_error(error)}")
    except OSError as error:
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] [Check] Error de socket comprobando el token: {_safe_error(error)}")

def script_load(settings):
    global _script_settings
    _script_settings = settings
    obs.obs_frontend_add_event_callback(on_event)
    obs.script_log(obs.LOG_INFO, f"{PLUGIN_NAME} cargado.")

def script_unload():
    obs.timer_remove(execute_stream_info_update)
    obs.timer_remove(run_proactive_refresh)
    obs.obs_frontend_remove_event_callback(on_event)
    obs.script_log(obs.LOG_INFO, f"{PLUGIN_NAME} descargado.")
