import obspython as obs
import os
import re
import glob
import subprocess

# ─── Config defaults ──────────────────────────────────────────────────────────
base_folder          = ""      # debe configurarse en el plugin antes de funcionar
keep_recording       = False
auto_start_recording = False
auto_start_replay    = False

# Limpieza de grabaciones "en negro"
enable_cleanup    = False
cleanup_threshold = 90        # % mínimo de negro para eliminar (0-100)
ffmpeg_exe        = "ffmpeg"  # ruta a ffmpeg o "ffmpeg" si está en PATH

# Intencion capturada en el momento del cambio de escena
_want_restart_recording = False
_want_restart_replay    = False

# Flags para arrancar TRAS recibir el evento STOPPED (stop es asincrono)
_pending_start_recording = False
_pending_start_replay    = False

# Tracking de carpeta activa para cleanup
_current_recording_folder = None


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
        "🗑 Eliminar grabaciones 'en negro'"
    )
    obs.obs_properties_add_int_slider(
        props, "cleanup_threshold",
        "  % negro para eliminar",
        50, 100, 1
    )
    obs.obs_properties_add_text(
        props, "ffmpeg_exe",
        "  Ruta FFmpeg (vacío = PATH)",
        obs.OBS_TEXT_DEFAULT
    )

    return props

def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "base_folder", "")
    obs.obs_data_set_default_bool(settings, "keep_recording",       False)
    obs.obs_data_set_default_bool(settings, "auto_start_recording", False)
    obs.obs_data_set_default_bool(settings, "auto_start_replay",    False)
    obs.obs_data_set_default_bool(settings, "enable_cleanup",       False)
    obs.obs_data_set_default_int(settings,  "cleanup_threshold",    90)
    obs.obs_data_set_default_string(settings, "ffmpeg_exe",         "ffmpeg")

def script_update(settings):
    global base_folder, keep_recording, auto_start_recording, auto_start_replay
    global enable_cleanup, cleanup_threshold, ffmpeg_exe
    val = obs.obs_data_get_string(settings, "base_folder")
    base_folder          = val if val else ""
    keep_recording       = obs.obs_data_get_bool(settings, "keep_recording")
    auto_start_recording = obs.obs_data_get_bool(settings, "auto_start_recording")
    auto_start_replay    = obs.obs_data_get_bool(settings, "auto_start_replay")
    enable_cleanup       = obs.obs_data_get_bool(settings, "enable_cleanup")
    cleanup_threshold    = obs.obs_data_get_int(settings,  "cleanup_threshold")
    val2 = obs.obs_data_get_string(settings, "ffmpeg_exe")
    ffmpeg_exe           = val2 if val2.strip() else "ffmpeg"


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

def ensure_folder(path):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        obs.script_log(obs.LOG_WARNING,
            "No se pudo crear carpeta '{}': {}".format(path, e))

def apply_path_to_config(path):
    """Escribe el path en el perfil activo de OBS (modo Simple y Avanzado)."""
    config = obs.obs_frontend_get_profile_config()
    if config is None:
        obs.script_log(obs.LOG_WARNING, "No se pudo obtener el config del perfil.")
        return False
    obs.config_set_string(config, "SimpleOutput", "FilePath", path)
    obs.config_set_string(config, "AdvOut", "RecFilePath", path)
    obs.config_set_string(config, "AdvOut", "FFFilePath",  path)
    obs.config_save_safe(config, "tmp", None)
    return True

def set_paths_for_folder(folder_name):
    global _current_recording_folder
    
    # Limpiar carpeta anterior si existe y está activada la limpieza
    if enable_cleanup and _current_recording_folder:
        print("[Cleanup] Limpiando carpeta anterior: '{}'".format(_current_recording_folder))
        _cleanup_black_files(_current_recording_folder)
    
    target = os.path.join(base_folder, folder_name)
    ensure_folder(target)
    ok = apply_path_to_config(target)
    if ok:
        _current_recording_folder = target
        obs.script_log(obs.LOG_INFO, "Path actualizado -> '{}'".format(target))
    return ok


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

def _cleanup_black_files(folder):
    """Limpia grabaciones negras en la carpeta especificada."""
    _cleanup_black_files_in_folder(folder, cleanup_threshold, ffmpeg_exe)

