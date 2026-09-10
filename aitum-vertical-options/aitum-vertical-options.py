import base64
import difflib
import glob
import json
import os
import re
import shutil
import socket
import struct
import tempfile
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
_listener_thread = None
_listener_stop = threading.Event()
_listener_socket = None
_move_lock = threading.Lock()
_processed_files = set()
_move_lock_path = os.path.join(tempfile.gettempdir(), "aitum-vertical-options.lock")

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
        obs.script_log(obs.LOG_WARNING, "Error al exportar ajustes verticales: {}".format(error))
    return True


def on_import_settings(props, prop):
    path = os.path.join(_settings_directory(), "aitum-vertical-options.json")
    if not os.path.isfile(path):
        obs.script_log(obs.LOG_WARNING, "No se encontró el archivo: {}".format(path))
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
        obs.script_log(obs.LOG_WARNING, "Error al importar ajustes verticales: {}".format(error))
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


def _scene_key(name):
    words = re.findall(r"[a-z0-9]+", name.lower())
    words = [word for word in words if word not in ("escene", "scene", "vertical", "horizontal")]
    if words and words[0] in _SCENE_ALIASES:
        words = _SCENE_ALIASES[words[0]].split() + words[1:]
    return tuple(words[:2])


def _best_vertical_scene(horizontal_name, candidates):
    key = _scene_key(horizontal_name)
    matches = [name for name in candidates if _scene_key(name) == key]
    if not matches:
        return None
    return max(matches, key=lambda name: difflib.SequenceMatcher(None, horizontal_name.lower(), name.lower()).ratio())


