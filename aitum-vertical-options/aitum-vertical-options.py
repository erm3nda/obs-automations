import base64
import difflib
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import threading
import time
import uuid

import obspython as obs

try:
    import cv2
    import numpy as np
    _has_opencv = True
except ImportError:
    _has_opencv = False


enabled = True
base_folder = ""
sync_scene = True
move_vertical_files = False
keep_vertical_recording = False
auto_start_vertical_recording = False
auto_start_vertical_backtrack = False
enable_vertical_cleanup = False
vertical_min_size_mb = 25
vertical_cleanup_threshold = 90

_script_settings = None
_vertical_target_folder = None
_current_scene_name = ""
_listener_thread = None
_listener_stop = threading.Event()
_listener_socket = None
_move_lock = threading.Lock()
_processed_files = set()

_SCENE_ALIASES = {
    "bfv": "battlefield v",
    "bf6": "battlefield 6",
    "fv5": "battlefield v",
}


def script_description():
    return (
        "Gestiona las escenas y archivos de Aitum Vertical. Los auto-inicios de "
        "grabación y backtrack son opcionales y están desactivados por defecto."
    )


def _settings_directory():
    path = obs.obs_data_get_string(_script_settings, "settings_path") if _script_settings else ""
    return path or os.path.join(os.path.expanduser("~"), "Desktop")


def on_export_settings(props, prop):
    path = os.path.join(_settings_directory(), "aitum-vertical-options.json")
    data = {
        "enabled": enabled,
        "base_folder": base_folder,
        "sync_scene": sync_scene,
        "move_vertical_files": move_vertical_files,
        "keep_vertical_recording": keep_vertical_recording,
        "auto_start_vertical_recording": auto_start_vertical_recording,
        "auto_start_vertical_backtrack": auto_start_vertical_backtrack,
        "enable_vertical_cleanup": enable_vertical_cleanup,
        "vertical_min_size_mb": vertical_min_size_mb,
        "vertical_cleanup_threshold": vertical_cleanup_threshold,
    }
    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4, ensure_ascii=False)
        obs.script_log(obs.LOG_INFO, "Ajustes de Aitum Vertical exportados: {}".format(path))
    except Exception as error:
        obs.script_log(obs.LOG_INFO, "[ERROR] Error al exportar ajustes verticales: {}".format(error))
    return True


def on_import_settings(props, prop):
    path = os.path.join(_settings_directory(), "aitum-vertical-options.json")
    if not os.path.isfile(path):
        obs.script_log(obs.LOG_INFO, "[ERROR] No se encontró el archivo: {}".format(path))
        return True
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        target = _script_settings or obs.obs_data_create()
        obs.obs_data_set_bool(target, "enabled", data.get("enabled", True))
        obs.obs_data_set_string(target, "base_folder", data.get("base_folder", ""))
        obs.obs_data_set_bool(target, "sync_scene", data.get("sync_scene", True))
        obs.obs_data_set_bool(target, "move_vertical_files", data.get("move_vertical_files", False))
        obs.obs_data_set_bool(target, "keep_vertical_recording", data.get("keep_vertical_recording", False))
        obs.obs_data_set_bool(target, "auto_start_vertical_recording", data.get("auto_start_vertical_recording", False))
        obs.obs_data_set_bool(target, "auto_start_vertical_backtrack", data.get("auto_start_vertical_backtrack", False))
        obs.obs_data_set_bool(target, "enable_vertical_cleanup", data.get("enable_vertical_cleanup", False))
        obs.obs_data_set_int(target, "vertical_min_size_mb", data.get("vertical_min_size_mb", 25))
        obs.obs_data_set_int(target, "vertical_cleanup_threshold", data.get("vertical_cleanup_threshold", 90))
        script_update(target)
        if props:
            obs.obs_properties_apply_settings(props, target)
        if not _script_settings:
            obs.obs_data_release(target)
        obs.script_log(obs.LOG_INFO, "Ajustes de Aitum Vertical importados.")
    except Exception as error:
        obs.script_log(obs.LOG_INFO, "[ERROR] Error al importar ajustes verticales: {}".format(error))
    return True


