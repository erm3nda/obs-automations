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
    """Extrae el nombre limpio del juego a partir de la escena (ej: 'Raceroom escene' -> 'Raceroom')."""
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
    obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] ✓ Guardado nativo en OBS para escena '{scene_name}'")

def script_description():
    scene_name = get_active_scene_name()
    return (
        "<b>Set-Stream-Info</b><br>"
        "Edita y aplica la información del directo para cada escena sin archivos externos.<br>"
        f"• <b>Escena activa actual:</b> <code>{scene_name if scene_name else 'Ninguna'}</code><br><br>"
        "Si no defines un título específico, se usará automáticamente el nombre de la escena más la coletilla global."
    )

def on_refresh_button_clicked(properties, property):
    """Callback para el botón manual de comprobar y refrescar token."""
    obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] Iniciando verificación manual del Token...")
    check_obs_twitch_integration()
    return True

def on_force_refresh_clicked(properties, property):
    """Fuerza la renovación inmediata del token."""
    obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] Iniciando refresco forzado del token...")
    refresh_twitch_token()
    return True

def open_manual_token_generator(properties=None, property=None):
    """Abre la URL de autorización oficial de TwitchTokenGenerator en el navegador del sistema."""
    client_id = "gp762nuuoqcoxypju8c569th9wz7q5"
    scopes = twitch_scopes if twitch_scopes and twitch_scopes.strip() else "channel:manage:broadcast"
    scopes_url = "%20".join([s.strip() for s in scopes.split() if s.strip()])
    auth_url = f"https://id.twitch.tv/oauth2/authorize?response_type=code&client_id={client_id}&redirect_uri=https://twitchtokengenerator.com&scope={scopes_url}"
    obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] Abriendo generador de token en el navegador: {auth_url}")
    webbrowser.open(auth_url)
    return True

def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_bool(props, "enabled", "✅ Plugin Activo")
    
    active_scene = get_active_scene_name()
    t_saved, c_saved = get_scene_data_from_obs(_script_settings, active_scene)

    # Grupo 1: Información de la Escena Actual y Configuración de Directo
    scene_props = obs.obs_properties_create()
    p_title = obs.obs_properties_add_text(
        scene_props, "current_scene_title",
        "Título de la escena",
        obs.OBS_TEXT_DEFAULT
    )
    p_cat = obs.obs_properties_add_list(
        scene_props, "current_scene_category",
        "Categoría de la escena",
        obs.OBS_COMBO_TYPE_EDITABLE, obs.OBS_COMBO_FORMAT_STRING
    )
    if c_saved:
        obs.obs_property_list_add_string(p_cat, c_saved, c_saved)

    obs.obs_properties_add_text(
        scene_props, "global_suffix",
        "Coletilla global",
        obs.OBS_TEXT_DEFAULT
    )
    obs.obs_properties_add_bool(scene_props, "block_if_streaming", "🚫 NO cambiar info mientras esté emitiendo (Solo Offline)")

    list_mode = obs.obs_properties_add_list(
        scene_props, "update_mode", "Modo de actualización",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_INT
    )
    obs.obs_property_list_add_int(list_mode, "Desactivado (Solo manual / Guardado)", 0)
    obs.obs_property_list_add_int(list_mode, "⚡ Cambiar Inmediatamente", 1)
    obs.obs_property_list_add_int(list_mode, "⏱ Cambiar con Retardo", 2)

    obs.obs_properties_add_int(scene_props, "delay_seconds", "Segundos de retardo", 5, 3600, 5)
    obs.obs_properties_add_button(scene_props, "apply_button", "Aplicar y guardar escena activa", on_apply_clicked)
    
    obs.obs_properties_add_group(
        props, "scene_group", "📺 Configuración de Escenas y Directo",
        obs.OBS_GROUP_NORMAL, scene_props
    )

    # Grupo 2: Credenciales y Conexión con Twitch
    twitch_props = obs.obs_properties_create()
    obs.obs_properties_add_text(twitch_props, "twitch_client_id", "Twitch Client ID", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(twitch_props, "twitch_oauth_token", "Twitch Access Token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(twitch_props, "twitch_refresh_token", "Twitch Refresh Token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(twitch_props, "twitch_scopes", "Scopes de Twitch", obs.OBS_TEXT_DEFAULT)
    
    obs.obs_properties_add_button(twitch_props, "refresh_token_button", "🔍 Comprobar Token", on_refresh_button_clicked)
    obs.obs_properties_add_button(twitch_props, "force_refresh_button", "🔄 Forzar Refresco de Token", on_force_refresh_clicked)
    obs.obs_properties_add_button(twitch_props, "manual_generate_button", "🔑 Abrir Generador en Navegador", open_manual_token_generator)

    obs.obs_properties_add_group(
        props, "twitch_group", "🔐 Credenciales y Conexión Twitch",
        obs.OBS_GROUP_NORMAL, twitch_props
    )

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
    obs.obs_data_set_default_string(settings, "twitch_scopes", "channel:manage:broadcast")
    obs.obs_data_set_default_int(settings,    "last_refresh_timestamp", 0)

def run_proactive_refresh():
    obs.timer_remove(run_proactive_refresh)
    obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] Ejecutando refresco proactivo en el hilo principal de OBS...")
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
        
        # Refresco proactivo (cada 14 días)
        last_refresh = obs.obs_data_get_int(settings, "last_refresh_timestamp")
        current_time = int(time.time())
        if twitch_refresh_token and (last_refresh == 0 or (current_time - last_refresh) > 14 * 86400):
            obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] El token no se ha refrescado en 14 días o es la primera carga. Programando refresco proactivo...")
            obs.timer_add(run_proactive_refresh, 1000)

