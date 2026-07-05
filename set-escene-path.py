import obspython as obs
import os
import re
import glob
import threading
import time

try:
    import cv2
    import numpy as np
    _has_opencv = True
except ImportError:
    _has_opencv = False

# ─── Config defaults ──────────────────────────────────────────────────────────
base_folder          = ""      # debe configurarse en el plugin antes de funcionar
base_filename_format = ""
keep_recording       = False
auto_start_recording = False
auto_start_replay    = False

# Limpieza de grabaciones "en negro"
enable_cleanup    = False
min_size_mb       = 25            # Tamaño mínimo en MB para conservar el vídeo (0-500)
cleanup_threshold = 90        # % mínimo de negro para eliminar (0-100)

# Intencion capturada en el momento del cambio de escena
_want_restart_recording = False
_want_restart_replay    = False

# Flags para arrancar TRAS recibir el evento STOPPED (stop es asincrono)
_pending_start_recording = False
_pending_start_replay    = False

# Tracking de carpeta activa para cleanup
_current_recording_folder = None
_active_recording_folder  = None


# ─── Script metadata ──────────────────────────────────────────────────────────

def script_description():
    return (
        "<b>Set-Escene-Path</b><br>"
        "Cambia el path de grabación y replay buffer según el <b>nombre de la escena</b> activa.<br><br>"
        "• <b>Mantener grabación:</b> no interrumpe, aplica en la próxima sesión.<br>"
        "• <b>Auto-start:</b> detiene y reinicia en el nuevo path automáticamente.<br>"
        "<i>El stream nunca se toca.</i>"
    )

def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_path(
        props, "base_folder", "Carpeta base",
        obs.OBS_PATH_DIRECTORY, "", None
    )
    obs.obs_properties_add_text(
        props, "base_filename_format",
        "Formato de nombre base (vacío = auto)",
        obs.OBS_TEXT_DEFAULT
    )
    obs.obs_properties_add_bool(
        props, "keep_recording",
        "Mantener grabación activa al cambiar escena"
    )
    obs.obs_properties_add_bool(
        props, "auto_start_recording",
        "Auto-start: iniciar grabación al cambiar escena"
    )
    obs.obs_properties_add_bool(
        props, "auto_start_replay",
        "Auto-start: iniciar replay buffer al cambiar escena"
    )

    # Sección limpieza
    obs.obs_properties_add_bool(
        props, "enable_cleanup",
        "🗑 Eliminar grabaciones 'en negro' / cortas (Requiere OpenCV)"
    )
    obs.obs_properties_add_int(
        props, "min_size_mb",
        "  Tamaño mín. para conservar (MB)",
        0, 500, 1
    )
    obs.obs_properties_add_int_slider(
        props, "cleanup_threshold",
        "  % negro para eliminar (si supera tamaño mín.)",
        50, 100, 1
    )

    return props

def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "base_folder", "")
    obs.obs_data_set_default_string(settings, "base_filename_format", "")
    obs.obs_data_set_default_bool(settings, "keep_recording",       False)
    obs.obs_data_set_default_bool(settings, "auto_start_recording", False)
    obs.obs_data_set_default_bool(settings, "auto_start_replay",    False)
    obs.obs_data_set_default_bool(settings, "enable_cleanup",       False)
    obs.obs_data_set_default_int(settings,  "min_size_mb",          25)
    obs.obs_data_set_default_int(settings,  "cleanup_threshold",    90)