def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_bool(props, "enabled", "Plugin activo")
    obs.obs_properties_add_path(
        props, "base_folder", "Carpeta base para archivos verticales",
        obs.OBS_PATH_DIRECTORY, "", None
    )
    obs.obs_properties_add_bool(props, "sync_scene", "Sincronizar escena")
    obs.obs_properties_add_bool(props, "move_vertical_files", "Mover archivos automáticamente")
    obs.obs_properties_add_bool(props, "keep_vertical_recording", "Mantener grabación vertical activa")
    obs.obs_properties_add_bool(props, "auto_start_vertical_recording", "Auto-iniciar grabación vertical")
    obs.obs_properties_add_bool(props, "auto_start_vertical_backtrack", "Auto-iniciar backtrack vertical")
    obs.obs_properties_add_text(
        props, "vertical_warning",
        "ADVERTENCIA: cerrar OBS con backtrack vertical activo puede provocar un crash. Se recomienda no activar las opciones de auto-inicio.",
        obs.OBS_TEXT_INFO
    )
    obs.obs_properties_add_bool(props, "enable_vertical_cleanup", "Limpiar archivos en negro")
    obs.obs_properties_add_int(props, "vertical_min_size_mb", "Tamaño mínimo (MB)", 0, 500, 1)
    obs.obs_properties_add_int_slider(
        props, "vertical_cleanup_threshold", "% de negro para eliminar", 50, 100, 1
    )

    management = obs.obs_properties_create()
    obs.obs_properties_add_path(
        management, "settings_path", "Carpeta donde guardar ajustes",
        obs.OBS_PATH_DIRECTORY, "", os.path.join(os.path.expanduser("~"), "Desktop")
    )
    obs.obs_properties_add_button(management, "export_btn", "Guardar ajustes", on_export_settings)
    obs.obs_properties_add_button(management, "import_btn", "Cargar ajustes", on_import_settings)
    obs.obs_properties_add_group(props, "settings_mng", "Gestión de ajustes", obs.OBS_GROUP_NORMAL, management)
    return props


def script_defaults(settings):
    obs.obs_data_set_default_bool(settings, "enabled", True)
    obs.obs_data_set_default_string(settings, "base_folder", "")
    obs.obs_data_set_default_bool(settings, "sync_scene", True)
    obs.obs_data_set_default_bool(settings, "move_vertical_files", False)
    obs.obs_data_set_default_bool(settings, "keep_vertical_recording", False)
    obs.obs_data_set_default_bool(settings, "auto_start_vertical_recording", False)
    obs.obs_data_set_default_bool(settings, "auto_start_vertical_backtrack", False)
    obs.obs_data_set_default_bool(settings, "enable_vertical_cleanup", False)
    obs.obs_data_set_default_int(settings, "vertical_min_size_mb", 25)
    obs.obs_data_set_default_int(settings, "vertical_cleanup_threshold", 90)


def script_update(settings):
    global _script_settings, enabled, base_folder, sync_scene
    global move_vertical_files, keep_vertical_recording
    global auto_start_vertical_recording, auto_start_vertical_backtrack
    global enable_vertical_cleanup
    global vertical_min_size_mb, vertical_cleanup_threshold
    _script_settings = settings
    enabled = obs.obs_data_get_bool(settings, "enabled")
    base_folder = obs.obs_data_get_string(settings, "base_folder").strip()
    sync_scene = obs.obs_data_get_bool(settings, "sync_scene")
    move_vertical_files = obs.obs_data_get_bool(settings, "move_vertical_files")
    keep_vertical_recording = obs.obs_data_get_bool(settings, "keep_vertical_recording")
    auto_start_vertical_recording = obs.obs_data_get_bool(settings, "auto_start_vertical_recording")
    auto_start_vertical_backtrack = obs.obs_data_get_bool(settings, "auto_start_vertical_backtrack")
    enable_vertical_cleanup = obs.obs_data_get_bool(settings, "enable_vertical_cleanup")
    vertical_min_size_mb = obs.obs_data_get_int(settings, "vertical_min_size_mb")
    vertical_cleanup_threshold = obs.obs_data_get_int(settings, "vertical_cleanup_threshold")
    _refresh_target_folder_for_current_scene()


def _scene_key(name):
    words = re.findall(r"[a-z0-9]+", name.lower())
    words = [word for word in words if word not in (
        "escene", "escena", "scene", "vertical", "horizontal"
    )]
    if words and words[0] in _SCENE_ALIASES:
        words = _SCENE_ALIASES[words[0]].split() + words[1:]
    return tuple(words)