def get_emoji_for_game(game_name):
    """Devuelve un emoji adecuado según el nombre del juego."""
    if not game_name:
        return "🎮"
    name_lower = game_name.lower()
    
    # Automovilismo / Carreras
    if any(x in name_lower for x in ["race", "rally", "assetto", "corsa", "f1", "kart", "car", "speed", "simbin", "automobilista", "iracing", "rfactor", "project cars", "dirt"]):
        return "🚗"
    # Motociclismo
    if any(x in name_lower for x in ["moto", "gp", "ride", "bike"]):
        return "🏍️"
    # Fútbol / Deportes
    if any(x in name_lower for x in ["fifa", "fc", "football", "soccer", "pes", "nba", "tennis"]):
        return "⚽"
    # Vuelo / Simulación aérea
    if any(x in name_lower for x in ["flight", "plane", "aero", "sky"]):
        return "✈️"
    # Lucha
    if any(x in name_lower for x in ["street fighter", "tekken", "mortal kombat", "ufc", "fight"]):
        return "🥊"
    # Disparos / Acción
    if any(x in name_lower for x in ["call of duty", "cod", "battlefield", "csgo", "counter", "valorant", "halo", "doom"]):
        return "🔫"
    
    return "🎮"

def strip_leading_emojis(s):
    if not s:
        return ""
    while s:
        first_char = ord(s[0])
        if (0x2000 <= first_char <= 0x32FF) or (first_char >= 0x1F000) or s[0].isspace():
            s = s[1:]
        else:
            break
    return s

