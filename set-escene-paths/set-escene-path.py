import obspython as obs
import os
import re
import glob
import json
import difflib
import shutil
import socket
import struct
import hashlib
import base64
import uuid
import tempfile
import threading
import time

try:
    import cv2
    import numpy as np
    _has_opencv = True
except ImportError:
    _has_opencv = False

# ─── Config defaults ──────────────────────────────────────────────────────────
enabled              = True
base_folder          = ""      # debe configurarse en el plugin antes de funcionar
base_filename_format = ""
keep_recording       = False
auto_start_recording = False
auto_start_replay    = False
apply_vertical_paths = True
auto_start_vertical_recording = False
auto_start_vertical_backtrack = False
keep_vertical_recording = False
move_vertical_files = False

# Limpieza de grabaciones "en negro"
enable_cleanup    = False
min_size_mb       = 25            # Tamaño mínimo en MB para conservar el vídeo (0-500)
cleanup_threshold = 90        # % mínimo de negro para eliminar (0-100)
enable_vertical_cleanup = False
vertical_min_size_mb = 25
vertical_cleanup_threshold = 90

# Mapeo de nombres de escena a nombres de juegos reales
# Añade aquí tus alias. Ejemplo: 'fv5' -> 'Battlefield V'
SCENE_ALIASES = {
    'fv5': 'Battlefield V',
    'bf6': 'Battlefield 6',
    'pubg': 'PLAYERUNKNOWN\'S BATTLEGROUNDS',
    'ac': 'Assetto Corsa',
    'acevo': 'Assetto Corsa EVO',
    'rf2': 'rFactor 2(rFactor2.exe)',
}

# Intencion capturada en el momento del cambio de escena
_want_restart_recording = False
_want_restart_replay    = False

# Flags para arrancar TRAS recibir el evento STOPPED (stop es asincrono)
_pending_start_recording = False
_pending_start_replay    = False

# Tracking de carpeta activa para cleanup
_current_recording_folder = None
_active_recording_folder  = None
_vertical_event_listener_thread = None
_vertical_event_listener_stop = threading.Event()
_vertical_event_listener_socket = None
_vertical_move_lock = threading.Lock()
_vertical_processed_files = set()
_vertical_move_lock_path = os.path.join(tempfile.gettempdir(), "set-escene-path.vertical-move.lock")
_vertical_target_folder = None
_vertical_source_scene = None


# ─── Script metadata ──────────────────────────────────────────────────────────

def script_description():
    return (
        "<b>Set-Escene-Path</b><br>"
        "Cambia el path de grabación y replay buffer según el <b>nombre de la escena</b> activa.<br><br>"
        "• <b>Mantener grabación:</b> no interrumpe, aplica en la próxima sesión.<br>"
        "• <b>Auto-start:</b> detiene y reinicia en el nuevo path automáticamente.<br>"
        "• <b>Grabación vertical:</b> intenta usar el path horizontal.<br>"
        "• <b>Backtrack vertical:</b> usa un path estático; activa mover ficheros si hace falta.<br>"
        "<i>El stream nunca se toca.</i>"
    )

def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_bool(props, "enabled", "✅ Plugin Activo")

    horizontal_props = obs.obs_properties_create()
    obs.obs_properties_add_path(
        horizontal_props, "base_folder", "Carpeta de grabación",
        obs.OBS_PATH_DIRECTORY, "", None
    )
    obs.obs_properties_add_text(
        horizontal_props, "base_filename_format",
        "Formato de nombre",
        obs.OBS_TEXT_DEFAULT
    )
    obs.obs_properties_add_bool(
        horizontal_props, "keep_recording",
        "Mantener grabación activa"
    )
    obs.obs_properties_add_bool(
        horizontal_props, "auto_start_recording",
        "Auto-start: grabación"
    )
    obs.obs_properties_add_bool(
        horizontal_props, "auto_start_replay",
        "Auto-start: replay buffer"
    )
    obs.obs_properties_add_bool(
        horizontal_props, "enable_cleanup",
        "Limpiar archivos"
    )
    obs.obs_properties_add_int(
        horizontal_props, "min_size_mb",
        "Tamaño mínimo (MB)", 0, 500, 1
    )
    obs.obs_properties_add_int_slider(
        horizontal_props, "cleanup_threshold",
        "% de negro para eliminar", 50, 100, 1
    )
    obs.obs_properties_add_group(
        props, "horizontal_settings", "Ajustes de OBS horizontal",
        obs.OBS_GROUP_NORMAL, horizontal_props
    )

    if get_vertical_config_path():
        vertical_props = obs.obs_properties_create()
        obs.obs_properties_add_bool(
            vertical_props, "apply_vertical_paths", "Activar en vertical"
        )
        obs.obs_properties_add_bool(
            vertical_props, "keep_vertical_recording",
            "Mantener grabación activa"
        )
        obs.obs_properties_add_bool(
            vertical_props, "auto_start_vertical_recording",
            "Auto-start: grabación"
        )
        obs.obs_properties_add_bool(
            vertical_props, "auto_start_vertical_backtrack",
            "Auto-start: backtrack"
        )
        obs.obs_properties_add_text(
            vertical_props, "vertical_backtrack_note",
            "Nota: activa Backtrack; su checkbox no cambia",
            obs.OBS_TEXT_INFO
        )
        obs.obs_properties_add_bool(
            vertical_props, "move_vertical_files", "Mover ficheros automáticamente"
        )
        obs.obs_properties_add_bool(
            vertical_props, "enable_vertical_cleanup", "Limpiar archivos"
        )
        obs.obs_properties_add_int(
            vertical_props, "vertical_min_size_mb", "Tamaño mínimo (MB)", 0, 500, 1
        )
        obs.obs_properties_add_int_slider(
            vertical_props, "vertical_cleanup_threshold",
            "% de negro para eliminar", 50, 100, 1
        )
        obs.obs_properties_add_group(
            props, "vertical_settings", "Ajustes de Aitum Vertical",
            obs.OBS_GROUP_NORMAL, vertical_props
        )

    return props