def _read_frame(sock):
    def receive(size):
        data = b""
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    header = receive(2)
    if not header:
        return None
    first, second = header
    length = second & 0x7F
    if length == 126:
        length = struct.unpack(">H", receive(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", receive(8))[0]
    if second & 0x80:
        receive(4)
    payload = receive(length)
    return (first & 0x0F, payload) if payload is not None else None


def _send_text(sock, text):
    payload = text.encode("utf-8")
    mask = os.urandom(4)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    length = len(masked)
    if length < 126:
        header = bytes([0x81, 0x80 | length])
    elif length < 65536:
        header = bytes([0x81, 0x80 | 126]) + struct.pack(">H", length)
    else:
        header = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", length)
    sock.sendall(header + mask + masked)


def _vendor_request(request_type, request_data=None):
    request_id = str(uuid.uuid4())
    request = {"op": 6, "d": {"requestType": "CallVendorRequest", "requestId": request_id,
        "requestData": {"vendorName": "aitum-vertical-canvas", "requestType": request_type,
                         "requestData": request_data or {}}}}
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect(("127.0.0.1", 4455))
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = ("GET / HTTP/1.1\r\nHost: 127.0.0.1:4455\r\nUpgrade: websocket\r\n"
                     "Connection: Upgrade\r\nSec-WebSocket-Key: {}\r\nSec-WebSocket-Version: 13\r\n\r\n").format(key)
        sock.sendall(handshake.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            response += sock.recv(1)
        hello = _read_frame(sock)
        if not hello or json.loads(hello[1].decode("utf-8")).get("op") != 0:
            raise RuntimeError("handshake de OBS WebSocket inválido")
        _send_text(sock, json.dumps({"op": 1, "d": {"rpcVersion": 1}}, separators=(",", ":")))
        while True:
            frame = _read_frame(sock)
            if not frame:
                raise RuntimeError("OBS WebSocket cerró la conexión")
            if frame[0] in (8, 9, 10):
                continue
            if json.loads(frame[1].decode("utf-8")).get("op") == 2:
                break
        _send_text(sock, json.dumps(request, separators=(",", ":")))
        while True:
            frame = _read_frame(sock)
            if not frame:
                return None
            if frame[0] in (8, 9, 10):
                continue
            message = json.loads(frame[1].decode("utf-8"))
            if message.get("op") == 7 and message.get("d", {}).get("requestId") == request_id:
                response_data = message.get("d", {}).get("responseData", {})
                return response_data.get("responseData", response_data)
    except Exception as error:
        obs.script_log(obs.LOG_INFO, "Aitum Vertical no disponible: {}".format(error))
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
    import re
    name = re.sub(r"[<>:\"/\\|?*]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "unnamed"


def _update_target_folder(scene_name):
    global _vertical_target_folder
    if base_folder:
        _vertical_target_folder = os.path.join(base_folder, _clean_name(scene_name))
        os.makedirs(_vertical_target_folder, exist_ok=True)


def _vertical_config_path():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    path = os.path.join(appdata, "obs-studio", "plugin_config", "vertical-canvas", "config.json")
    return path if os.path.isfile(path) else None


def _find_vertical_file(event_type, minimum_time):
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
        for folder in folders:
            for item in glob.glob(os.path.join(folder, "*")):
                if (os.path.isfile(item) and item.lower().endswith((".mkv", ".mp4", ".mov", ".ts", ".flv"))
                        and (event_type != "backtrack_saved" or "backtrack" in os.path.basename(item).lower())
                        and os.path.getmtime(item) >= minimum_time - 2):
                    candidates.append(item)
        return max(candidates, key=os.path.getmtime) if candidates else None
    except Exception as error:
        obs.script_log(obs.LOG_WARNING, "No se pudo localizar archivo vertical: {}".format(error))
        return None


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
        obs.script_log(obs.LOG_WARNING, "Error limpiando archivo vertical: {}".format(error))


def _organize_file(event_type, event_time):
    source = None
    for _ in range(12):
        time.sleep(0.5)
        source = _find_vertical_file(event_type, event_time)
        if source:
            break
    if not source:
        return
    target = _vertical_target_folder
    if move_vertical_files and target:
        try:
            with _move_lock:
                if source in _processed_files:
                    return
                os.makedirs(target, exist_ok=True)
                destination = os.path.join(target, os.path.basename(source))
                shutil.move(source, destination)
                _processed_files.add(source)
                source = destination
        except Exception as error:
            obs.script_log(obs.LOG_WARNING, "No se pudo mover archivo vertical: {}".format(error))
    if enable_vertical_cleanup:
        threading.Thread(target=_cleanup_file, args=(source,), daemon=True).start()


def _event_listener():
    global _listener_socket
    while not _listener_stop.is_set():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _listener_socket = sock
        sock.settimeout(1)
        try:
            sock.connect(("127.0.0.1", 4455))
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            sock.sendall(("GET / HTTP/1.1\r\nHost: 127.0.0.1:4455\r\nUpgrade: websocket\r\n"
                          "Connection: Upgrade\r\nSec-WebSocket-Key: {}\r\nSec-WebSocket-Version: 13\r\n\r\n").format(key).encode("ascii"))
            while b"\r\n\r\n" not in sock.recv(4096):
                pass
            _read_frame(sock)
            _send_text(sock, json.dumps({"op": 1, "d": {"rpcVersion": 1, "eventSubscriptions": 576}}))
            _read_frame(sock)
            while not _listener_stop.is_set():
                frame = _read_frame(sock)
                if not frame or frame[0] in (8, 9, 10):
                    continue
                message = json.loads(frame[1].decode("utf-8"))
                if message.get("op") != 5:
                    continue
                event = message.get("d", {}).get("eventData", {})
                if (message.get("d", {}).get("eventType") == "VendorEvent"
                        and event.get("vendorName") == "aitum-vertical-canvas"
                        and event.get("eventType") in ("backtrack_saved", "recording_stopped")):
                    threading.Thread(target=_organize_file, args=(event["eventType"], time.time()), daemon=True).start()
        except Exception:
            pass
        finally:
            sock.close()
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
        _listener_socket.close()
    obs.script_log(obs.LOG_INFO, "Aitum Vertical Options descargado.")