def _best_vertical_scene(horizontal_name, candidates):
    key = _scene_key(horizontal_name)
    if not key:
        return None

    keyed_candidates = [(name, _scene_key(name)) for name in candidates]
    keyed_candidates = [(name, candidate_key) for name, candidate_key in keyed_candidates if candidate_key]
    exact_matches = [name for name, candidate_key in keyed_candidates if candidate_key == key]
    if exact_matches:
        return exact_matches[0]
    if not keyed_candidates:
        return None

    normalized_name = " ".join(key)
    return max(
        keyed_candidates,
        key=lambda item: difflib.SequenceMatcher(None, normalized_name, " ".join(item[1])).ratio()
    )[0]


def _receive_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def _read_frame(sock):
    header = _receive_exact(sock, 2)
    if not header:
        return None
    first, second = header
    length = second & 0x7F
    if length == 126:
        extended_length = _receive_exact(sock, 2)
        if extended_length is None:
            return None
        length = struct.unpack(">H", extended_length)[0]
    elif length == 127:
        extended_length = _receive_exact(sock, 8)
        if extended_length is None:
            return None
        length = struct.unpack(">Q", extended_length)[0]
    if length > 16 * 1024 * 1024:
        raise ValueError("trama WebSocket demasiado grande")
    mask = None
    if second & 0x80:
        mask = _receive_exact(sock, 4)
        if mask is None:
            return None
    payload = _receive_exact(sock, length)
    if payload is None:
        return None
    if mask:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return (first & 0x0F, payload)


def _send_frame(sock, opcode, payload):
    mask = os.urandom(4)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    length = len(masked)
    if length < 126:
        header = bytes([0x80 | opcode, 0x80 | length])
    elif length < 65536:
        header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", length)
    else:
        header = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack(">Q", length)
    sock.sendall(header + mask + masked)


def _send_text(sock, text):
    _send_frame(sock, 1, text.encode("utf-8"))


def _read_json_message(sock):
    while True:
        frame = _read_frame(sock)
        if frame is None or frame[0] == 8:
            return None
        if frame[0] == 9:
            _send_frame(sock, 10, frame[1])
            continue
        if frame[0] != 1:
            continue
        return json.loads(frame[1].decode("utf-8"))