def script_defaults(settings):
    obs.obs_data_set_default_bool(settings, "enabled", True)
    obs.obs_data_set_default_string(settings, "base_folder", "")
    obs.obs_data_set_default_string(settings, "base_filename_format", "")
    obs.obs_data_set_default_bool(settings, "keep_recording",       False)
    obs.obs_data_set_default_bool(settings, "auto_start_recording", False)
    obs.obs_data_set_default_bool(settings, "auto_start_replay",    False)
    obs.obs_data_set_default_bool(settings, "apply_vertical_paths", True)
    obs.obs_data_set_default_bool(settings, "move_vertical_files", False)
    obs.obs_data_set_default_bool(settings, "auto_start_vertical_recording", False)
    obs.obs_data_set_default_bool(settings, "auto_start_vertical_backtrack", False)
    obs.obs_data_set_default_bool(settings, "keep_vertical_recording", False)
    obs.obs_data_set_default_bool(settings, "enable_cleanup",       False)
    obs.obs_data_set_default_int(settings,  "min_size_mb",          25)
    obs.obs_data_set_default_int(settings,  "cleanup_threshold",    90)
    obs.obs_data_set_default_bool(settings, "enable_vertical_cleanup", False)
    obs.obs_data_set_default_int(settings,  "vertical_min_size_mb", 25)
    obs.obs_data_set_default_int(settings,  "vertical_cleanup_threshold", 90)

def script_update(settings):
    global enabled, base_folder, keep_recording, auto_start_recording, auto_start_replay
    global apply_vertical_paths
    global auto_start_vertical_recording, auto_start_vertical_backtrack
    global keep_vertical_recording
    global move_vertical_files
    global enable_cleanup, min_size_mb, cleanup_threshold
    global enable_vertical_cleanup, vertical_min_size_mb, vertical_cleanup_threshold
    global _current_recording_folder, base_filename_format
    enabled              = obs.obs_data_get_bool(settings, "enabled")
    val = obs.obs_data_get_string(settings, "base_folder")
    base_folder          = val if val else ""
    keep_recording       = obs.obs_data_get_bool(settings, "keep_recording")
    auto_start_recording = obs.obs_data_get_bool(settings, "auto_start_recording")
    auto_start_replay    = obs.obs_data_get_bool(settings, "auto_start_replay")
    if not obs.obs_data_has_user_value(settings, "apply_vertical_paths"):
        obs.obs_data_set_bool(settings, "apply_vertical_paths", True)
    apply_vertical_paths = obs.obs_data_get_bool(settings, "apply_vertical_paths")
    auto_start_vertical_recording = obs.obs_data_get_bool(settings, "auto_start_vertical_recording")
    auto_start_vertical_backtrack = obs.obs_data_get_bool(settings, "auto_start_vertical_backtrack")
    keep_vertical_recording = obs.obs_data_get_bool(settings, "keep_vertical_recording")
    move_vertical_files = obs.obs_data_get_bool(settings, "move_vertical_files")
    enable_cleanup       = obs.obs_data_get_bool(settings, "enable_cleanup")
    min_size_mb          = obs.obs_data_get_int(settings,  "min_size_mb")
    cleanup_threshold    = obs.obs_data_get_int(settings,  "cleanup_threshold")
    enable_vertical_cleanup = obs.obs_data_get_bool(settings, "enable_vertical_cleanup")
    vertical_min_size_mb = obs.obs_data_get_int(settings, "vertical_min_size_mb")
    vertical_cleanup_threshold = obs.obs_data_get_int(settings, "vertical_cleanup_threshold")
    
    # Inicializar base_filename_format si no está establecido
    base_filename_format = obs.obs_data_get_string(settings, "base_filename_format")
    if not base_filename_format:
        config = obs.obs_frontend_get_profile_config()
        if config:
            val_format = obs.config_get_string(config, "Output", "FilenameFormatting")
            if val_format:
                base_filename_format = val_format
                obs.obs_data_set_string(settings, "base_filename_format", val_format)
                
    # Asegurar que el path de la escena actual se aplica (ej: al cargar el script o cambiar la carpeta base)
    if base_folder:
        try:
            sc = obs.obs_frontend_get_current_scene()
            if sc:
                scene_name = obs.obs_source_get_name(sc)
                obs.obs_source_release(sc)
                set_paths_for_scene(scene_name)
        except Exception as e:
            obs.script_log(obs.LOG_WARNING, "No se pudo pre-inicializar el path de escena: {}".format(e))

    if enable_cleanup and not _has_opencv:
        obs.script_log(obs.LOG_WARNING,
            "[Cleanup] ADVERTENCIA: La limpieza de videos está activada pero OpenCV (cv2) no está disponible. "
            "Por favor, instala opencv-python en tu entorno de Python (ej: pip install opencv-python numpy) para usar esta función.")