def _cleanup_black_files_in_folder(folder, threshold, ffmpeg):
    """Limpia grabaciones negras en la carpeta especificada con parámetros."""
    if not folder or not os.path.isdir(folder):
        return

    # Buscar videos
    candidates = []
    for ext in _VIDEO_EXTS:
        candidates.extend(glob.glob(os.path.join(folder, ext)))

    if not candidates:
        return

    print("[Cleanup] Analizando {} videos en '{}'".format(len(candidates), folder))

    # Analizar cada video
    for filepath in candidates:
        try:
            # blackdetect
            cmd = [ffmpeg, "-i", filepath, "-vf", "blackdetect=d=0:pix_th=0.10:picture_black_ratio_th=0.98", "-an", "-f", "null", "-"]
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
            output = result.stderr.decode("utf-8", errors="replace")

            match = re.search(r"black_duration:([\d\.]+)", output)
            duration_match = re.search(r"Duration:\s+(\d+):(\d+):(\d+\.?\d*)", output)

            if match and duration_match:
                total_black = float(match.group(1))
                total_secs = int(duration_match.group(1)) * 3600 + int(duration_match.group(2)) * 60 + float(duration_match.group(3))
                ratio_pct = (total_black / total_secs) * 100 if total_secs > 0 else 0

                print("[Cleanup] Video '{}' - {:.0f}% negro".format(os.path.basename(filepath), ratio_pct))

                if ratio_pct >= threshold:
                    folder_path = os.path.dirname(filepath)
                    os.remove(filepath)
                    print("[Cleanup] ✓ Eliminado video negro ({:.0f}%): '{}'".format(ratio_pct, filepath))
                    _delete_empty_folder(folder_path)
                else:
                    print("[Cleanup] ✓ Video conservado ({:.0f}% < {}%)".format(ratio_pct, threshold))

        except Exception as e:
            print("[Cleanup] Error analizando '{}': {}".format(filepath, e))
    
    # Verificación final: si la carpeta quedó vacía después de procesar todos los archivos
    try:
        if os.path.isdir(folder):
            items = os.listdir(folder)
            non_hidden = [i for i in items if not i.startswith('.') and i.lower() != 'thumbs.db']
            if not non_hidden:
                print("[Cleanup] Verificación final: carpeta vacía, eliminando '{}'".format(folder))
                _delete_empty_folder(folder)
    except Exception as e:
        print("[Cleanup] Error en verificación final: {}".format(e))


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

    # Actualizar path en config ahora (no afecta grabacion activa)
    set_paths_for_folder(folder_name)

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

    if event == obs.OBS_FRONTEND_EVENT_SCENE_CHANGED:
        handle_scene_changed()

    elif event == obs.OBS_FRONTEND_EVENT_RECORDING_STOPPED:
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
    obs.obs_frontend_add_event_callback(on_event)
    
    # Leer settings directamente para limpieza al inicio
    cleanup_enabled = obs.obs_data_get_bool(settings, "enable_cleanup")
    base = obs.obs_data_get_string(settings, "base_folder")
    threshold = obs.obs_data_get_int(settings, "cleanup_threshold")
    ffmpeg = obs.obs_data_get_string(settings, "ffmpeg_exe")
    if not ffmpeg.strip():
        ffmpeg = "ffmpeg"
    
    # Limpiar todas las carpetas al arrancar si está activado
    if cleanup_enabled and base:
        print("[Cleanup] Limpiando todas las carpetas al arrancar...")
        try:
            for item in os.listdir(base):
                folder_path = os.path.join(base, item)
                if os.path.isdir(folder_path):
                    _cleanup_black_files_in_folder(folder_path, threshold, ffmpeg)
        except Exception as e:
            print("[Cleanup] Error al limpiar carpetas: {}".format(e))
    
    obs.script_log(obs.LOG_INFO, "Set-Escene-Path cargado.")

def script_unload():
    obs.timer_remove(_deferred_restart)
    obs.timer_remove(_start_recording_after_stop)
    obs.timer_remove(_start_replay_after_stop)
    obs.script_log(obs.LOG_INFO, "Set-Escene-Path descargado.")
