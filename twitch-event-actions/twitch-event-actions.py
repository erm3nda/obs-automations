import obspython as obs
import threading
import socket
import ssl
import time
import re
import urllib.request
import json
import webbrowser
import textwrap

# --- Variables Globales ---
_script_settings = None
_irc_stop_event = threading.Event()
_irc_thread = None
_irc_socket = None
_active_hide_timers = {}
_chat_message_history = []
_test_msg_counter = 0

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
chat_max_lines = 4
chat_max_chars = 40
chat_duration = 5

subscriptions_enabled = False
sub_target_type = 1
sub_target_source = "twitch_subscription"
sub_duration = 5

# Cola segura para despacho entre hilos
_chat_queue = []

def script_description():
    return "Twitch Event Actions: Automatiza acciones en OBS basadas en eventos de Twitch."

def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_bool(props, "enabled", "Activar Script")
    
    # Auth Group
    auth_props = obs.obs_properties_create()
    obs.obs_properties_add_text(auth_props, "client_id", "Twitch Client ID", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(auth_props, "oauth_token", "Twitch OAuth Token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(auth_props, "refresh_token", "Twitch Refresh Token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(auth_props, "twitch_scopes", "Scopes", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_button(auth_props, "generate_token", "Generar Token (Navegador)", on_generate_token)
    obs.obs_properties_add_button(auth_props, "refresh_token_btn", "Refrescar Token", on_refresh_token)
    obs.obs_properties_add_button(auth_props, "get_id_button", "Detectar ID y Canal", on_get_broadcaster_id)
    obs.obs_properties_add_group(props, "auth_group", "Configuración Twitch", obs.OBS_GROUP_NORMAL, auth_props)

    # Chat Group
    chat_props = obs.obs_properties_create()
    obs.obs_properties_add_bool(chat_props, "chat_enabled", "Activar Chat")
    p_chat_type = obs.obs_properties_add_list(
        chat_props, "chat_target_type", "Tipo de Acción Chat",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_INT
    )
    obs.obs_property_list_add_int(p_chat_type, "Lista de chat acumulativa", 0)
    obs.obs_property_list_add_int(p_chat_type, "Mostrar/Ocultar Fuente o Escena", 1)
    obs.obs_property_list_add_int(p_chat_type, "Actualizar Texto único", 2)

    p_chat_source = obs.obs_properties_add_list(
        chat_props, "chat_target_source", "Fuente/Escena de Chat",
        obs.OBS_COMBO_TYPE_EDITABLE, obs.OBS_COMBO_FORMAT_STRING
    )
    obs.obs_properties_add_int(chat_props, "chat_max_messages", "Número máximo de mensajes", 1, 50, 1)
    obs.obs_properties_add_int(chat_props, "chat_max_chars", "Máximo caracteres por línea (Wrap)", 10, 200, 1)
    obs.obs_properties_add_int(chat_props, "chat_duration", "Ocultar / Borrar tras (segundos)", 1, 300, 1)
    obs.obs_properties_add_button(chat_props, "test_chat_button", "▶ Probar Acción Chat", on_test_chat)
    
    # Rellenar fuentes
    sources = obs.obs_enum_sources()
    if sources is not None:
        for source in sources:
            name = obs.obs_source_get_name(source)
            obs.obs_property_list_add_string(p_chat_source, name, name)
        obs.source_list_release(sources)
    
    obs.obs_properties_add_group(props, "chat_actions", "⭐ Evento: Chat", obs.OBS_GROUP_NORMAL, chat_props)
    
    return props

def script_defaults(settings):
    obs.obs_data_set_default_bool(settings, "enabled", True)
    obs.obs_data_set_default_string(settings, "client_id", "")
    obs.obs_data_set_default_string(settings, "oauth_token", "")
    obs.obs_data_set_default_string(settings, "refresh_token", "")
    obs.obs_data_set_default_string(settings, "twitch_scopes", "channel:manage:broadcast user:read:chat")
    obs.obs_data_set_default_string(settings, "broadcaster_id", "")
    obs.obs_data_set_default_string(settings, "chat_channel", "")
    
    obs.obs_data_set_default_bool(settings, "chat_enabled", False)
    obs.obs_data_set_default_int(settings, "chat_target_type", 0)
    obs.obs_data_set_default_string(settings, "chat_target_source", "twitch_chat")
    obs.obs_data_set_default_int(settings, "chat_max_messages", 5)
    obs.obs_data_set_default_int(settings, "chat_max_chars", 40)
    obs.obs_data_set_default_int(settings, "chat_duration", 20)

def script_update(settings):
    global enabled, _script_settings, client_id, oauth_token, refresh_token, twitch_scopes, broadcaster_id, chat_channel
    global chat_enabled, chat_target_type, chat_target_source, chat_max_messages, chat_max_chars, chat_duration

    _script_settings = settings
    enabled = obs.obs_data_get_bool(settings, "enabled")
    client_id = obs.obs_data_get_string(settings, "client_id").strip()
    oauth_token = obs.obs_data_get_string(settings, "oauth_token").strip()
    refresh_token = obs.obs_data_get_string(settings, "refresh_token").strip()
    twitch_scopes = obs.obs_data_get_string(settings, "twitch_scopes").strip()
    broadcaster_id = obs.obs_data_get_string(settings, "broadcaster_id").strip()
    chat_channel = obs.obs_data_get_string(settings, "chat_channel").strip().lstrip("#")

    chat_enabled = obs.obs_data_get_bool(settings, "chat_enabled")
    chat_target_type = obs.obs_data_get_int(settings, "chat_target_type")
    chat_target_source = obs.obs_data_get_string(settings, "chat_target_source").strip()
    chat_max_messages = obs.obs_data_get_int(settings, "chat_max_messages")
    chat_max_chars = obs.obs_data_get_int(settings, "chat_max_chars")
    chat_duration = obs.obs_data_get_int(settings, "chat_duration")

    _restart_irc_listener()

def on_refresh_token(properties, property):
    global oauth_token, refresh_token, client_id
    if not refresh_token:
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
                obs.obs_data_set_string(_script_settings, "oauth_token", oauth_token)
                obs.obs_data_set_string(_script_settings, "refresh_token", refresh_token)
    except:
        pass
    return True

def on_generate_token(properties, property):
    webbrowser.open("https://twitchtokengenerator.com/")
    return True

def on_get_broadcaster_id(properties, property):
    global broadcaster_id, chat_channel
    if not client_id or not oauth_token: return True
    request = urllib.request.Request(
        "https://api.twitch.tv/helix/users",
        headers={"Client-ID": client_id, "Authorization": "Bearer {}".format(oauth_token.replace("oauth:", "").strip())}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            users = json.loads(response.read().decode("utf-8")).get("data", [])
        if users:
            broadcaster_id = users[0].get("id", "")
            chat_channel = users[0].get("login", "")
            if _script_settings:
                obs.obs_data_set_string(_script_settings, "broadcaster_id", broadcaster_id)
                obs.obs_data_set_string(_script_settings, "chat_channel", chat_channel)
            _restart_irc_listener()
    except:
        pass
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

def _process_chat_queue():
    global _chat_queue
    if _chat_queue:
        for payload in _chat_queue:
            trigger_event_action(chat_target_type, chat_target_source, chat_duration, payload)
        _chat_queue = []

def _restart_irc_listener():
    global _irc_stop_event, _irc_thread, _irc_socket
    _irc_stop_event.set()
    if _irc_thread: _irc_thread.join(timeout=1.0)
    _irc_stop_event = threading.Event()
    _irc_thread = threading.Thread(target=_irc_worker_loop, daemon=True)
    _irc_thread.start()

def _irc_worker_loop():
    global _irc_socket
    server, port = "irc.chat.twitch.tv", 6697
    channel_name = chat_channel.lower().lstrip("#")
    
    while not _irc_stop_event.is_set():
        try:
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(5.0)
            _irc_socket = ssl.create_default_context().wrap_socket(raw_sock, server_hostname=server)
            _irc_socket.connect((server, port))
            _irc_socket.sendall(f"PASS oauth:{oauth_token.replace('oauth:', '').strip()}\r\n".encode())
            _irc_socket.sendall(f"NICK justinfan12345\r\n".encode())
            _irc_socket.sendall(f"JOIN #{channel_name}\r\n".encode())
            
            buffer = ""
            while not _irc_stop_event.is_set():
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
                        _chat_queue.append(f"{match.group(1)}: {match.group(2)}")
        except: 
            time.sleep(5)

def on_test_chat(props, prop):
    global _test_msg_counter
    _chat_queue.append(f"UsuarioTest_{_test_msg_counter}: Este es un mensaje de prueba bastante largo para verificar el ajuste automático de caracteres y líneas en el panel de OBS.")
    _test_msg_counter += 1
    return True

def script_load(settings):
    obs.timer_add(_process_chat_queue, 100)
    script_update(settings)

def script_unload():
    _irc_stop_event.set()
    if _irc_socket: _irc_socket.close()