def execute_stream_info_update(update_category=True):
    """Ejecuta la actualización de la información según la escena activa."""
    if not enabled:
        return
    obs.timer_remove(execute_stream_info_update)
    
    is_streaming = obs.obs_frontend_streaming_active()
    if is_streaming and block_if_streaming:
        obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] Transmisión activa detectada. Cambio bloqueado por configuración.")
        return

    active_scene = get_active_scene_name()
    if not active_scene:
        return

    saved_title, saved_cat = get_scene_data_from_obs(_script_settings, active_scene)
    
    # 1. Resolver título (si está en blanco, usa el nombre limpio de escena)
    base_title = saved_title if saved_title else clean_scene_name(active_scene)
    
    # Eliminar cualquier emoji previo al inicio para evitar duplicación recursiva
    clean_base = strip_leading_emojis(base_title)
    
    # Obtener el emoji representativo del juego
    emoji = get_emoji_for_game(clean_base)
    
    # 2. Agregar coletilla global si existe (sin la barra vertical)
    if global_suffix:
        final_title = f"{emoji} {clean_base}{global_suffix}"
    else:
        final_title = f"{emoji} {clean_base}"

    # 3. Resolver categoría (si está en blanco, usa el nombre limpio de escena)
    final_category = saved_cat if saved_cat else clean_scene_name(active_scene)

    obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] Aplicando info para escena '{active_scene}' -> Título final: '{final_title}' | Categoría: '{final_category}'")
    
    category_to_update = final_category if update_category else ""
    t = threading.Thread(target=update_stream_info_helix, args=(final_title, category_to_update))
    t.daemon = True
    t.start()

def get_game_id_by_name(game_name, headers):
    """Busca el ID numérico de una categoría/juego en Twitch por su nombre."""
    if not game_name or game_name.lower() == "juego":
        return None
        
    encoded_name = urllib.parse.quote(game_name)
    url = f"https://api.twitch.tv/helix/games?name={encoded_name}"
    req = urllib.request.Request(url, headers=headers, method='GET')
    
    obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] Buscando ID de Twitch para la categoría: '{game_name}'...")
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            games = res_data.get("data", [])
            if games:
                game_id = games[0].get("id")
                obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] ✓ Categoría encontrada: '{games[0].get('name')}' con ID: {game_id}")
                return game_id
            else:
                obs.script_log(obs.LOG_WARNING, f"[Set-Stream-Info] ⚠ No se encontró la categoría '{game_name}' en Twitch.")
                return None
    except urllib.error.HTTPError as he:
        try:
            err_body = he.read().decode('utf-8')
            obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Error HTTP {he.code} buscando categoría '{game_name}': {he.reason}. Detalles de Twitch: {err_body}")
        except Exception:
            obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Error HTTP {he.code} buscando categoría: {he.reason}")
    except Exception as e:
        obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Error inesperado consultando categoría '{game_name}': {e}")
    return None
def get_broadcaster_id_automatically(headers):
    """Obtiene el ID de usuario de Twitch a partir del Token OAuth si no se especificó uno."""
    url = "https://api.twitch.tv/helix/users"
    req = urllib.request.Request(url, headers=headers, method='GET')
    obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] Intentando obtener Broadcaster ID automáticamente a partir del Token OAuth...")
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            users = res_data.get("data", [])
            if users:
                b_id = users[0].get("id")
                obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] ✓ Broadcaster ID obtenido con éxito: {b_id} (Usuario: {users[0].get('login')})")
                return b_id
            else:
                obs.script_log(obs.LOG_WARNING, "[Set-Stream-Info] El token no devolvió información de ningún usuario.")
    except urllib.error.HTTPError as he:
        try:
            err_body = he.read().decode('utf-8')
            obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Error HTTP {he.code} obteniendo Broadcaster ID: {he.reason}. Respuesta de Twitch: {err_body}")
        except Exception:
            obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Error HTTP {he.code} obteniendo Broadcaster ID: {he.reason}")
    except Exception as e:
        obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Error inesperado obteniendo Broadcaster ID: {e}")
    return None