def _websocket_handshake(sock, event_subscriptions=None):
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET / HTTP/1.1\r\nHost: 127.0.0.1:4455\r\nUpgrade: websocket\r\n"
        "Connection: Upgrade\r\nSec-WebSocket-Key: {}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    ).format(key)
    sock.sendall(request.encode("ascii"))

    response = bytearray()
    while not response.endswith(b"\r\n\r\n"):
        chunk = sock.recv(1)
        if not chunk:
            raise RuntimeError("OBS WebSocket cerró la conexión durante el handshake")
        response.extend(chunk)
        if len(response) > 65536:
            raise RuntimeError("cabeceras de OBS WebSocket demasiado grandes")

    lines = response.decode("iso-8859-1").split("\r\n")
    if not lines[0].startswith("HTTP/1.1 101 "):
        raise RuntimeError("OBS rechazó el handshake WebSocket: {}".format(lines[0]))
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    expected_accept = base64.b64encode(hashlib.sha1(
        (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
    ).digest()).decode("ascii")
    if headers.get("sec-websocket-accept") != expected_accept:
        raise RuntimeError("respuesta Sec-WebSocket-Accept inválida")

    hello = _read_json_message(sock)
    if not hello or hello.get("op") != 0:
        raise RuntimeError("handshake de OBS WebSocket inválido")
    identify = {"rpcVersion": 1}
    if event_subscriptions is not None:
        identify["eventSubscriptions"] = event_subscriptions
    _send_text(sock, json.dumps({"op": 1, "d": identify}, separators=(",", ":")))

    while True:
        message = _read_json_message(sock)
        if not message:
            raise RuntimeError("OBS WebSocket cerró la conexión durante la identificación")
        if message.get("op") == 2:
            return


def _vendor_request(request_type, request_data=None):
    request_id = str(uuid.uuid4())
    request = {"op": 6, "d": {"requestType": "CallVendorRequest", "requestId": request_id,
        "requestData": {"vendorName": "aitum-vertical-canvas", "requestType": request_type,
                         "requestData": request_data or {}}}}
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect(("127.0.0.1", 4455))
        _websocket_handshake(sock)
        _send_text(sock, json.dumps(request, separators=(",", ":")))
        while True:
            message = _read_json_message(sock)
            if not message:
                return None
            if message.get("op") == 7 and message.get("d", {}).get("requestId") == request_id:
                response_data = message.get("d", {}).get("responseData", {})
                return response_data.get("responseData", response_data)
    except Exception as error:
        obs.script_log(obs.LOG_INFO, "[ERROR] Aitum Vertical no disponible: {}".format(error))
        return None
    finally:
        sock.close()


def _switch_vertical_scene(horizontal_name):
    response = _vendor_request("get_scenes") or {}
    names = [item.get("name", "") for item in response.get("scenes", [])]
    vertical_name = _best_vertical_scene(horizontal_name, names)
    if vertical_name:
        _vendor_request("switch_scene", {"scene": vertical_name})
        obs.script_log(obs.LOG_INFO, "Escena vertical sincronizada: {}".format(vertical_name))
        _update_vertical_recording_controls()
    else:
        obs.script_log(obs.LOG_INFO, "No hay escena vertical coincidente para: {}".format(horizontal_name))


def _update_vertical_recording_controls():
    if auto_start_vertical_backtrack:
        _vendor_request("stop_backtrack")
        time.sleep(0.5)
        _vendor_request("start_backtrack")
        obs.script_log(obs.LOG_WARNING, "Backtrack vertical reiniciado automáticamente.")

    if auto_start_vertical_recording and not keep_vertical_recording:
        status = _vendor_request("status") or {}
        if not status.get("recording"):
            _vendor_request("start_recording")
            obs.script_log(obs.LOG_WARNING, "Grabación vertical iniciada automáticamente.")


def _clean_name(name):
    name = re.sub(r"[<>:\"/\\|?*]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "unnamed"


def _update_target_folder(scene_name):
    global _vertical_target_folder, _current_scene_name
    _current_scene_name = scene_name
    _vertical_target_folder = None
    if base_folder:
        _vertical_target_folder = os.path.join(base_folder, _clean_name(scene_name))
        os.makedirs(_vertical_target_folder, exist_ok=True)


def _refresh_target_folder_for_current_scene():
    scene = obs.obs_frontend_get_current_scene()
    if not scene:
        _update_target_folder("")
        return
    try:
        _update_target_folder(obs.obs_source_get_name(scene))
    finally:
        obs.obs_source_release(scene)


def _vertical_config_path():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    path = os.path.join(appdata, "obs-studio", "plugin_config", "vertical-canvas", "config.json")
    return path if os.path.isfile(path) else None


def _find_vertical_file(event_type, minimum_time, scene_name):
    config_path = _vertical_config_path()
    if not config_path:
        return None
    try:
        with open(config_path, "r", encoding="utf-8-sig") as file:
            data = json.load(file)
        key = "backtrack_path" if event_type == "backtrack_saved" else "record_path"
        folders = {canvas.get(key, "") for canvas in data.get("canvas", [])}
        folders.discard("")
        candidates = []
        scene_key = _scene_key(scene_name)
        for folder in folders:
            for item in glob.glob(os.path.join(folder, "*")):
                filename = os.path.basename(item).lower()
                is_backtrack = "backtrack" in filename
                if (os.path.isfile(item) and item.lower().endswith((".mkv", ".mp4", ".mov", ".ts", ".flv"))
                        and (is_backtrack if event_type == "backtrack_saved" else not is_backtrack)
                        and os.path.getmtime(item) >= minimum_time - 2
                        and scene_key and _contains_scene_key(filename, scene_key)):
                    candidates.append(item)
        return max(candidates, key=os.path.getmtime) if candidates else None
    except Exception as error:
        obs.script_log(obs.LOG_INFO, "[ERROR] No se pudo localizar archivo vertical: {}".format(error))
        return None


def _contains_scene_key(filename, scene_key):
    words = re.findall(r"[a-z0-9]+", filename.lower())
    words = [word for word in words if word not in ("backtrack", "replay")]
    filename_key = _scene_key(" ".join(words))
    width = len(scene_key)
    return any(filename_key[index:index + width] == scene_key
               for index in range(len(filename_key) - width + 1))


def _cleanup_file(path):
    time.sleep(2)
    if not os.path.isfile(path):
        return
    try:
        if os.path.getsize(path) < vertical_min_size_mb * 1024 * 1024:
            os.remove(path)
            return
        if not _has_opencv:
            return
        capture = cv2.VideoCapture(path)
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        samples = [0, total // 2, max(0, total - 1)] if total else []
        black = valid = 0
        for index in set(samples):
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if ok and frame is not None:
                valid += 1
                black += float(np.mean(frame)) < 15
        capture.release()
        if valid and (black / valid) * 100 >= vertical_cleanup_threshold:
            os.remove(path)
    except Exception as error:
        obs.script_log(obs.LOG_INFO, "[ERROR] Error limpiando archivo vertical: {}".format(error))


def _wait_for_stable_file(event_type, event_time, scene_name):
    previous_state = None
    for _ in range(12):
        candidate = _find_vertical_file(event_type, event_time, scene_name)
        if candidate:
            try:
                stat = os.stat(candidate)
                state = (candidate, stat.st_size, stat.st_mtime_ns)
            except OSError:
                state = None
            if state and state == previous_state:
                return candidate
            previous_state = state
        else:
            previous_state = None
        time.sleep(0.5)
    return None


def _organize_file(event_type, event_time, target_folder, scene_name):
    if not move_vertical_files and not enable_vertical_cleanup:
        return
    source = _wait_for_stable_file(event_type, event_time, scene_name)
    if not source:
        obs.script_log(
            obs.LOG_INFO,
            "[ERROR] No se encontró un archivo vertical reciente que coincida con la escena activa."
        )
        return
    if move_vertical_files and target_folder:
        destination = os.path.join(target_folder, os.path.basename(source))
        for attempt in range(12):
            try:
                with _move_lock:
                    if source in _processed_files:
                        return
                    if not os.path.isfile(source):
                        return
                    os.makedirs(target_folder, exist_ok=True)
                    shutil.move(source, destination)
                    _processed_files.add(source)
                    source = destination
                break
            except PermissionError as error:
                if attempt == 11:
                    obs.script_log(obs.LOG_INFO, "[ERROR] No se pudo mover archivo vertical: {}".format(error))
                    return
                time.sleep(0.5)
            except Exception as error:
                obs.script_log(obs.LOG_INFO, "[ERROR] No se pudo mover archivo vertical: {}".format(error))
                return
    if enable_vertical_cleanup:
        threading.Thread(target=_cleanup_file, args=(source,), daemon=True).start()


def _event_listener():
    global _listener_socket
    connection_error_logged = False
    while not _listener_stop.is_set():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _listener_socket = sock
        sock.settimeout(5)
        try:
            sock.connect(("127.0.0.1", 4455))
            _websocket_handshake(sock, 576)
            sock.settimeout(None)
            connection_error_logged = False
            while not _listener_stop.is_set():
                message = _read_json_message(sock)
                if message is None:
                    break
                if message.get("op") != 5:
                    continue
                event = message.get("d", {}).get("eventData", {})
                if (message.get("d", {}).get("eventType") == "VendorEvent"
                        and event.get("vendorName") == "aitum-vertical-canvas"
                        and event.get("eventType") in ("backtrack_saved", "recording_stopped")):
                    threading.Thread(
                        target=_organize_file,
                        args=(
                            event["eventType"],
                            time.time(),
                            _vertical_target_folder,
                            _current_scene_name,
                        ),
                        daemon=True
                    ).start()
        except Exception as error:
            if not _listener_stop.is_set() and not connection_error_logged:
                obs.script_log(obs.LOG_INFO, "[ERROR] Listener de Aitum Vertical: {}".format(error))
                connection_error_logged = True
        finally:
            try:
                sock.close()
            finally:
                if _listener_socket is sock:
                    _listener_socket = None
        _listener_stop.wait(3)


def on_frontend_event(event):
    if not enabled or event != obs.OBS_FRONTEND_EVENT_SCENE_CHANGED:
        return
    scene = obs.obs_frontend_get_current_scene()
    if not scene:
        return
    name = obs.obs_source_get_name(scene)
    obs.obs_source_release(scene)
    _update_target_folder(name)
    if sync_scene:
        threading.Thread(target=_switch_vertical_scene, args=(name,), daemon=True).start()


def script_load(settings):
    global _script_settings, _listener_thread
    _script_settings = settings
    script_update(settings)
    obs.obs_frontend_add_event_callback(on_frontend_event)
    _listener_stop.clear()
    _listener_thread = threading.Thread(target=_event_listener, daemon=True)
    _listener_thread.start()
    if auto_start_vertical_backtrack or auto_start_vertical_recording:
        obs.script_log(
            obs.LOG_WARNING,
            "Aitum Vertical Options: las opciones de auto-inicio están activas. "
            "Cerrar OBS con backtrack activo puede provocar un crash; se recomienda desactivarlas."
        )
    else:
        obs.script_log(obs.LOG_INFO, "Aitum Vertical Options cargado con auto-inicios desactivados.")


def script_unload():
    _listener_stop.set()
    if _listener_socket:
        try:
            _listener_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        _listener_socket.close()
    obs.script_log(obs.LOG_INFO, "Aitum Vertical Options descargado.")