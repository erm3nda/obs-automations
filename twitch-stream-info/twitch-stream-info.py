import obspython as obs
import urllib.request
import urllib.parse
import urllib.error
import json
import threading
import re
import webbrowser
import os
import subprocess
import sys
import time

PLUGIN_NAME = "Twitch-Stream-Info"
SETTINGS_FILENAME = "twitch-stream-info.json"
ICONS = ["🎮", "🚗", "🏍️", "⚽", "✈️", "🥊", "🔫"]

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
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Abriendo generador de token en el navegador: {auth_url}")
    webbrowser.open(auth_url)
    return True

def run_smart_auth_wrapper(properties, property):
    script_path = os.path.join(os.path.dirname(__file__), "twitch_login.py")
    scopes = obs.obs_data_get_string(_script_settings, "twitch_scopes") or "channel:manage:broadcast user:read:chat"
    subprocess.Popen([sys.executable, script_path, "--smart", scopes])
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Iniciando proceso de autenticación inteligente de Twitch...")
    return True

def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_bool(props, "enabled", "✅ Plugin Activo")
    
    active_scene = get_active_scene_name()
    t_saved, c_saved = get_scene_data_from_obs(_script_settings, active_scene)

    scene_props = obs.obs_properties_create()
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
        client_id = obs.obs_data_get_string(_script_settings, "twitch_client_id")
        token = obs.obs_data_get_string(_script_settings, "twitch_oauth_token")
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
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                games = res_data.get("data", [])
                obs.obs_property_list_clear(cat_prop)
                for game in games:
                    obs.obs_property_list_add_string(cat_prop, game.get("name"), game.get("name"))
                obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Se encontraron {len(games)} categorías.")
        except Exception as e:
            obs.script_log(obs.LOG_ERROR, f"[{PLUGIN_NAME}] Error buscando: {e}")
        return True
    
    obs.obs_properties_add_button(scene_props, "refresh_cat_button", "🔍 Cargar/Refrescar categorías", on_refresh_categories)
    
    if c_saved:
        obs.obs_property_list_add_string(p_cat, c_saved, c_saved)

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
    obs.obs_properties_add_text(twitch_props, "twitch_client_id", "Twitch Client ID", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(twitch_props, "twitch_oauth_token", "Twitch Access Token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(twitch_props, "twitch_refresh_token", "Twitch Refresh Token", obs.OBS_TEXT_PASSWORD)
    p_scopes = obs.obs_properties_add_text(twitch_props, "twitch_scopes", "Scopes de Twitch", obs.OBS_TEXT_DEFAULT)
    obs.obs_property_set_modified_callback(p_scopes, lambda props, prop, settings: True)
    
    obs.obs_properties_add_button(twitch_props, "smart_auth_button", "⚡ Autenticación Automática (Playwright)", run_smart_auth_wrapper)
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
    global _script_settings, is_first_load
    
    _script_settings = settings
    enabled          = obs.obs_data_get_bool(settings, "enabled")
    active_scene = get_active_scene_name()

    global_suffix = obs.obs_data_get_string(settings, "global_suffix")
    new_title     = obs.obs_data_get_string(settings, "current_scene_title")
    new_category  = obs.obs_data_get_string(settings, "current_scene_category")
    
    if active_scene and (new_title or new_category):
        current_scene_title    = new_title
        current_scene_category = new_category
        save_scene_data_to_obs(settings, active_scene, new_title, new_category)

    block_if_streaming  = obs.obs_data_get_bool(settings,   "block_if_streaming")
    update_mode        = obs.obs_data_get_int(settings,    "update_mode")
    delay_seconds      = obs.obs_data_get_int(settings,    "delay_seconds")
    broadcaster_id     = obs.obs_data_get_string(settings, "broadcaster_id")
    twitch_client_id   = obs.obs_data_get_string(settings, "twitch_client_id")
    twitch_client_secret = obs.obs_data_get_string(settings, "twitch_client_secret")
    twitch_oauth_token = obs.obs_data_get_string(settings, "twitch_oauth_token")
    twitch_refresh_token = obs.obs_data_get_string(settings, "twitch_refresh_token")
    twitch_scopes      = obs.obs_data_get_string(settings, "twitch_scopes")

    if is_first_load:
        is_first_load = False
        execute_stream_info_update(update_category=False)
        last_refresh = obs.obs_data_get_int(settings, "last_refresh_timestamp")
        current_time = int(time.time())
        if twitch_refresh_token and (last_refresh == 0 or (current_time - last_refresh) > 14 * 86400):
            obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Programando refresco proactivo diferido...")
            obs.timer_add(run_proactive_refresh, 30000)

def execute_stream_info_update(update_category=True):
    if not enabled: return
    obs.timer_remove(execute_stream_info_update)
    is_streaming = obs.obs_frontend_streaming_active()
    if is_streaming and block_if_streaming:
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Transmisión activa detectada. Cambio bloqueado.")
        return

    active_scene = get_active_scene_name()
    if not active_scene: return

    saved_title, saved_cat = get_scene_data_from_obs(_script_settings, active_scene)
    final_title = f"{saved_title if saved_title else clean_scene_name(active_scene)}{global_suffix}"
    final_category = saved_cat if saved_cat else clean_scene_name(active_scene)
    
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Aplicando info para escena '{active_scene}' -> Título: '{final_title}' | Categoría: '{final_category}'")
    
    category_to_update = final_category if update_category else ""
    t = threading.Thread(target=update_stream_info_helix, args=(final_title, category_to_update))
    t.daemon = True
    t.start()

def get_game_id_by_name(game_name, headers):
    if not game_name or game_name.lower() == "juego": return None
    url = f"https://api.twitch.tv/helix/games?name={urllib.parse.quote(game_name)}"
    req = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            games = res_data.get("data", [])
            if games: return games[0].get("id")
    except Exception as e:
        obs.script_log(obs.LOG_ERROR, f"[{PLUGIN_NAME}] Error buscando categoría: {e}")
    return None

def get_broadcaster_id_automatically(headers):
    url = "https://api.twitch.tv/helix/users"
    req = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            users = res_data.get("data", [])
            if users: return users[0].get("id")
    except Exception as e:
        obs.script_log(obs.LOG_ERROR, f"[{PLUGIN_NAME}] Error obteniendo Broadcaster ID: {e}")
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
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                if res_data.get("access_token"):
                    twitch_oauth_token = res_data["access_token"]
                    if res_data.get("refresh_token"): twitch_refresh_token = res_data["refresh_token"]
                    if _script_settings:
                        obs.obs_data_set_string(_script_settings, "twitch_oauth_token", twitch_oauth_token)
                        obs.obs_data_set_string(_script_settings, "twitch_refresh_token", twitch_refresh_token)
                        obs.obs_data_set_int(_script_settings, "last_refresh_timestamp", int(time.time()))
                    return True
        except Exception as e:
            obs.script_log(obs.LOG_ERROR, f"[{PLUGIN_NAME}] Error API oficial: {e}")
    else:
        url = f"https://twitchtokengenerator.com/api/refresh/{twitch_refresh_token.strip()}"
        req = urllib.request.Request(url, method='GET')
        req.add_header("User-Agent", "Mozilla/5.0")
        try:
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                if res_data.get("success"):
                    twitch_oauth_token = res_data.get("token")
                    twitch_refresh_token = res_data.get("refresh")
                    if _script_settings:
                        obs.obs_data_set_string(_script_settings, "twitch_oauth_token", twitch_oauth_token)
                        obs.obs_data_set_string(_script_settings, "twitch_refresh_token", twitch_refresh_token)
                        obs.obs_data_set_int(_script_settings, "last_refresh_timestamp", int(time.time()))
                    return True
        except Exception as e:
            obs.script_log(obs.LOG_ERROR, f"[{PLUGIN_NAME}] Error API gen: {e}")
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
        with urllib.request.urlopen(req) as response:
            if response.status in (204, 200):
                obs.script_log(obs.LOG_INFO, f"✓ [{PLUGIN_NAME}] ¡Información actualizada!")
                return
    except urllib.error.HTTPError as he:
        if he.code == 401 and refresh_twitch_token():
            headers["Authorization"] = f"Bearer {twitch_oauth_token.strip()}"
            req_retry = urllib.request.Request(url, data=data, headers=headers, method='PATCH')
            try:
                with urllib.request.urlopen(req_retry) as res_retry:
                    if res_retry.status in (204, 200): return
            except: pass
        obs.script_log(obs.LOG_ERROR, f"[{PLUGIN_NAME}] ❌ Error: {he.reason}")

def on_apply_clicked(properties, property): execute_stream_info_update()

def on_export_settings(props, prop):
    settings_dir = obs.obs_data_get_string(_script_settings, "settings_path") or os.path.join(os.path.expanduser("~"), "Desktop")
    export_path = os.path.join(settings_dir, SETTINGS_FILENAME)
    try:
        scene_map = obs.obs_data_get_obj(_script_settings, "scene_map")
        scene_map_json = obs.obs_data_get_json(scene_map) if scene_map else "{}"
        if scene_map: obs.obs_data_release(scene_map)
        config_data = {
            "enabled": enabled, "block_if_streaming": block_if_streaming, "update_mode": update_mode,
            "delay_seconds": delay_seconds, "global_suffix": global_suffix, "twitch_client_id": twitch_client_id,
            "twitch_client_secret": twitch_client_secret, "twitch_oauth_token": twitch_oauth_token,
            "twitch_refresh_token": twitch_refresh_token, "twitch_scopes": twitch_scopes,
            "broadcaster_id": broadcaster_id, "scene_map": json.loads(scene_map_json)
        }
        with open(export_path, "w", encoding="utf-8") as f: json.dump(config_data, f, indent=4, ensure_ascii=False)
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] ✓ Ajustes exportados a: {export_path}")
    except Exception as e: obs.script_log(obs.LOG_ERROR, f"[{PLUGIN_NAME}] Error exportar: {e}")
    return True

def on_import_settings(props, prop):
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
        obs.obs_data_set_string(target_settings, "twitch_client_id", config_data.get("twitch_client_id", ""))
        obs.obs_data_set_string(target_settings, "twitch_client_secret", config_data.get("twitch_client_secret", ""))
        obs.obs_data_set_string(target_settings, "twitch_oauth_token", config_data.get("twitch_oauth_token", ""))
        obs.obs_data_set_string(target_settings, "twitch_refresh_token", config_data.get("twitch_refresh_token", ""))
        obs.obs_data_set_string(target_settings, "twitch_scopes", config_data.get("twitch_scopes", "channel:manage:broadcast user:read:chat"))
        obs.obs_data_set_string(target_settings, "broadcaster_id", config_data.get("broadcaster_id", ""))
        if "scene_map" in config_data:
            scene_map = obs.obs_data_create_from_json(json.dumps(config_data["scene_map"]))
            obs.obs_data_set_obj(target_settings, "scene_map", scene_map)
            obs.obs_data_release(scene_map)
        script_update(target_settings)
        if props: obs.obs_properties_apply_settings(props, target_settings)
        if not _script_settings: obs.obs_data_release(target_settings)
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] ✓ Ajustes importados correctamente.")
    except Exception as e: obs.script_log(obs.LOG_ERROR, f"[{PLUGIN_NAME}] Error importar: {e}")
    return True

def handle_scene_changed():
    active_scene = get_active_scene_name()
    t_saved, c_saved = get_scene_data_from_obs(_script_settings, active_scene)
    if _script_settings:
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
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as response:
            obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [Check] ✓ Token VÁLIDO.")
    except urllib.error.HTTPError as he:
        if he.code == 401 and refresh_twitch_token(): return
        obs.script_log(obs.LOG_ERROR, f"[{PLUGIN_NAME}] [Check] ❌ Error token.")

def script_load(settings):
    global _script_settings
    _script_settings = settings
    obs.obs_frontend_add_event_callback(on_event)
    obs.script_log(obs.LOG_INFO, f"{PLUGIN_NAME} cargado.")

def script_unload():
    obs.timer_remove(execute_stream_info_update)
    obs.timer_remove(run_proactive_refresh)
    obs.script_log(obs.LOG_INFO, f"{PLUGIN_NAME} descargado.")