def refresh_twitch_token():
    """Refresca el token OAuth utilizando el Refresh Token o la API de TwitchTokenGenerator y guarda los nuevos valores."""
    global twitch_oauth_token, twitch_refresh_token, twitch_client_id, twitch_client_secret
    
    if not twitch_refresh_token or not twitch_refresh_token.strip():
        obs.script_log(obs.LOG_WARNING, "[Set-Stream-Info] No se puede refrescar el token: falta el Refresh Token.")
        return False

    # CASO 1: Con Client Secret (API oficial de Twitch)
    if twitch_client_secret and twitch_client_secret.strip():
        if not twitch_client_id or not twitch_client_id.strip():
            obs.script_log(obs.LOG_WARNING, "[Set-Stream-Info] Falta el Twitch Client ID para el refresco oficial.")
            return False
            
        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "grant_type": "refresh_token",
            "refresh_token": twitch_refresh_token.strip(),
            "client_id": twitch_client_id.strip(),
            "client_secret": twitch_client_secret.strip()
        }
        data = urllib.parse.urlencode(params).encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        
        obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] Intentando refrescar Token usando la API oficial de Twitch...")
        try:
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                new_access = res_data.get("access_token")
                new_refresh = res_data.get("refresh_token")
                
                if new_access:
                    twitch_oauth_token = new_access
                    if new_refresh:
                        twitch_refresh_token = new_refresh
                    
                    if _script_settings:
                        obs.obs_data_set_string(_script_settings, "twitch_oauth_token", twitch_oauth_token)
                        if new_refresh:
                            obs.obs_data_set_string(_script_settings, "twitch_refresh_token", twitch_refresh_token)
                        obs.obs_data_set_int(_script_settings, "last_refresh_timestamp", int(time.time()))
                    
                    obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] ✓ Token OAuth refrescado correctamente (API Oficial).")
                    return True
        except urllib.error.HTTPError as he:
            try:
                err_body = he.read().decode('utf-8')
                obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Error al refrescar Token (API Oficial) ({he.code}): {he.reason}. Detalles: {err_body}")
            except Exception:
                obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Error al refrescar Token (API Oficial) ({he.code}): {he.reason}")
        except Exception as e:
            obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Error inesperado al refrescar Token (API Oficial): {e}")

    # CASO 2: Sin Client Secret (API de TwitchTokenGenerator)
    else:
        url = f"https://twitchtokengenerator.com/api/refresh/{twitch_refresh_token.strip()}"
        req = urllib.request.Request(url, method='GET')
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] Intentando refrescar Token usando la API de TwitchTokenGenerator...")
        try:
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                if res_data.get("success"):
                    new_access = res_data.get("token")
                    new_refresh = res_data.get("refresh")
                    
                    if new_access:
                        twitch_oauth_token = new_access
                        if new_refresh:
                            twitch_refresh_token = new_refresh
                        
                        if _script_settings:
                            obs.obs_data_set_string(_script_settings, "twitch_oauth_token", twitch_oauth_token)
                            if new_refresh:
                                obs.obs_data_set_string(_script_settings, "twitch_refresh_token", twitch_refresh_token)
                            obs.obs_data_set_int(_script_settings, "last_refresh_timestamp", int(time.time()))
                        
                        obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] ✓ Token OAuth refrescado correctamente (TwitchTokenGenerator API).")
                        return True
                else:
                    obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Falló el refresco en TwitchTokenGenerator: {res_data.get('message', 'Sin mensaje')}")
        except urllib.error.HTTPError as he:
            try:
                err_body = he.read().decode('utf-8')
                obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Error en TwitchTokenGenerator API ({he.code}): {he.reason}. Detalles: {err_body}")
            except Exception:
                obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Error en TwitchTokenGenerator API ({he.code}): {he.reason}")
        except Exception as e:
            obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Error inesperado en TwitchTokenGenerator API: {e}")

    return False