def script_update(settings):
    global base_folder, keep_recording, auto_start_recording, auto_start_replay
    global enable_cleanup, min_size_mb, cleanup_threshold
    global _current_recording_folder, base_filename_format
    val = obs.obs_data_get_string(settings, "base_folder")
    base_folder          = val if val else ""
    keep_recording       = obs.obs_data_get_bool(settings, "keep_recording")
    auto_start_recording = obs.obs_data_get_bool(settings, "auto_start_recording")
    auto_start_replay    = obs.obs_data_get_bool(settings, "auto_start_replay")
    enable_cleanup       = obs.obs_data_get_bool(settings, "enable_cleanup")
    min_size_mb          = obs.obs_data_get_int(settings,  "min_size_mb")
    cleanup_threshold    = obs.obs_data_get_int(settings,  "cleanup_threshold")
    
    # Inicializar base_filename_format si no está establecido
    base_filename_format = obs.obs_data_get_string(settings, "base_filename_format")
    if not base_filename_format:
        config = obs.obs_frontend_get_profile_config()
        if config:
            val_format = obs.config_get_string(config, "Output", "FilenameFormatting")
            if val_format:
                base_filename_format = val_format
                obs.obs_data_set_string(settings, "base_filename_format", val_format)
                
    # Inicializar _current_recording_folder si no está establecido
    if not _current_recording_folder and base_folder:
        try:
            sc = obs.obs_frontend_get_current_scene()
            if sc:
                scene_name = obs.obs_source_get_name(sc)
                obs.obs_source_release(sc)
                folder_name = clean_name_for_folder(scene_name)
                _current_recording_folder = os.path.join(base_folder, folder_name)
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
    name = re.sub(r'[\u2122\u00ae\u00a9]', '', name)
    name = re.sub(r'\s*\((tm|r|c)\)\s*', ' ', name, flags=re.IGNORECASE)
    name = re.sub(r'[<>:"/\\|?*]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    # Quitar sufijo " escene" (con o sin espacio, case-insensitive)
    name = re.sub(r'\s*escene\s*$', '', name, flags=re.IGNORECASE).strip()
    return name if name else "unnamed"

def clean_name_for_filename(name):
    """
    Limpia el nombre de la escena para usarlo como prefijo de archivo:
    - Quita marcas registradas y caracteres especiales.
    - Reemplaza espacios y otros caracteres no válidos por '_'.
    - Quita el sufijo ' escene' / ' escene' (case insensitive).
    - Une guiones bajos consecutivos en uno solo.
    """
    name = re.sub(r'[\u2122\u00ae\u00a9]', '', name)
    name = re.sub(r'\s*\((tm|r|c)\)\s*', '_', name, flags=re.IGNORECASE)
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Reemplazar cualquier espacio (incluyendo múltiples) por '_'
    name = re.sub(r'\s+', '_', name)
    # Quitar sufijo " escene"
    name = re.sub(r'_*escene_*$', '', name, flags=re.IGNORECASE)
    # Reemplazar múltiples guiones bajos consecutivos por uno solo
    name = re.sub(r'_+', '_', name)
    return name.strip('_')

def ensure_folder(path):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        obs.script_log(obs.LOG_WARNING,
            "No se pudo crear carpeta '{}': {}".format(path, e))

def set_paths_for_scene(scene_name):
    global _current_recording_folder
    
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
    
    # Actualizar el formato de nombre de archivo con el prefijo de la escena
    prefix = clean_name_for_filename(scene_name)
    if prefix:
        new_format = "{}_{}".format(prefix, base_filename_format)
    else:
        new_format = base_filename_format
        
    obs.config_set_string(config, "Output", "FilenameFormatting", new_format)
    obs.config_save_safe(config, "tmp", None)
    
    _current_recording_folder = target
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
            mean_brightness = np.mean(frame)
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

def trigger_cleanup_for_file(filepath):
    """Inicia el análisis de limpieza en un thread de fondo daemon."""
    if not filepath or not os.path.exists(filepath):
        return
    thread = threading.Thread(target=_async_cleanup_file, args=(filepath, min_size_mb, cleanup_threshold))
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
    global _current_recording_folder
    obs.obs_frontend_add_event_callback(on_event)
    
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
    obs.timer_remove(_deferred_restart)
    obs.timer_remove(_start_recording_after_stop)
    obs.timer_remove(_start_replay_after_stop)
    obs.script_log(obs.LOG_INFO, "Set-Escene-Path descargado.")