# ─── Helpers de nombre y path ─────────────────────────────────────────────────

def clean_name_for_folder(name):
    """
    Limpia el nombre de escena para usarlo como nombre de carpeta Windows.
      'BFV escene'                    -> 'BFV'
      'Battlefield™ 2042'             -> 'Battlefield 2042'
      'Call of Duty®: Modern Warfare' -> 'Call of Duty  Modern Warfare'
    """
    # 1. Aplicar alias si existe (usando limites de palabra para evitar falsos positivos como 'ac' en 'facecam')
    lower_name = name.lower()
    for alias, real_name in SCENE_ALIASES.items():
        if re.search(r'\b' + re.escape(alias) + r'\b', lower_name):
            return real_name

    # 2. Limpieza estándar si no hay alias
    name = re.sub(r'[\u2122\u00ae\u00a9]', '', name)
    name = re.sub(r'\s*\((tm|r|c)\)\s*', ' ', name, flags=re.IGNORECASE)
    name = re.sub(r'[<>:"/\\|?*]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    # Quitar sufijo " escene" (con o sin espacio, case-insensitive)
    name = re.sub(r'\s*escene\s*$', '', name, flags=re.IGNORECASE).strip()
    return name if name else "unnamed"

def clean_name_for_filename(name):
    """
    Devuelve el nombre del juego usando la misma lógica que las carpetas
    para mantener consistencia.
    """
    return clean_name_for_folder(name)

def scene_match_key(name):
    """Devuelve las dos primeras palabras normalizadas de una escena."""
    words = [
        word for word in re.findall(r"[a-z0-9]+", name.lower())
        if word not in ("escene", "scene", "vertical", "horizontal")
    ]
    return tuple(words[:2])

def find_best_vertical_scene(horizontal_scene, vertical_scenes):
    """Busca la escena vertical más parecida usando las dos primeras palabras."""
    horizontal_key = scene_match_key(horizontal_scene)
    if not horizontal_key:
        return None

    candidates = [
        scene for scene in vertical_scenes
        if scene_match_key(scene) == horizontal_key
    ]
    if not candidates:
        return None

    normalized_horizontal = " ".join(re.findall(r"[a-z0-9]+", horizontal_scene.lower()))
    return max(
        candidates,
        key=lambda scene: difflib.SequenceMatcher(
            None,
            normalized_horizontal,
            " ".join(re.findall(r"[a-z0-9]+", scene.lower()))
        ).ratio()
    )

def _ws_read_frame(sock):
    def receive_exact(size):
        data = b""
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    header = receive_exact(2)
    if not header:
        return None
    first, second = header
    length = second & 0x7F
    if length == 126:
        extended = receive_exact(2)
        if not extended:
            return None
        length = struct.unpack(">H", extended)[0]
    elif length == 127:
        extended = receive_exact(8)
        if not extended:
            return None
        length = struct.unpack(">Q", extended)[0]
    if second & 0x80:
        mask = receive_exact(4)
        if not mask:
            return None
    else:
        mask = None
    payload = receive_exact(length)
    if payload is None:
        return None
    if mask:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return first & 0x0F, payload

def _ws_send_text(sock, text):
    payload = text.encode("utf-8")
    mask = os.urandom(4)
    payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    length = len(payload)
    if length < 126:
        header = bytes([0x81, 0x80 | length])
    elif length < 65536:
        header = bytes([0x81, 0x80 | 126]) + struct.pack(">H", length)
    else:
        header = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", length)
    sock.sendall(header + mask + payload)

def aitum_vendor_request(request_type, request_data=None):
    """Invoca una petición del vendor oficial de Aitum Vertical por OBS WebSocket."""
    request_id = str(uuid.uuid4())
    request = {
        "op": 6,
        "d": {
            "requestType": "CallVendorRequest",
            "requestId": request_id,
            "requestData": {
                "vendorName": "aitum-vertical-canvas",
                "requestType": request_type,
                "requestData": request_data or {}
            }
        }
    }
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        sock.connect(("127.0.0.1", 4455))
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            "GET / HTTP/1.1\r\n"
            "Host: 127.0.0.1:4455\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: {}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).format(key)
        sock.sendall(handshake.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(1)
            if not chunk:
                break
            response += chunk
        if b"101" not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("OBS WebSocket rechazó el handshake")

        hello_frame = _ws_read_frame(sock)
        if not hello_frame:
            raise RuntimeError("OBS WebSocket no envió Hello")
        try:
            hello = json.loads(hello_frame[1].decode("utf-8"))
        except Exception:
            raise RuntimeError("Hello inválido, frame={}".format(hello_frame[1][:32].hex()))
        if hello.get("op") != 0:
            raise RuntimeError("Respuesta inicial inesperada de OBS WebSocket")

        identify = {"op": 1, "d": {"rpcVersion": 1}}
        _ws_send_text(sock, json.dumps(identify, separators=(",", ":")))
        while True:
            identified = _ws_read_frame(sock)
            if not identified:
                raise RuntimeError("OBS WebSocket no aceptó Identify")
            if identified[0] == 9:
                continue
            if identified[0] == 8:
                raise RuntimeError("OBS WebSocket cerró durante Identify")
            identified_message = json.loads(identified[1].decode("utf-8"))
            if identified_message.get("op") == 2:
                break

        _ws_send_text(sock, json.dumps(request, separators=(",", ":")))
        while True:
            frame = _ws_read_frame(sock)
            if not frame:
                raise RuntimeError("OBS WebSocket cerró la conexión")
            if frame[0] in (8, 9, 10):
                continue
            message = json.loads(frame[1].decode("utf-8"))
            if message.get("op") == 7 and message.get("d", {}).get("requestId") == request_id:
                vendor_response = message.get("d", {}).get("responseData", {})
                return vendor_response.get("responseData", vendor_response)
    except Exception as error:
        obs.script_log(obs.LOG_WARNING, "Aitum Vertical WebSocket: {}".format(error))
    finally:
        sock.close()
    return None

def _find_vertical_file(event_type, minimum_time):
    config_path = get_vertical_config_path()
    if not config_path:
        return None
    try:
        with open(config_path, "r", encoding="utf-8-sig") as config_file:
            config_data = json.load(config_file)
        paths = set()
        key = "backtrack_path" if event_type == "backtrack_saved" else "record_path"
        for canvas in config_data.get("canvas", []):
            path = canvas.get(key, "")
            if path:
                paths.add(path)

        # Aitum puede guardar una ruta distinta de la que usa en memoria.
        # Incluye las carpetas hermanas de la raíz horizontal como respaldo.
        if base_folder:
            parent_folder = os.path.dirname(os.path.normpath(base_folder))
            if os.path.isdir(parent_folder):
                for child in os.listdir(parent_folder):
                    candidate = os.path.join(parent_folder, child)
                    if os.path.isdir(candidate):
                        paths.add(candidate)

        obs.script_log(
            obs.LOG_INFO,
            "Buscando archivo vertical '{}' desde: {}".format(
                event_type, "; ".join(sorted(paths))
            )
        )

        marker = "backtrack" if event_type == "backtrack_saved" else None
        candidates = []
        for folder in paths:
            if not os.path.isdir(folder):
                continue
            for item in os.listdir(folder):
                path = os.path.join(folder, item)
                if (os.path.isfile(path)
                    and item.lower().endswith((".mkv", ".mp4", ".mov", ".ts", ".flv"))
                    and (marker is None or marker in item.lower())
                    and os.path.getmtime(path) >= minimum_time - 2):
                    candidates.append(path)
        if candidates:
            selected = max(candidates, key=os.path.getmtime)
            obs.script_log(obs.LOG_INFO, "Candidato vertical encontrado -> '{}'".format(selected))
            return selected
        obs.script_log(obs.LOG_WARNING, "Sin candidatos verticales en las rutas inspeccionadas.")
        return None
    except Exception as error:
        obs.script_log(obs.LOG_WARNING, "No se pudo localizar archivo vertical: {}".format(error))
        return None

def _organize_vertical_file(event_type, target, event_time):
    if not apply_vertical_paths:
        return
    try:
        lock_handle = os.open(_vertical_move_lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(lock_handle)
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(_vertical_move_lock_path) > 30:
                os.remove(_vertical_move_lock_path)
                lock_handle = os.open(_vertical_move_lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(lock_handle)
            else:
                return
        except (FileNotFoundError, FileExistsError):
            return

    try:
        _organize_vertical_file_locked(event_type, target, event_time)
    finally:
        try:
            os.remove(_vertical_move_lock_path)
        except FileNotFoundError:
            pass

def _organize_vertical_file_locked(event_type, target, event_time):
    source = None
    for _ in range(12):
        time.sleep(0.5)
        source = _find_vertical_file(event_type, event_time)
        if source:
            break
    if not source:
        obs.script_log(obs.LOG_WARNING, "No se encontró archivo vertical tras '{}'.".format(event_type))
        return
    if not move_vertical_files:
        if enable_vertical_cleanup:
            trigger_cleanup_for_file(
                source, vertical_min_size_mb, vertical_cleanup_threshold
            )
        return
    ensure_folder(target)
    destination = os.path.join(target, os.path.basename(source))
    try:
        with _vertical_move_lock:
            if source in _vertical_processed_files:
                return
        shutil.move(source, destination)
        with _vertical_move_lock:
            _vertical_processed_files.add(source)
        obs.script_log(obs.LOG_INFO, "Archivo vertical movido -> '{}'".format(destination))
        if enable_vertical_cleanup:
            trigger_cleanup_for_file(
                destination, vertical_min_size_mb, vertical_cleanup_threshold
            )
    except Exception as error:
        obs.script_log(obs.LOG_WARNING, "No se pudo mover archivo vertical: {}".format(error))

def _handle_vertical_vendor_event(event_type):
    if not _vertical_target_folder:
        return
    event_time = time.time()
    thread = threading.Thread(target=_organize_vertical_file, args=(event_type, _vertical_target_folder, event_time))
    thread.daemon = True
    thread.start()

def _vertical_event_listener():
    global _vertical_event_listener_socket
    while not _vertical_event_listener_stop.is_set():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _vertical_event_listener_socket = sock
        sock.settimeout(1.0)
        try:
            sock.connect(("127.0.0.1", 4455))
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            handshake = (
                "GET / HTTP/1.1\r\nHost: 127.0.0.1:4455\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Key: {}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).format(key)
            sock.sendall(handshake.encode("ascii"))
            response = b""
            while b"\r\n\r\n" not in response:
                response += sock.recv(1)
            _ws_read_frame(sock)
            _ws_send_text(sock, json.dumps({"op": 1, "d": {"rpcVersion": 1, "eventSubscriptions": 576}}))
            identified = _ws_read_frame(sock)
            if not identified:
                raise RuntimeError("listener no identificado")
            obs.script_log(obs.LOG_INFO, "Listener de eventos de Aitum conectado.")
            while not _vertical_event_listener_stop.is_set():
                try:
                    frame = _ws_read_frame(sock)
                except socket.timeout:
                    continue
                if not frame:
                    break
                if frame[0] in (8, 9, 10):
                    continue
                message = json.loads(frame[1].decode("utf-8"))
                if message.get("op") != 5:
                    continue
                data = message.get("d", {})
                if data.get("eventType") == "ReplayBufferSaved":
                    saved_path = data.get("eventData", {}).get("savedReplayPath")
                    if enable_cleanup and saved_path:
                        print("[Cleanup] Replay horizontal guardado: '{}'".format(saved_path))
                        trigger_cleanup_for_file(saved_path)
                    continue
                if data.get("eventType") != "VendorEvent":
                    continue
                event_data = data.get("eventData", {})
                if event_data.get("vendorName") != "aitum-vertical-canvas":
                    continue
                event_type = event_data.get("eventType")
                if event_type in ("backtrack_saved", "recording_stopped"):
                    _handle_vertical_vendor_event(event_type)
        except Exception as error:
            if not _vertical_event_listener_stop.is_set():
                obs.script_log(obs.LOG_WARNING, "Listener Aitum: {}".format(error))
        finally:
            sock.close()
            _vertical_event_listener_socket = None
        _vertical_event_listener_stop.wait(3.0)

def switch_vertical_scene(horizontal_scene):
    response = aitum_vendor_request("get_scenes")
    if not response:
        return None
    vertical_scenes = [item.get("name", "") for item in response.get("scenes", [])]
    vertical_scene = find_best_vertical_scene(horizontal_scene, vertical_scenes)
    if not vertical_scene:
        obs.script_log(obs.LOG_WARNING, "No se encontró escena vertical para '{}'.".format(horizontal_scene))
        return None
    aitum_vendor_request("switch_scene", {"scene": vertical_scene})
    obs.script_log(obs.LOG_INFO, "Escena vertical cambiada -> '{}'".format(vertical_scene))
    return vertical_scene

def update_vertical_scene_controls(horizontal_scene):
    if not apply_vertical_paths:
        return
    switch_vertical_scene(horizontal_scene)
    if auto_start_vertical_backtrack:
        aitum_vendor_request("stop_backtrack")
        time.sleep(0.5)
        aitum_vendor_request("start_backtrack")
        obs.script_log(obs.LOG_INFO, "Backtrack vertical reiniciado.")
    status = aitum_vendor_request("status") or {}
    recording_active = bool(status.get("recording"))
    if keep_vertical_recording:
        if recording_active:
            obs.script_log(obs.LOG_INFO, "Grabación vertical mantenida al cambiar de escena.")
        elif auto_start_vertical_recording:
            aitum_vendor_request("start_recording")
            obs.script_log(obs.LOG_INFO, "Grabación vertical iniciada sin interrumpirla.")
    elif recording_active:
        aitum_vendor_request("stop_recording")
        obs.script_log(obs.LOG_INFO, "Grabación vertical detenida al cambiar de escena.")
        if auto_start_vertical_recording:
            time.sleep(0.5)
            aitum_vendor_request("start_recording")
            obs.script_log(obs.LOG_INFO, "Grabación vertical reiniciada por auto-start.")

def start_vertical_scene_update(horizontal_scene):
    """Ejecuta el control remoto de Aitum fuera del callback principal de OBS."""
    thread = threading.Thread(target=update_vertical_scene_controls, args=(horizontal_scene,))
    thread.daemon = True
    thread.start()

def ensure_folder(path):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        obs.script_log(obs.LOG_WARNING,
            "No se pudo crear carpeta '{}': {}".format(path, e))

def get_vertical_config_path():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    config_path = os.path.join(
        appdata, "obs-studio", "plugin_config", "vertical-canvas", "config.json"
    )
    return config_path if os.path.isfile(config_path) else None

def set_paths_for_scene(scene_name):
    global _current_recording_folder, _vertical_target_folder, _vertical_source_scene
    
    folder_name = clean_name_for_folder(scene_name)
    target = os.path.join(base_folder, folder_name)
    ensure_folder(target)
    
    config = obs.obs_frontend_get_profile_config()
    if config is None:
        obs.script_log(obs.LOG_WARNING, "No se pudo obtener el config del perfil.")
        return False
        
    obs.config_set_string(config, "SimpleOutput", "FilePath", target)
    obs.config_set_string(config, "AdvOut", "RecFilePath", target)
    obs.config_set_string(config, "AdvOut", "FFFilePath",  target)
    
    # Aplicar formato de Nvidia al nombre del archivo
    # Nvidia usa: "GameName Replay YYYY.MM.DD - HH.MM.SS.DVR"
    file_prefix = clean_name_for_filename(scene_name)
    if base_filename_format:
        new_format = file_prefix + " Replay " + base_filename_format
    else:
        new_format = file_prefix + " Replay %CCYY.%MM.%DD - %hh.%mm.%ss.DVR"
    
    obs.config_set_string(config, "Output", "FilenameFormatting", new_format)
    obs.config_save_safe(config, "tmp", None)
    _current_recording_folder = target
    _vertical_target_folder = target
    _vertical_source_scene = scene_name
    obs.script_log(obs.LOG_INFO, "Path actualizado -> '{}'".format(target))
    obs.script_log(obs.LOG_INFO, "Formato de nombre actualizado -> '{}'".format(new_format))
    return True

# ─── Limpieza de grabaciones en negro ─────────────────────────────────────────

_VIDEO_EXTS = ("*.mkv", "*.mp4", "*.flv", "*.mov", "*.ts", "*.avi", "*.fragmented.mov")

def _delete_empty_folder(folder):
    """Borra carpeta si está vacía (solo archivos ocultos del sistema)."""
    if not folder or not os.path.isdir(folder):
        return
    
    # No borrar la carpeta base del usuario
    if base_folder:
        try:
            if os.path.normpath(os.path.abspath(folder)) == os.path.normpath(os.path.abspath(base_folder)):
                print("[Cleanup] Carpeta base, no se borra: '{}'".format(folder))
                return
        except:
            pass
    
    try:
        items = os.listdir(folder)
        non_hidden = [i for i in items if not i.startswith('.') and i.lower() != 'thumbs.db']
        print("[Cleanup] Carpeta '{}' tiene {} items (no ocultos)".format(folder, len(non_hidden)))
        
        if not non_hidden:
            os.rmdir(folder)
            print("[Cleanup] ✓ Carpeta vacía eliminada: '{}'".format(folder))
        else:
            print("[Cleanup] Carpeta no vacía, items: {}".format(non_hidden))
    except Exception as e:
        print("[Cleanup] Error al borrar carpeta: {}".format(e))

def get_newest_video_file(folder):
    """Obtiene el archivo de video más recientemente modificado en la carpeta."""
    if not folder or not os.path.isdir(folder):
        return None
    candidates = []
    for ext in _VIDEO_EXTS:
        candidates.extend(glob.glob(os.path.join(folder, ext)))
    if not candidates:
        return None
    try:
        candidates.sort(key=os.path.getmtime, reverse=True)
        return candidates[0]
    except Exception as e:
        print("[Cleanup] Error al ordenar candidatos: {}".format(e))
        return None

def _async_cleanup_file(filepath, min_size, threshold):
    """Corre en un thread separado para evitar colgar la UI de OBS."""
    # Esperar 2 segundos para asegurar que OBS haya liberado el archivo por completo
    time.sleep(2.0)
    
    if not filepath or not os.path.exists(filepath):
        return

    # Si el archivo es menor que el tamaño mínimo configurado, se elimina directamente
    try:
        size = os.path.getsize(filepath)
        min_size_bytes = min_size * 1024 * 1024
        if size < min_size_bytes:
            print("[Cleanup] Video '{}' es demasiado pequeño ({:.2f} MB < {} MB). Se elimina inmediatamente.".format(
                os.path.basename(filepath), size / (1024 * 1024), min_size))
            folder_path = os.path.dirname(filepath)
            os.remove(filepath)
            _delete_empty_folder(folder_path)
            return
    except Exception as e:
        print("[Cleanup] Error al verificar tamaño de '{}': {}".format(filepath, e))
        return

    if not _has_opencv:
        print("[Cleanup] OpenCV no está disponible. No se puede realizar el análisis de negro.")
        return

    try:
        print("[Cleanup] Iniciando análisis nativo con OpenCV para '{}'...".format(os.path.basename(filepath)))
        
        cap = cv2.VideoCapture(filepath)
        if not cap.isOpened():
            print("[Cleanup] OpenCV no pudo abrir el archivo: '{}'".format(filepath))
            return

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            # Video sin frames, se considera malo
            cap.release()
            print("[Cleanup] Video sin frames. Se elimina.")
            folder_path = os.path.dirname(filepath)
            os.remove(filepath)
            _delete_empty_folder(folder_path)
            return

        # Muestrear primer frame, del medio y último frame
        frames_to_check = [0]
        if total_frames > 2:
            frames_to_check.append(total_frames // 2)
        if total_frames > 1:
            frames_to_check.append(total_frames - 1)

        black_frames = 0
        valid_samples = 0

        for f_idx in frames_to_check:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            valid_samples += 1
            # Calcular brillo medio del frame
            # frame es array HxWxC en BGR. Calcular media.
            mean_brightness = float(np.mean(frame))
            print("[Cleanup] Muestra frame {}: brillo medio {:.2f}".format(f_idx, mean_brightness))
            # Consideramos negro si el brillo medio es < 15 (sobre 255)
            if mean_brightness < 15.0:
                black_frames += 1

        cap.release()

        if valid_samples > 0:
            ratio_pct = (black_frames / valid_samples) * 100
            print("[Cleanup] OpenCV: {:.0f}% de los frames analizados son negros ({} de {})".format(
                ratio_pct, black_frames, valid_samples))

            if ratio_pct >= threshold:
                folder_path = os.path.dirname(filepath)
                os.remove(filepath)
                print("[Cleanup] ✓ Eliminado video negro ({:.0f}%): '{}'".format(ratio_pct, filepath))
                _delete_empty_folder(folder_path)
            else:
                print("[Cleanup] ✓ Video conservado ({:.0f}% < {}%)".format(ratio_pct, threshold))
        else:
            print("[Cleanup] No se pudieron decodificar frames del video: '{}'".format(filepath))

    except Exception as e:
        print("[Cleanup] Error analizando '{}' con OpenCV: {}".format(filepath, e))

def trigger_cleanup_for_file(filepath, cleanup_min_size=None, cleanup_threshold_value=None):
    """Inicia el análisis de limpieza en un thread de fondo daemon."""
    if not filepath or not os.path.exists(filepath):
        return
    cleanup_min_size = min_size_mb if cleanup_min_size is None else cleanup_min_size
    cleanup_threshold_value = cleanup_threshold if cleanup_threshold_value is None else cleanup_threshold_value
    thread = threading.Thread(
        target=_async_cleanup_file,
        args=(filepath, cleanup_min_size, cleanup_threshold_value)
    )
    thread.daemon = True
    thread.start()


# ─── Arranque diferido tras STOPPED ──────────────────────────────────────────
# OBS necesita tiempo tras STOPPED antes de aceptar un nuevo start().
# Usamos un timer de 1500ms como buffer de seguridad.

def _start_recording_after_stop():
    """Arranca la grabacion 1.5s despues de que OBS confirmo el STOPPED."""
    obs.timer_remove(_start_recording_after_stop)
    obs.script_log(obs.LOG_INFO, "Iniciando grabacion en nuevo path...")
    obs.obs_frontend_recording_start()

def _start_replay_after_stop():
    """Arranca el replay buffer 1.5s despues de que OBS confirmo el STOPPED."""
    obs.timer_remove(_start_replay_after_stop)
    obs.script_log(obs.LOG_INFO, "Iniciando replay buffer en nuevo path...")
    obs.obs_frontend_replay_buffer_start()


# ─── Logica diferida (500ms despues del cambio de escena) ────────────────────

def _deferred_restart():
    """
    Se ejecuta 500ms despues del ultimo cambio de escena.
    Para este momento OBS ya ha procesado completamente el cambio
    y la GUI esta estable.
    """
    global _pending_start_recording, _pending_start_replay

    obs.timer_remove(_deferred_restart)

    recording_active = obs.obs_frontend_recording_active()
    replay_active    = obs.obs_frontend_replay_buffer_active()

    # ── Grabacion ──────────────────────────────────────────────────────────────
    if _want_restart_recording:
        if recording_active:
            _pending_start_recording = True
            obs.script_log(obs.LOG_INFO,
                "Deteniendo grabacion (reiniciara en nuevo path)...")
            obs.obs_frontend_recording_stop()
        elif not _pending_start_recording:
            obs.script_log(obs.LOG_INFO, "Iniciando grabacion en nuevo path...")
            obs.obs_frontend_recording_start()

    # ── Replay buffer ──────────────────────────────────────────────────────────
    if _want_restart_replay:
        if replay_active:
            _pending_start_replay = True
            obs.script_log(obs.LOG_INFO,
                "Deteniendo replay buffer (reiniciara en nuevo path)...")
            obs.obs_frontend_replay_buffer_stop()
        elif not _pending_start_replay:
            obs.script_log(obs.LOG_INFO, "Iniciando replay buffer en nuevo path...")
            obs.obs_frontend_replay_buffer_start()


# ─── Evento de cambio de escena ───────────────────────────────────────────────

def handle_scene_changed():
    if not enabled:
        return
    """
    Al cambiar de escena:
      1. Obtiene el nombre de la escena activa de OBS.
      2. Actualiza el path en config inmediatamente.
      3. Captura intencion (quiere restart?).
      4. Difiere el stop/start 500ms para que OBS termine su cambio.
         Cambios rapidos: el timer se resetea, solo ejecuta una vez.
    """
    global _want_restart_recording, _want_restart_replay

    # Guard: carpeta base no configurada
    if not base_folder or not base_folder.strip():
        obs.script_log(obs.LOG_WARNING,
            "Set-Escene-Path: carpeta base no configurada. "
            "Ve a Herramientas > Scripts y configura la 'Carpeta base'.")
        return

    # Obtener nombre de la escena activa
    sc = obs.obs_frontend_get_current_scene()
    if sc is None:
        obs.script_log(obs.LOG_WARNING, "No se pudo obtener la escena actual.")
        return
    scene_name = obs.obs_source_get_name(sc)
    obs.obs_source_release(sc)

    folder_name = clean_name_for_folder(scene_name)

    # Log detallado para verificar nombre antes y despues del trim
    obs.script_log(obs.LOG_INFO, "=== Set-Escene-Path ===")
    obs.script_log(obs.LOG_INFO, "  Escena raw    : '{}'".format(scene_name))
    obs.script_log(obs.LOG_INFO, "  Carpeta final : '{}'".format(folder_name))
    obs.script_log(obs.LOG_INFO, "  Path completo : '{}'".format(
        os.path.join(base_folder, folder_name)))

    # Actualizar path y formato en config ahora (no afecta grabacion activa)
    set_paths_for_scene(scene_name)
    start_vertical_scene_update(scene_name)

    # Capturar estado actual
    recording_active = obs.obs_frontend_recording_active()
    replay_active    = obs.obs_frontend_replay_buffer_active()

    _want_restart_recording = auto_start_recording or (recording_active and not keep_recording)
    _want_restart_replay    = auto_start_replay    or (replay_active    and not keep_recording)

    if not _want_restart_recording and not _want_restart_replay:
        obs.script_log(obs.LOG_INFO,
            "Path guardado (sin interrumpir). Aplica en la proxima sesion.")
        return

    # Cancelar timer anterior y programar ejecucion diferida
    obs.timer_remove(_deferred_restart)
    obs.timer_add(_deferred_restart, 500)


# ─── Callback de eventos ──────────────────────────────────────────────────────

def on_event(event):
    global _pending_start_recording, _pending_start_replay
    global _active_recording_folder

    if event == obs.OBS_FRONTEND_EVENT_SCENE_CHANGED:
        handle_scene_changed()

    elif event == obs.OBS_FRONTEND_EVENT_RECORDING_STARTED:
        _active_recording_folder = _current_recording_folder
        print("[Cleanup] Grabación iniciada. Carpeta activa guardada: '{}'".format(_active_recording_folder))

    elif event == obs.OBS_FRONTEND_EVENT_RECORDING_STOPPED:
        if enable_cleanup and _active_recording_folder:
            newest_video = get_newest_video_file(_active_recording_folder)
            if newest_video:
                print("[Cleanup] Grabación finalizada en '{}'. Iniciando limpieza en background para '{}'".format(
                    _active_recording_folder, os.path.basename(newest_video)))
                trigger_cleanup_for_file(newest_video)
            _active_recording_folder = None

        if _pending_start_recording:
            _pending_start_recording = False
            obs.script_log(obs.LOG_INFO,
                "Grabacion cerrada. Arrancando en nuevo path en 1.5s...")
            obs.timer_remove(_start_recording_after_stop)
            obs.timer_add(_start_recording_after_stop, 1500)

    elif event == obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STOPPED:
        if _pending_start_replay:
            _pending_start_replay = False
            obs.script_log(obs.LOG_INFO,
                "Replay buffer cerrado. Arrancando en nuevo path en 1.5s...")
            obs.timer_remove(_start_replay_after_stop)
            obs.timer_add(_start_replay_after_stop, 1500)


# ─── Ciclo de vida del script ─────────────────────────────────────────────────

def script_load(settings):
    global _current_recording_folder, _vertical_event_listener_thread
    obs.obs_frontend_add_event_callback(on_event)
    if _vertical_event_listener_thread and _vertical_event_listener_thread.is_alive():
        return
    _vertical_event_listener_stop.clear()
    _vertical_event_listener_thread = threading.Thread(target=_vertical_event_listener)
    _vertical_event_listener_thread.daemon = True
    _vertical_event_listener_thread.start()
    
    # Intentar inicializar la carpeta actual al arrancar
    try:
        sc = obs.obs_frontend_get_current_scene()
        if sc:
            scene_name = obs.obs_source_get_name(sc)
            obs.obs_source_release(sc)
            folder_name = clean_name_for_folder(scene_name)
            if base_folder:
                _current_recording_folder = os.path.join(base_folder, folder_name)
    except:
        pass
        
    obs.script_log(obs.LOG_INFO, "Set-Escene-Path cargado.")

def script_unload():
    _vertical_event_listener_stop.set()
    if _vertical_event_listener_socket:
        _vertical_event_listener_socket.close()
    obs.timer_remove(_deferred_restart)
    obs.timer_remove(_start_recording_after_stop)
    obs.timer_remove(_start_replay_after_stop)
    obs.script_log(obs.LOG_INFO, "Set-Escene-Path descargado.")