def update_stream_info_helix(title, category):
    """Actualiza la información vía Twitch Helix API utilizando el Token personalizado."""
    global twitch_oauth_token, twitch_refresh_token
    if not twitch_client_id or not twitch_oauth_token:
        obs.script_log(obs.LOG_WARNING, "[Set-Stream-Info] Falta Twitch Client ID o Twitch OAuth Token.")
        return

    headers = {
        "Client-ID": twitch_client_id,
        "Authorization": f"Bearer {twitch_oauth_token.replace('oauth:', '').strip()}",
        "Content-Type": "application/json"
    }

    global broadcaster_id
    current_broadcaster_id = broadcaster_id
    if not current_broadcaster_id or not current_broadcaster_id.strip():
        current_broadcaster_id = get_broadcaster_id_automatically(headers)
        if not current_broadcaster_id:
            obs.script_log(obs.LOG_WARNING, "[Set-Stream-Info] No se pudo obtener el Broadcaster ID automáticamente. La petición no continuará.")
            return

    game_id = None
    if category:
        game_id = get_game_id_by_name(category, headers)

    url = f"https://api.twitch.tv/helix/channels?broadcaster_id={current_broadcaster_id}"
    body = {}
    if title:
        body["title"] = title
    if game_id:
        body["game_id"] = game_id

    if not body:
        obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] No hay cambios que aplicar (título y categoría vacíos).")
        return

    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='PATCH')

    obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] Enviando petición a Twitch para actualizar canal (Broadcaster ID: {current_broadcaster_id})...")
    obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] Datos enviados: {json.dumps(body)}")

    try:
        with urllib.request.urlopen(req) as response:
            if response.status in (204, 200):
                obs.script_log(obs.LOG_INFO, "✓ [Set-Stream-Info] ¡Información del directo actualizada con éxito en Twitch!")
                return
            else:
                obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] Respuesta inesperada de la API: {response.status}")
    except urllib.error.HTTPError as he:
        if he.code == 401:
            obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] El token parece haber caducado (401 Unauthorized). Intentando refrescar automáticamente...")
            if refresh_twitch_token():
                headers["Authorization"] = f"Bearer {twitch_oauth_token.strip()}"
                req_retry = urllib.request.Request(url, data=data, headers=headers, method='PATCH')
                try:
                    with urllib.request.urlopen(req_retry) as res_retry:
                        if res_retry.status in (204, 200):
                            obs.script_log(obs.LOG_INFO, "✓ [Set-Stream-Info] ¡Información del directo actualizada tras refrescar el token!")
                            return
                except Exception as retry_err:
                    obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] Fallo en el reintento tras refrescar token: {retry_err}")
                    open_manual_token_generator()
            else:
                obs.script_log(obs.LOG_ERROR, "[Set-Stream-Info] No se pudo refrescar el token automáticamente. Abriendo generador manual...")
                open_manual_token_generator()
        
        try:
            err_body = he.read().decode('utf-8')
            obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] ❌ Error HTTP {he.code}: {he.reason}. Respuesta de Twitch: {err_body}")
        except Exception:
            obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] ❌ Error HTTP {he.code} actualizando canal: {he.reason}")
    except Exception as e:
        obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] ❌ Error inesperado al actualizar canal: {e}")



def on_apply_clicked(properties, property):
    execute_stream_info_update()

def handle_scene_changed():
    """Maneja el evento de cambio de escena refrescando la UI de OBS y aplicando actualización."""
    active_scene = get_active_scene_name()
    t_saved, c_saved = get_scene_data_from_obs(_script_settings, active_scene)
    
    if _script_settings:
        obs.obs_data_set_string(_script_settings, "current_scene_title", t_saved)
        obs.obs_data_set_string(_script_settings, "current_scene_category", c_saved)

    if update_mode == 0:
        return
    elif update_mode == 1:
        obs.timer_remove(execute_stream_info_update)
        execute_stream_info_update()
    elif update_mode == 2:
        obs.timer_remove(execute_stream_info_update)
        obs.timer_add(execute_stream_info_update, delay_seconds * 1000)
        obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] Cambio a escena '{active_scene}' detectado. Programado en {delay_seconds}s...")

def on_event(event):
    if not enabled:
        return
    if event == obs.OBS_FRONTEND_EVENT_SCENE_CHANGED:
        handle_scene_changed()

def check_obs_twitch_integration():
    """Comprueba el servicio de streaming configurado en OBS y valida el token de Twitch."""
    service = obs.obs_frontend_get_streaming_service()
    if not service:
        obs.script_log(obs.LOG_WARNING, "[Set-Stream-Info] [Check] No se ha detectado ningún servicio de streaming configurado en OBS.")
    else:
        service_name = obs.obs_service_get_name(service)
        service_type = obs.obs_service_get_type(service)
        obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] [Check] Servicio configurado en OBS: '{service_name}' ({service_type})")
        
        settings = obs.obs_service_get_settings(service)
        if settings:
            svc_name = obs.obs_data_get_string(settings, "service")
            server = obs.obs_data_get_string(settings, "server")
            key = obs.obs_data_get_string(settings, "key")
            key_masked = f"{key[:6]}... (oculto)" if key else "No configurada"
            obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] [Check] Nombre del servicio en ajustes: '{svc_name}' | Ingest: '{server}' | Clave: {key_masked}")
            obs.obs_data_release(settings)

    # Validar el token actual de Twitch
    if not twitch_oauth_token or not twitch_oauth_token.strip():
        obs.script_log(obs.LOG_WARNING, "[Set-Stream-Info] [Check] ❌ No hay ningún Twitch OAuth Token configurado en el script.")
        open_manual_token_generator()
        return

    headers = {
        "Client-ID": twitch_client_id.strip() if twitch_client_id else "",
        "Authorization": f"Bearer {twitch_oauth_token.replace('oauth:', '').strip()}",
        "Content-Type": "application/json"
    }

    obs.script_log(obs.LOG_INFO, "[Set-Stream-Info] [Check] Verificando validez del Token con Twitch...")
    url = "https://api.twitch.tv/helix/users"
    req = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            users = res_data.get("data", [])
            if users:
                obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] [Check] ✓ ¡Token VÁLIDO! Conectado como: {users[0].get('login')} (ID: {users[0].get('id')})")
                return
    except urllib.error.HTTPError as he:
        if he.code == 401:
            obs.script_log(obs.LOG_WARNING, "[Set-Stream-Info] [Check] ⚠ El Token actual ha caducado o es inválido. Intentando refrescar automáticamente...")
            if refresh_twitch_token():
                headers["Authorization"] = f"Bearer {twitch_oauth_token.strip()}"
                req_retry = urllib.request.Request(url, headers=headers, method='GET')
                try:
                    with urllib.request.urlopen(req_retry) as res_retry:
                        res_data = json.loads(res_retry.read().decode('utf-8'))
                        users = res_data.get("data", [])
                        if users:
                            obs.script_log(obs.LOG_INFO, f"[Set-Stream-Info] [Check] ✓ ¡Token refrescado y verificado con éxito! Conectado como: {users[0].get('login')}")
                            return
                except Exception:
                    pass
            obs.script_log(obs.LOG_ERROR, "[Set-Stream-Info] [Check] ❌ ERROR CRÍTICO: El token ha caducado y no se pudo refrescar.")
            obs.script_log(obs.LOG_ERROR, "                      Abriendo generador de token en el navegador...")
            open_manual_token_generator()
        else:
            obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] [Check] Error de red al verificar token: {he.reason}")
    except Exception as e:
        obs.script_log(obs.LOG_ERROR, f"[Set-Stream-Info] [Check] Error inesperado al verificar token: {e}")

def script_load(settings):
    global _script_settings
    _script_settings = settings
    obs.obs_frontend_add_event_callback(on_event)
    obs.script_log(obs.LOG_INFO, "Set-Stream-Info cargado correctamente.")

def script_unload():
    obs.timer_remove(execute_stream_info_update)
    obs.script_log(obs.LOG_INFO, "Set-Stream-Info descargado.")


