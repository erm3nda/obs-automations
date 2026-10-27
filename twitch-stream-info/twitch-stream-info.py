import obspython as obs
import urllib.request
import urllib.parse
import urllib.error
import json
import threading
import queue
import re
import html
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
_last_selected_scene = None
_scene_ui_properties = None
_fallback_warning_logged = False
_credential_status_property = None
_credential_validation_status = "Todavía no se han comprobado."
_credential_backend_status = "No hay credenciales guardadas."
_credential_migration_pending = False
_credential_conflict_detected = False
_credential_storage_mode = None
_main_thread_actions = queue.Queue()
_http_stop_event = threading.Event()
_http_workers = set()
_http_workers_lock = threading.Lock()

CREDENTIAL_TARGET = "Twitch-Stream-Info"
CREDENTIAL_FILE = ".obs-twitch-stream-info-credentials.json"
CREDENTIALS_PENDING_SETTING = "_pending_twitch_credentials_json"
CREDENTIAL_KEYS = (
    "twitch_client_id",
    "twitch_client_secret",
    "twitch_oauth_token",
    "twitch_refresh_token",
)
_credential_presence = {key: False for key in CREDENTIAL_KEYS}


def _plaintext_fallback_allowed(settings):
    return bool(settings and obs.obs_data_get_bool(settings, "allow_plaintext_fallback"))


def _plaintext_fallback_forced(settings):
    return bool(settings and obs.obs_data_get_bool(settings, "force_plaintext_storage"))


class CredentialPersistenceError(Exception):
    pass


class CredentialSourceCleanupError(CredentialPersistenceError):
    pass


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
    platform_warning = (
        " En Windows, el acceso depende de las ACL del perfil de usuario; OBS no cifra el archivo."
        if os.name == "nt"
        else " En Unix, el archivo se restringe a permisos 0600."
    )
    obs.script_log(
        obs.LOG_INFO,
        f"[{PLUGIN_NAME}] [AVISO] {reason} El archivo local es SIN CIFRAR: "
        f"'{_credential_file_path()}'. Protege ese archivo y no lo compartas.{platform_warning}"
    )


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
        if secret.lower().startswith("oauth:"):
            token_without_prefix = secret[6:]
            variants.update(
                (
                    token_without_prefix,
                    urllib.parse.quote(token_without_prefix, safe=""),
                    urllib.parse.quote_plus(token_without_prefix),
                )
            )
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                message = message.replace(variant, "[REDACTED]")
    return message


def _oauth_authorization_header(token):
    normalized = token.strip()
    if normalized.lower().startswith("oauth:"):
        normalized = normalized[6:]
    return "OAuth " + normalized


def _credential_status_text():
    credentials = (
        ("ID", "twitch_client_id", twitch_client_id),
        ("CS", "twitch_client_secret", twitch_client_secret),
        ("A", "twitch_oauth_token", twitch_oauth_token),
        ("R", "twitch_refresh_token", twitch_refresh_token),
    )
    presence = " ".join(
        "{}{}".format(
            label,
            "✓" if value or _credential_presence.get(key, False) else "–",
        )
        for label, key, value in credentials
    )

    backend_status = _credential_backend_status
    backend_lower = backend_status.lower()
    file_path = _credential_file_path()
    if "conflicto" in backend_lower:
        backend = "conflicto"
        if "archivo local sin cifrar" in backend_lower:
            backend += " · SIN CIFRAR: " + file_path
    elif "archivo local sin cifrar" in backend_lower:
        backend = "SIN CIFRAR: " + file_path
        if any(marker in backend_lower for marker in ("fallo", "error", "no se pudo")):
            backend += " · error"
    elif "credential manager" in backend_lower:
        backend = "Credential Manager"
        if any(marker in backend_lower for marker in ("fallo", "error", "no se pudo")):
            backend += " · error"
    elif "no hay credenciales" in backend_lower:
        backend = "sin credenciales"
    else:
        backend = "error de almacén"

    validation_lower = _credential_validation_status.lower()
    if any(marker in validation_lower for marker in ("válid", "validado")):
        validation = "válido"
    elif any(marker in validation_lower for marker in ("error", "rechaz", "fallo")):
        validation = "error"
    else:
        validation = "pendiente"

    migration = "pendiente" if _credential_migration_pending else "OK"
    details = [
        "• Credenciales (ID / secreto / acceso / refresh): {}".format(presence),
        "• Almacén: {}".format(backend),
        "• Token: {} · Migración: {}".format(validation, migration),
    ]
    scene_name = get_active_scene_name()
    if scene_name:
        details.insert(2, "• Escena activa: {}".format(html.escape(scene_name)))
    return "<br>".join(details)


def _update_credential_status():
    if _credential_status_property is not None:
        obs.obs_property_set_description(
            _credential_status_property,
            _credential_status_text(),
        )


def _queue_main_thread_action(action, value=None):
    _main_thread_actions.put((action, value))


def _process_main_thread_actions():
    global _credential_backend_status, _credential_validation_status, _credential_migration_pending
    while True:
        try:
            action, value = _main_thread_actions.get_nowait()
        except queue.Empty:
            break

        if action == "log":
            level, message = value
            obs.script_log(level, message)
        elif action == "persist_credentials":
            try:
                backend = _save_credentials(
                    value,
                    allow_plaintext_fallback=_plaintext_fallback_allowed(_script_settings),
                    force_plaintext=_plaintext_fallback_forced(_script_settings),
                )
                if _script_settings:
                    _clear_credentials_from_settings(_script_settings)
                _credential_backend_status = backend
                _credential_validation_status = (
                    "Token renovado y persistido; access token todavía sin validar."
                )
            except CredentialSourceCleanupError as error:
                _credential_migration_pending = True
                _credential_backend_status = (
                    _credential_backend_status + "; destino verificado, pero la limpieza de una copia anterior falló."
                )
                _credential_validation_status = "Token renovado y persistido; limpieza parcial de credenciales."
                obs.script_log(
                    obs.LOG_INFO,
                    f"[{PLUGIN_NAME}] [ERROR] Token renovado y destino verificado, pero no se limpió una copia anterior: "
                    f"{_safe_error(error, value)}"
                )
            except (OSError, ValueError, CredentialPersistenceError) as error:
                _credential_migration_pending = True
                _credential_presence.update({key: bool(item) for key, item in value.items()})
                _credential_backend_status = (
                    _credential_backend_status
                    + "; los tokens nuevos solo viven en memoria y podrían perderse al cerrar."
                )
                _credential_validation_status = "Token renovado, pero la persistencia falló."
                obs.script_log(
                    obs.LOG_INFO,
                    f"[{PLUGIN_NAME}] [ERROR] No se pudieron persistir las credenciales renovadas: "
                    f"{_safe_error(error, value)}. Los tokens nuevos solo viven en memoria y podrían perderse al cerrar."
                )
            _update_credential_status()
        elif action == "refresh_timestamp" and _script_settings:
            obs.obs_data_set_int(_script_settings, "last_refresh_timestamp", value)
        elif action == "credential_status":
            _update_credential_status()


def _queue_obs_log(level, message):
    _queue_main_thread_action("log", (level, _safe_error(message)))


def _read_windows_credentials(target=CREDENTIAL_TARGET):
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
    if not advapi32.CredReadW(target, 1, 0, ctypes.byref(credential)):
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


def _write_windows_credentials(credentials, target=CREDENTIAL_TARGET):
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL

    blob = json.dumps(credentials, ensure_ascii=False).encode("utf-8")
    if len(blob) > 2560:
        raise ValueError("Credential data exceeds the Windows Credential Manager limit")
    blob_buffer = ctypes.create_string_buffer(blob)
    credential = _Credential()
    credential.Type = 1
    credential.TargetName = target
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(
        blob_buffer, ctypes.POINTER(ctypes.c_ubyte)
    )
    credential.Persist = 2
    credential.UserName = PLUGIN_NAME
    if not advapi32.CredWriteW(ctypes.byref(credential), 0):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, "CredWriteW failed")


def _delete_windows_credentials(target=CREDENTIAL_TARGET):
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    if not advapi32.CredDeleteW(target, 1, 0):
        error_code = ctypes.get_last_error()
        if error_code != 1168:
            raise OSError(error_code, "CredDeleteW failed")


def _normalize_credentials(credentials):
    return {
        key: credentials.get(key, "") if isinstance(credentials.get(key, ""), str) else ""
        for key in CREDENTIAL_KEYS
    }


def _credential_source_conflicts(windows_credentials, fallback_credentials):
    return tuple(
        key
        for key in CREDENTIAL_KEYS
        if windows_credentials.get(key)
        and fallback_credentials.get(key)
        and windows_credentials[key] != fallback_credentials[key]
    )


def _read_fallback_credentials():
    path = _credential_file_path()
    if not os.path.exists(path):
        return {}
    if os.name != "nt":
        os.chmod(path, 0o600)
    with open(path, "r", encoding="utf-8") as credential_file:
        stored = json.load(credential_file)
    if not isinstance(stored, dict):
        raise ValueError("Credential file must contain a JSON object")
    return _normalize_credentials(stored)


def _write_fallback_credentials(credentials):
    path = _credential_file_path()
    temp_path = None
    descriptor = None
    try:
        descriptor, temp_path = tempfile.mkstemp(
            prefix=CREDENTIAL_FILE + ".",
            suffix=".tmp",
            dir=os.path.dirname(path),
        )
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as credential_file:
            descriptor = None
            json.dump(credentials, credential_file, ensure_ascii=False)
            credential_file.flush()
            os.fsync(credential_file.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def _remove_fallback_credentials():
    try:
        os.remove(_credential_file_path())
    except FileNotFoundError:
        pass


def _read_credential_sources():
    windows_credentials = {}
    fallback_credentials = {}
    windows_error = None
    fallback_error = None
    if os.name == "nt":
        try:
            windows_credentials = _normalize_credentials(_read_windows_credentials() or {})
        except (OSError, ValueError, json.JSONDecodeError) as error:
            windows_error = error
    try:
        fallback_credentials = _read_fallback_credentials()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        fallback_error = error
    return windows_credentials, fallback_credentials, windows_error, fallback_error


def _set_backend_status_from_sources(windows_credentials, fallback_credentials, errors=()):
    global _credential_backend_status
    if windows_credentials and not errors:
        _credential_backend_status = "Credential Manager verificado"
        if fallback_credentials:
            _credential_backend_status += (
                f"; archivo local SIN CIFRAR también presente: {_credential_file_path()}"
            )
            _log_fallback_warning("Se detectó un almacén local de credenciales.")
    elif errors:
        _credential_backend_status = "Fallo al leer el almacén: {}".format(
            "; ".join(_safe_error(error) for error in errors)
        )
        if fallback_credentials:
            _credential_backend_status += (
                f"; archivo local SIN CIFRAR: {_credential_file_path()}"
            )
            _log_fallback_warning("Se detectó un almacén local de credenciales.")
    elif fallback_credentials:
        _credential_backend_status = f"Archivo local SIN CIFRAR: {_credential_file_path()}"
        _log_fallback_warning("Se detectó un almacén local de credenciales.")
    else:
        _credential_backend_status = "No hay credenciales guardadas."
    _update_credential_status()


def _load_credentials(force_plaintext=False):
    global _credential_conflict_detected, _credential_migration_pending, _credential_backend_status
    windows_credentials, fallback_credentials, windows_error, fallback_error = _read_credential_sources()
    conflicts = _credential_source_conflicts(windows_credentials, fallback_credentials)
    if conflicts:
        _credential_conflict_detected = True
        _credential_migration_pending = True
        _set_runtime_credentials({})
        _credential_validation_status = "Validación detenida hasta resolver el conflicto entre almacenes."
        _credential_backend_status = (
            f"Conflicto entre Credential Manager y archivo local SIN CIFRAR ({_credential_file_path()}); "
            "se conservan ambas copias. Guardado explícito requerido."
        )
        _credential_presence.update({
            key: bool(windows_credentials.get(key) or fallback_credentials.get(key))
            for key in CREDENTIAL_KEYS
        })
        _update_credential_status()
        return {}
    preferred_credentials = fallback_credentials if force_plaintext else windows_credentials
    secondary_credentials = windows_credentials if force_plaintext else fallback_credentials
    combined = {
        key: (
            preferred_credentials.get(key)
            or secondary_credentials.get(key)
            or ""
        )
        for key in CREDENTIAL_KEYS
    }
    errors = tuple(error for error in (windows_error, fallback_error) if error)
    _set_backend_status_from_sources(windows_credentials, fallback_credentials, errors)
    _credential_conflict_detected = False
    _credential_migration_pending = bool(
        errors
        or (
            os.name == "nt"
            and (
                (force_plaintext and windows_credentials)
                or (not force_plaintext and windows_credentials and fallback_credentials)
            )
        )
    )
    if force_plaintext and fallback_credentials:
        _credential_backend_status = (
            f"Archivo local SIN CIFRAR (forzado): {_credential_file_path()}"
        )
        _update_credential_status()
    return combined


def _verify_stored_credentials(credentials):
    windows_credentials, fallback_credentials, windows_error, fallback_error = _read_credential_sources()
    errors = tuple(error for error in (windows_error, fallback_error) if error)
    if windows_credentials == credentials and os.name == "nt" and not windows_error:
        return "Credential Manager verificado"
    if fallback_credentials == credentials and not fallback_error:
        return f"Archivo local SIN CIFRAR: {_credential_file_path()}"
    raise CredentialPersistenceError(
        "La relectura no coincide con el conjunto guardado{}".format(
            ": " + "; ".join(_safe_error(error, credentials) for error in errors)
            if errors else ""
        )
    )


def _save_credentials(
    credentials,
    allow_plaintext_fallback=False,
    resolve_conflict=False,
    force_plaintext=False,
):
    global _credential_backend_status, _credential_migration_pending
    normalized = _normalize_credentials(credentials)
    if _credential_conflict_detected and not resolve_conflict:
        raise CredentialPersistenceError(
            "Hay credenciales divergentes entre Credential Manager y el archivo local; "
            "resuélvelo con un guardado explícito de los cuatro campos."
        )

    windows_credentials = {}
    fallback_credentials = {}
    if os.name == "nt":
        try:
            windows_credentials = _normalize_credentials(_read_windows_credentials() or {})
        except (OSError, ValueError, json.JSONDecodeError) as error:
            windows_error = error
        else:
            windows_error = None
        try:
            fallback_credentials = _read_fallback_credentials()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            _credential_backend_status = (
                f"No se pudo leer el archivo local SIN CIFRAR {_credential_file_path()}: "
                + _safe_error(error, normalized)
            )
            _update_credential_status()
            raise CredentialPersistenceError(_credential_backend_status) from error

        source_conflicts = _credential_source_conflicts(
            windows_credentials,
            fallback_credentials,
        )
        if source_conflicts and not resolve_conflict:
            _credential_backend_status = (
                f"Conflicto entre Credential Manager y archivo local SIN CIFRAR "
                f"({_credential_file_path()}); se conservan ambas copias."
            )
            _update_credential_status()
            raise CredentialPersistenceError(
                "Credential Manager y el archivo local contienen valores distintos."
            )

        if force_plaintext:
            try:
                _write_fallback_credentials(normalized)
                if _read_fallback_credentials() != normalized:
                    raise CredentialPersistenceError(
                        "La relectura del archivo local no coincide."
                    )
            except (OSError, ValueError, CredentialPersistenceError) as error:
                _credential_backend_status = (
                    f"Fallback SIN CIFRAR forzado no verificado; Credential Manager "
                    f"conservado si existía. Archivo: {_credential_file_path()}. "
                    + _safe_error(error, normalized)
                )
                _update_credential_status()
                raise CredentialPersistenceError(_credential_backend_status) from error
            if any(windows_credentials.values()):
                try:
                    _delete_windows_credentials()
                except OSError as error:
                    _credential_backend_status = (
                        f"Archivo local SIN CIFRAR verificado, pero no se pudo retirar "
                        f"Credential Manager; ambos almacenes permanecen. "
                        f"Archivo: {_credential_file_path()}."
                    )
                    _credential_migration_pending = True
                    _update_credential_status()
                    raise CredentialSourceCleanupError(
                        "No se pudo borrar Credential Manager tras verificar el fallback: "
                        + _safe_error(error, normalized)
                    ) from error
            _credential_backend_status = (
                f"Archivo local SIN CIFRAR (forzado): {_credential_file_path()}"
            )
            _credential_migration_pending = bool(windows_error)
            if windows_error:
                _credential_backend_status += (
                    "; no se pudo verificar Credential Manager, puede quedar una copia anterior."
                )
            _log_fallback_warning(
                "El almacenamiento local SIN CIFRAR está forzado; el archivo se verificó antes de retirar Credential Manager."
            )
            _update_credential_status()
            return _credential_backend_status

        try:
            _write_windows_credentials(normalized)
            verified_backend = _verify_stored_credentials(normalized)
            if verified_backend != "Credential Manager verificado":
                raise CredentialPersistenceError(
                    "Credential Manager no confirmó el read-back."
                )
        except (OSError, ValueError, CredentialPersistenceError) as error:
            if not allow_plaintext_fallback:
                _credential_backend_status = (
                    "Fallo de Credential Manager; origen anterior conservado. "
                    + _safe_error(error, normalized)
                )
                _update_credential_status()
                raise CredentialPersistenceError(_credential_backend_status) from error
            secure_conflicts = any(
                windows_credentials.get(key)
                and windows_credentials[key] != normalized[key]
                for key in CREDENTIAL_KEYS
            )
            if secure_conflicts and not resolve_conflict:
                _credential_backend_status = (
                    "Credential Manager contiene otros valores; fallback no creado "
                    "para evitar credenciales divergentes."
                )
                _update_credential_status()
                raise CredentialPersistenceError(_credential_backend_status) from error
            try:
                _write_fallback_credentials(normalized)
                if _read_fallback_credentials() != normalized:
                    raise CredentialPersistenceError(
                        "La relectura del archivo local no coincide."
                    )
            except (OSError, ValueError, CredentialPersistenceError) as fallback_error:
                _credential_backend_status = (
                    f"Fallo de persistencia del archivo local SIN CIFRAR "
                    f"{_credential_file_path()}: {_safe_error(fallback_error, normalized)}"
                )
                _update_credential_status()
                raise CredentialPersistenceError(_credential_backend_status) from fallback_error
            if any(windows_credentials.values()):
                _credential_backend_status = (
                    "Fallback local verificado; Credential Manager conserva sus valores "
                    "anteriores. Se requiere resolución explícita del conflicto."
                )
                _update_credential_status()
                raise CredentialPersistenceError(_credential_backend_status) from error
            _credential_backend_status = f"Archivo local SIN CIFRAR: {_credential_file_path()}"
            _credential_migration_pending = bool(windows_error)
            if windows_error:
                _credential_backend_status += (
                    "; Credential Manager inaccesible, no se pudo confirmar si queda una copia."
                )
            _log_fallback_warning(
                "Se autorizó fallback SIN CIFRAR porque Credential Manager no está disponible."
            )
            _update_credential_status()
            return _credential_backend_status

        if any(fallback_credentials.values()):
            try:
                _remove_fallback_credentials()
            except OSError as error:
                _credential_backend_status = (
                    f"Credential Manager verificado; permanece el archivo local SIN CIFRAR "
                    f"{_credential_file_path()}."
                )
                _credential_migration_pending = True
                _update_credential_status()
                raise CredentialSourceCleanupError(
                    "No se pudo eliminar el archivo local anterior: "
                    + _safe_error(error, normalized)
                ) from error
        _credential_backend_status = verified_backend
        _credential_migration_pending = False
        _update_credential_status()
        return verified_backend

    if not allow_plaintext_fallback and not force_plaintext:
        _credential_backend_status = (
            "Este sistema no tiene almacén seguro; autoriza el fallback SIN CIFRAR "
            "o fuerza su uso local."
        )
        _update_credential_status()
        raise CredentialPersistenceError(_credential_backend_status)

    try:
        _write_fallback_credentials(normalized)
        if _read_fallback_credentials() != normalized:
            raise CredentialPersistenceError("La relectura del archivo local no coincide.")
    except (OSError, ValueError, CredentialPersistenceError) as error:
        _credential_backend_status = (
            f"Fallo de persistencia del archivo local SIN CIFRAR: {_safe_error(error, normalized)}"
        )
        _update_credential_status()
        raise CredentialPersistenceError(_credential_backend_status) from error

    _credential_backend_status = (
        f"Archivo local SIN CIFRAR (forzado): {_credential_file_path()}"
        if force_plaintext else
        f"Archivo local SIN CIFRAR: {_credential_file_path()}"
    )
    _credential_migration_pending = False
    _log_fallback_warning("Se autorizó el almacenamiento local SIN CIFRAR.")
    _update_credential_status()
    return _credential_backend_status


def _pending_credentials_from_settings(settings):
    raw_pending = obs.obs_data_get_string(settings, CREDENTIALS_PENDING_SETTING)
    if not raw_pending:
        return {}
    try:
        pending = json.loads(raw_pending)
    except json.JSONDecodeError as error:
        raise ValueError("El almacén pendiente de OBS no contiene JSON válido.") from error
    if not isinstance(pending, dict):
        raise ValueError("El almacén pendiente de OBS debe ser un objeto JSON.")
    return _normalize_credentials(pending)


def _legacy_credentials_from_settings(settings):
    pending = _pending_credentials_from_settings(settings)
    visible = _credentials_from_settings(settings)
    return {
        key: visible.get(key) or pending.get(key) or ""
        for key in CREDENTIAL_KEYS
    }


def _preserve_pending_credentials(settings, credentials):
    normalized = _normalize_credentials(credentials)
    obs.obs_data_set_string(
        settings,
        CREDENTIALS_PENDING_SETTING,
        json.dumps(normalized, ensure_ascii=False) if any(normalized.values()) else "",
    )
    for key in CREDENTIAL_KEYS:
        obs.obs_data_set_string(settings, key, "")


def _clear_credentials_from_settings(settings):
    for key in CREDENTIAL_KEYS:
        obs.obs_data_set_string(settings, key, "")
    obs.obs_data_set_string(settings, CREDENTIALS_PENDING_SETTING, "")


def _clear_credentials(settings=None):
    global _credential_backend_status
    global _credential_migration_pending, _credential_conflict_detected
    errors = []
    retained_sources = []
    if os.name == "nt":
        try:
            _delete_windows_credentials()
        except OSError as error:
            errors.append("Credential Manager: {}".format(_safe_error(error)))
            retained_sources.append("Credential Manager")
    try:
        _remove_fallback_credentials()
    except OSError as error:
        errors.append("Archivo local SIN CIFRAR: {}".format(_safe_error(error)))
        retained_sources.append(f"archivo local SIN CIFRAR {_credential_file_path()}")
    if settings is not None:
        _clear_credentials_from_settings(settings)
    if errors:
        _credential_migration_pending = True
        _credential_backend_status = "Error parcial al borrar; permanecen: {}".format(
            ", ".join(retained_sources)
        )
        _update_credential_status()
        raise CredentialPersistenceError("; ".join(errors))
    _credential_backend_status = "No hay credenciales guardadas."
    _credential_migration_pending = False
    _credential_conflict_detected = False
    _update_credential_status()


def _persist_runtime_credentials():
    credentials = {key: globals()[key] for key in CREDENTIAL_KEYS}
    _queue_main_thread_action("persist_credentials", credentials)
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
    global _last_selected_scene
    try:
        scene_name = obs.obs_data_get_string(settings, "selected_scene")
        title, category = get_scene_data_from_obs(settings, scene_name)
    except (RecursionError, SceneMapError) as error:
        _log_scene_map_error("No se pudo cargar la escena seleccionada", error)
        return False
    obs.obs_data_set_string(settings, "current_scene_title", title)
    obs.obs_data_set_string(settings, "current_scene_category", category)
    _last_selected_scene = scene_name
    obs.obs_properties_apply_settings(props, settings)
    return True


class SceneMapError(ValueError):
    pass


def _log_scene_map_error(context, error):
    obs.script_log(
        obs.LOG_INFO,
        f"[{PLUGIN_NAME}] [ERROR] {context}: {_safe_error(error)}",
    )


def _read_scene_map(settings):
    if not settings:
        return {}

    try:
        scene_map_json = obs.obs_data_get_string(settings, "scene_map_json")
    except RecursionError as error:
        raise SceneMapError("No se pudo leer el mapa JSON de escenas desde OBS.") from error
    primary_error = None
    if scene_map_json:
        try:
            scene_map = json.loads(scene_map_json)
            if not isinstance(scene_map, dict):
                raise ValueError("El mapa de escenas guardado no es un objeto JSON")
            if scene_map:
                return scene_map
        except (json.JSONDecodeError, RecursionError, ValueError) as error:
            primary_error = error

    try:
        legacy_map = obs.obs_data_get_obj(settings, "scene_map")
        if not legacy_map:
            if primary_error:
                raise SceneMapError(
                    "El mapa JSON actual está dañado y no hay un mapa antiguo recuperable: "
                    + _safe_error(primary_error)
                )
            return {}
        try:
            legacy_map_json = obs.obs_data_get_json(legacy_map)
        finally:
            obs.obs_data_release(legacy_map)
        legacy_map = json.loads(legacy_map_json)
        if not isinstance(legacy_map, dict):
            raise ValueError("El mapa antiguo no es un objeto JSON")
        if primary_error and not legacy_map:
            raise SceneMapError(
                "El mapa JSON actual está dañado y el mapa antiguo está vacío; "
                "se conservan ambos valores sin reemplazarlos."
            )
        serialized_legacy_map = json.dumps(legacy_map, ensure_ascii=False)
        obs.obs_data_set_string(settings, "scene_map_json", serialized_legacy_map)
        obs.obs_data_set_string(settings, "scene_map", "")
        if primary_error:
            obs.script_log(
                obs.LOG_INFO,
                f"[{PLUGIN_NAME}] [AVISO] Se recuperó el mapa de escenas antiguo; "
                "el mapa JSON principal estaba dañado.",
            )
        return legacy_map
    except SceneMapError:
        raise
    except (RecursionError, ValueError, json.JSONDecodeError) as error:
        details = "No se pudo leer el mapa antiguo: " + _safe_error(error)
        if primary_error:
            details = (
                "El mapa JSON principal y el mapa antiguo no se pudieron recuperar. "
                "Mapa principal: {}; mapa antiguo: {}"
            ).format(_safe_error(primary_error), _safe_error(error))
        raise SceneMapError(details) from error

def get_scene_data_from_obs(settings, scene_name):
    """Obtiene el título y la categoría de una escena guardada en las settings nativas de OBS."""
    if not settings or not scene_name:
        return "", ""

    try:
        scene_item = _read_scene_map(settings).get(scene_name, {})
    except RecursionError as error:
        raise SceneMapError("La lectura del mapa de escenas excedió la recursión permitida.") from error
    if not isinstance(scene_item, dict):
        return "", ""
    return scene_item.get("title", ""), scene_item.get("category", "")

def save_scene_data_to_obs(settings, scene_name, title, category):
    """Guarda el título y la categoría de una escena directamente en el sistema de settings de OBS."""
    if not settings or not scene_name:
        return

    scene_map = _read_scene_map(settings)
    scene_map[scene_name] = {"title": title, "category": category}
    try:
        serialized_scene_map = json.dumps(scene_map, ensure_ascii=False)
    except (RecursionError, ValueError) as error:
        raise SceneMapError("El mapa de escenas no se pudo serializar: " + _safe_error(error)) from error
    obs.obs_data_set_string(settings, "scene_map_json", serialized_scene_map)
    obs.obs_data_set_string(settings, "scene_map", "")
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] ✓ Guardado nativo en OBS para escena '{scene_name}'")

def on_refresh_button_clicked(properties, property):
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Iniciando verificación manual del Token...")
    check_obs_twitch_integration()
    return True

def on_force_refresh_clicked(properties, property):
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Iniciando refresco forzado del token...")
    refresh_twitch_token()
    _update_credential_status()
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


def _credentials_from_legacy_config(config_data):
    if not isinstance(config_data, dict):
        raise ValueError("El archivo de ajustes debe contener un objeto JSON")

    aliases = {
        "twitch_client_id": ("twitch_client_id", "client_id"),
        "twitch_client_secret": ("twitch_client_secret", "client_secret"),
        "twitch_oauth_token": ("twitch_oauth_token", "oauth_token", "access_token"),
        "twitch_refresh_token": ("twitch_refresh_token", "refresh_token"),
    }
    credentials = {}
    for key, legacy_keys in aliases.items():
        for legacy_key in legacy_keys:
            value = config_data.get(legacy_key)
            if isinstance(value, str) and value:
                credentials[key] = value
                break
    return credentials


def _set_runtime_credentials(credentials):
    global twitch_client_id, twitch_client_secret, twitch_oauth_token, twitch_refresh_token
    global _credential_validation_status
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
    for key in CREDENTIAL_KEYS:
        _credential_presence[key] = bool(globals()[key])
    _credential_validation_status = (
        "Credenciales disponibles; access token todavía sin comprobar."
        if any((twitch_client_id, twitch_client_secret, twitch_oauth_token, twitch_refresh_token))
        else "No hay credenciales guardadas."
    )
    _update_credential_status()


def _migrate_credentials(settings, legacy_credentials=None, preserve_legacy=True):
    global _credential_backend_status, _credential_migration_pending, _credential_conflict_detected
    _credential_migration_pending = False
    _credential_conflict_detected = False
    try:
        pending = _pending_credentials_from_settings(settings)
    except ValueError as error:
        windows_credentials, fallback_credentials, windows_error, fallback_error = _read_credential_sources()
        conflicts = _credential_source_conflicts(windows_credentials, fallback_credentials)
        if conflicts:
            _credential_conflict_detected = True
            _credential_migration_pending = True
            _set_runtime_credentials({})
            _credential_presence.update({
                key: bool(windows_credentials.get(key) or fallback_credentials.get(key))
                for key in CREDENTIAL_KEYS
            })
            _credential_backend_status = (
                "Fallo al leer credenciales pendientes de OBS; además hay discrepancias entre "
                "Credential Manager y el archivo local SIN CIFRAR. Se conservan ambas copias."
            )
            _credential_validation_status = "Validación detenida hasta resolver el conflicto entre almacenes."
            _update_credential_status()
            return False
        recoverable = {
            key: windows_credentials.get(key) or fallback_credentials.get(key) or ""
            for key in CREDENTIAL_KEYS
        }
        _set_runtime_credentials(recoverable)
        source_errors = tuple(error for error in (windows_error, fallback_error) if error)
        details = "; ".join(_safe_error(error, recoverable) for error in source_errors)
        _credential_backend_status = (
            "Fallo al leer credenciales pendientes de OBS; no se han borrado. "
            + _safe_error(error, recoverable)
            + (" Errores de almacén: " + details if details else "")
        )
        _credential_migration_pending = True
        _update_credential_status()
        return False
    visible = _credentials_from_settings(settings)
    legacy = {
        key: (
            (legacy_credentials or {}).get(key)
            or visible.get(key)
            or pending.get(key)
            or ""
        )
        for key in CREDENTIAL_KEYS
    }
    windows_credentials, fallback_credentials, windows_error, fallback_error = _read_credential_sources()
    read_errors = tuple(error for error in (windows_error, fallback_error) if error)
    force_plaintext = _plaintext_fallback_forced(settings)
    conflicts = _credential_source_conflicts(windows_credentials, fallback_credentials)
    if conflicts:
        _credential_conflict_detected = True
        _credential_migration_pending = True
        _credential_presence.update({
            key: bool(
                windows_credentials.get(key)
                or fallback_credentials.get(key)
                or legacy.get(key)
            )
            for key in CREDENTIAL_KEYS
        })
        _set_runtime_credentials({})
        _credential_presence.update({
            key: bool(
                windows_credentials.get(key)
                or fallback_credentials.get(key)
                or legacy.get(key)
            )
            for key in CREDENTIAL_KEYS
        })
        _credential_backend_status = (
            f"Conflicto entre Credential Manager y archivo local SIN CIFRAR ({_credential_file_path()}); "
            "se conservan ambas copias. "
            "Guardado explícito de los cuatro campos requerido."
        )
        if any(legacy.values()) and preserve_legacy:
            _preserve_pending_credentials(settings, legacy)
            _credential_backend_status += " Las credenciales legacy de OBS siguen pendientes de migración."
        _credential_validation_status = "Validación detenida hasta resolver el conflicto entre almacenes."
        _update_credential_status()
        return False
    preferred_credentials = fallback_credentials if force_plaintext else windows_credentials
    secondary_credentials = windows_credentials if force_plaintext else fallback_credentials
    credentials = {
        key: (
            preferred_credentials.get(key)
            or secondary_credentials.get(key)
            or legacy.get(key)
            or ""
        )
        for key in CREDENTIAL_KEYS
    }
    _set_runtime_credentials(credentials)

    secure_read_error = windows_error if os.name == "nt" else None
    if read_errors and (
        fallback_error
        or (secure_read_error and not (
            force_plaintext or _plaintext_fallback_allowed(settings)
        ))
    ):
        details = "; ".join(_safe_error(error, credentials) for error in read_errors)
        _credential_backend_status = "Fallo de lectura del almacén: " + details
        if any(legacy.values()) and preserve_legacy:
            _preserve_pending_credentials(settings, legacy)
            _credential_backend_status += "; credenciales todavía en ajustes OBS / migración pendiente."
        _credential_migration_pending = True
        _update_credential_status()
        return False

    if not any(credentials.values()):
        _set_backend_status_from_sources(windows_credentials, fallback_credentials)
        _credential_migration_pending = bool(read_errors)
        return not read_errors

    consent = _plaintext_fallback_allowed(settings)
    needs_store = bool(any(legacy.values()))
    if force_plaintext:
        needs_store = (
            needs_store
            or fallback_credentials != credentials
            or bool(any(windows_credentials.values()))
        )
    elif os.name == "nt":
        needs_store = needs_store or windows_credentials != credentials
        if windows_credentials == credentials and any(fallback_credentials.values()):
            try:
                _remove_fallback_credentials()
                fallback_credentials = {}
            except OSError as error:
                _credential_backend_status = (
                    "Credential Manager verificado; no se pudo eliminar el archivo local SIN CIFRAR "
                    f"{_credential_file_path()}."
                )
                if any(legacy.values()) and preserve_legacy:
                    _preserve_pending_credentials(settings, legacy)
                _credential_migration_pending = True
                _update_credential_status()
                return False
    else:
        needs_store = needs_store and fallback_credentials != credentials

    if needs_store:
        try:
            _save_credentials(
                credentials,
                allow_plaintext_fallback=consent,
                force_plaintext=force_plaintext,
            )
        except (OSError, ValueError, CredentialPersistenceError) as error:
            retained = []
            if windows_credentials:
                retained.append("Credential Manager")
            if fallback_credentials:
                retained.append(f"archivo local SIN CIFRAR {_credential_file_path()}")
            if any(legacy.values()) and preserve_legacy:
                _preserve_pending_credentials(settings, legacy)
                retained.append("ajustes OBS / migración pendiente")
            _credential_backend_status = (
                "Fallo de persistencia; origen conservado: {}. Destino/estado: {}. {}".format(
                    ", ".join(retained) or "no se confirmó ningún almacén",
                    _credential_backend_status,
                    _safe_error(error, credentials),
                )
            )
            _credential_migration_pending = True
            _update_credential_status()
            return False
    elif fallback_credentials:
        _credential_backend_status = (
            f"Archivo local SIN CIFRAR (forzado): {_credential_file_path()}"
            if force_plaintext else
            f"Archivo local SIN CIFRAR: {_credential_file_path()}"
        )
        _log_fallback_warning("Se detectó un almacén local de credenciales.")
    elif windows_credentials:
        _credential_backend_status = "Credential Manager verificado"

    if any(legacy.values()) and preserve_legacy:
        _clear_credentials_from_settings(settings)
    _credential_conflict_detected = False
    _credential_migration_pending = bool(secure_read_error)
    _update_credential_status()
    return True


def on_save_credentials_clicked(properties, property):
    global _credential_backend_status, _credential_validation_status
    global _credential_migration_pending, _credential_conflict_detected
    settings_credentials = _credentials_from_settings(_script_settings)
    if _credential_conflict_detected and not all(settings_credentials.values()):
        _credential_migration_pending = True
        _credential_backend_status = (
            "Conflicto entre almacenes: introduce los cuatro campos y pulsa Guardar "
            "para resolverlo. Ambas copias anteriores se conservan."
        )
        _update_credential_status()
        obs.script_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] [ERROR] Para resolver el conflicto, introduce los cuatro campos antes de guardar."
        )
        return True
    credentials = {
        key: settings_credentials[key] or globals()[key]
        for key in CREDENTIAL_KEYS
    }
    if not any(credentials.values()):
        obs.script_log(obs.LOG_WARNING, f"[{PLUGIN_NAME}] No hay credenciales que guardar.")
        return True

    try:
        backend = _save_credentials(
            credentials,
            allow_plaintext_fallback=_plaintext_fallback_allowed(_script_settings),
            resolve_conflict=all(settings_credentials.values()),
            force_plaintext=_plaintext_fallback_forced(_script_settings),
        )
    except (OSError, ValueError, CredentialPersistenceError) as error:
        _credential_migration_pending = True
        _credential_presence.update({key: bool(value) for key, value in credentials.items()})
        _credential_backend_status = (
            _credential_backend_status
            + "; no se eliminó ninguna copia anterior. "
            + _safe_error(error, credentials)
        )
        _update_credential_status()
        obs.script_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] [ERROR] No se pudieron guardar las credenciales: "
            f"{_safe_error(error, credentials)}"
        )
        obs.script_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] [AVISO] No se eliminaron las credenciales de su origen anterior."
        )
        return True

    _set_runtime_credentials(credentials)
    _clear_credentials_from_settings(_script_settings)
    _credential_backend_status = backend
    _credential_validation_status = "Access token todavía sin comprobar."
    _credential_conflict_detected = False
    _update_credential_status()
    obs.obs_properties_apply_settings(properties, _script_settings)
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Credenciales verificadas en {backend}.")
    return True


def on_clear_credentials_clicked(properties, property):
    global _credential_backend_status, _credential_validation_status
    try:
        _clear_credentials(_script_settings)
    except (OSError, CredentialPersistenceError) as error:
        _credential_validation_status = "Borrado parcial; comprueba las copias indicadas."
        _update_credential_status()
        obs.script_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] [ERROR] No se pudieron borrar las credenciales: {_safe_error(error)}"
        )
        obs.obs_properties_apply_settings(properties, _script_settings)
        return True

    _set_runtime_credentials({})
    _credential_backend_status = "No hay credenciales guardadas."
    _credential_validation_status = "Credenciales borradas."
    _update_credential_status()
    obs.obs_properties_apply_settings(properties, _script_settings)
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Todas las copias conocidas de credenciales fueron borradas.")
    return True


def script_description():
    return (
        "<b>Uso de Twitch Stream Info</b><br>"
        "<b>Escena para editar:</b> selecciona una escena para cargar y cambiar su título y categoría guardados.<br>"
        "<b>Título vacío:</b> se forma con el nombre de la escena y la coletilla global.<br>"
        "<b>Modo de actualización:</b> elige actualización inmediata, con retardo o solo manual. <b>Bloquear durante directo:</b> detiene las automáticas, pero permite aplicar cambios manuales.<br>"
        "<b>Cargar categorías:</b> escribe al menos 3 letras; requiere Client ID y Access Token.<br>"
        "<b>Credenciales:</b> los campos vacíos conservan las guardadas. Sin Client Secret, la renovación envía el refresh token a TwitchTokenGenerator.com.<br>"
        "<b>Fallback local:</b> solo se usa si lo habilitas; el archivo queda en texto plano. En Windows depende de los permisos de tu perfil y en Unix usa permisos 0600. Si hay conflicto con Credential Manager, se conservan ambas copias; guarda los cuatro campos para resolverlo."
    )


def script_properties():
    global _credential_status_property, _last_selected_scene, _scene_ui_properties
    props = obs.obs_properties_create()
    obs.obs_properties_add_bool(props, "enabled", "Plugin activo")
    options_props = obs.obs_properties_create()
    
    active_scene = get_active_scene_name()
    selected_scene = active_scene or (
        obs.obs_data_get_string(_script_settings, "selected_scene")
        if _script_settings else ""
    )
    scene_data_available = True
    try:
        t_saved, c_saved = get_scene_data_from_obs(_script_settings, selected_scene)
    except SceneMapError as error:
        _log_scene_map_error("No se pudo cargar la escena para editar", error)
        scene_data_available = False
    if _script_settings and selected_scene:
        obs.obs_data_set_string(_script_settings, "selected_scene", selected_scene)
        if scene_data_available:
            obs.obs_data_set_string(_script_settings, "current_scene_title", t_saved)
            obs.obs_data_set_string(_script_settings, "current_scene_category", c_saved)
            _last_selected_scene = selected_scene

    scene_props = options_props
    _scene_ui_properties = scene_props
    p_scene = obs.obs_properties_add_list(
        scene_props, "selected_scene", "Escena para editar",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )
    for scene_name in get_scene_names():
        obs.obs_property_list_add_string(p_scene, scene_name, scene_name)
    obs.obs_property_set_modified_callback(p_scene, on_scene_selection_changed)
    obs.obs_properties_add_text(
        scene_props, "current_scene_title", "Título de la escena", obs.OBS_TEXT_DEFAULT
    )

    # Selector de iconos
    def on_add_icon(props, prop):
        # En el callback, 'props' es la instancia de obs_properties_t,
        # pero para obtener/setear datos necesitamos el objeto settings actual.
        if not _script_settings:
            obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] No hay ajustes disponibles para anadir el icono.")
            return True

        title = obs.obs_data_get_string(_script_settings, "current_scene_title")
        icon = obs.obs_data_get_string(_script_settings, "icon_selector") or ICONS[0]
        scene_name = (
            obs.obs_data_get_string(_script_settings, "selected_scene")
            or get_active_scene_name()
        )
        if not scene_name:
            obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] No hay una escena seleccionada para guardar el icono.")
            return True

        new_title = f"{title}{icon}"
        obs.obs_data_set_string(_script_settings, "current_scene_title", new_title)
        try:
            title_to_save = obs.obs_data_get_string(_script_settings, "current_scene_title")
            category_to_save = obs.obs_data_get_string(_script_settings, "current_scene_category")
            save_scene_data_to_obs(
                _script_settings,
                scene_name,
                title_to_save,
                category_to_save,
            )
        except (RecursionError, SceneMapError) as error:
            _log_scene_map_error("No se pudo guardar el icono en el mapa; se mantiene en el campo actual", error)

        obs.obs_properties_apply_settings(_scene_ui_properties or props, _script_settings)
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Icono anadido al titulo de '{scene_name}'.")
        return True

    p_icon_list = obs.obs_properties_add_list(scene_props, "icon_selector", "Selector de iconos", obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
    for ic in ICONS:
        obs.obs_property_list_add_string(p_icon_list, ic, ic)
    if _script_settings and not obs.obs_data_get_string(_script_settings, "icon_selector"):
        obs.obs_data_set_string(_script_settings, "icon_selector", ICONS[0])
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
    
    twitch_props = options_props
    obs.obs_properties_add_text(
        twitch_props,
        "credential_status",
        _credential_status_text(),
        obs.OBS_TEXT_INFO,
    )
    _credential_status_property = obs.obs_properties_get(twitch_props, "credential_status")
    obs.obs_properties_add_text(twitch_props, "twitch_client_id", "Twitch Client ID", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(twitch_props, "twitch_client_secret", "Twitch Client Secret", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(twitch_props, "twitch_oauth_token", "Twitch Access Token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(twitch_props, "twitch_refresh_token", "Twitch Refresh Token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_bool(
        twitch_props,
        "allow_plaintext_fallback",
        "Permitir fallback SIN CIFRAR",
    )
    obs.obs_properties_add_bool(
        twitch_props,
        "force_plaintext_storage",
        "Forzar almacenamiento local SIN CIFRAR",
    )
    obs.obs_properties_add_button(twitch_props, "save_credentials_button", "🔒 Guardar credenciales", on_save_credentials_clicked)
    obs.obs_properties_add_button(twitch_props, "clear_credentials_button", "🗑 Borrar credenciales guardadas", on_clear_credentials_clicked)
    p_scopes = obs.obs_properties_add_text(twitch_props, "twitch_scopes", "Scopes de Twitch", obs.OBS_TEXT_DEFAULT)
    obs.obs_property_set_modified_callback(p_scopes, lambda props, prop, settings: True)
    obs.obs_properties_add_button(twitch_props, "manual_generate_button", "🌐 Abrir TwitchTokenGenerator.com (Manual)", open_manual_token_generator)
    obs.obs_properties_add_button(twitch_props, "refresh_token_button", "🔍 Comprobar Token", on_refresh_button_clicked)
    obs.obs_properties_add_button(twitch_props, "force_refresh_button", "🔄 Forzar Refresco de Token", on_force_refresh_clicked)

    mng_props = options_props
    obs.obs_properties_add_path(
        mng_props, "settings_path", "Carpeta donde guardar ajustes",
        obs.OBS_PATH_DIRECTORY, "",
        os.path.join(os.path.expanduser("~"), "Desktop")
    )
    obs.obs_properties_add_button(mng_props, "export_btn", "💾 Guardar Ajustes", on_export_settings)
    obs.obs_properties_add_button(mng_props, "import_btn", "📂 Cargar Ajustes", on_import_settings)
    obs.obs_properties_add_group(
        props,
        "options_group",
        "Opciones del plugin",
        obs.OBS_GROUP_NORMAL,
        options_props,
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
    obs.obs_data_set_default_bool(settings, "allow_plaintext_fallback", False)
    obs.obs_data_set_default_bool(settings, "force_plaintext_storage", False)
    obs.obs_data_set_default_string(settings, CREDENTIALS_PENDING_SETTING, "")
    obs.obs_data_set_default_string(settings, "scene_map_json", "{}")
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
    global _script_settings, is_first_load, _is_importing_settings, _credential_storage_mode
    global _last_selected_scene
    
    _script_settings = settings
    enabled          = obs.obs_data_get_bool(settings, "enabled")
    active_scene = get_active_scene_name()

    global_suffix = obs.obs_data_get_string(settings, "global_suffix")
    new_title     = obs.obs_data_get_string(settings, "current_scene_title")
    new_category  = obs.obs_data_get_string(settings, "current_scene_category")
    
    target_scene = obs.obs_data_get_string(settings, "selected_scene") or active_scene
    if target_scene:
        selection_changed = target_scene != _last_selected_scene
        try:
            if is_first_load:
                # On initial load the editable fields may still be empty defaults;
                # hydrate them from the per-scene map instead of erasing it.
                saved_title, saved_category = get_scene_data_from_obs(settings, target_scene)
                if saved_title or saved_category:
                    new_title, new_category = saved_title, saved_category
                    obs.obs_data_set_string(settings, "current_scene_title", new_title)
                    obs.obs_data_set_string(settings, "current_scene_category", new_category)
                _last_selected_scene = target_scene
            elif selection_changed:
                # OBS calls script_update before the selector's modified callback.
                # Load the newly selected scene before treating the old editor fields as edits.
                new_title, new_category = get_scene_data_from_obs(settings, target_scene)
                obs.obs_data_set_string(settings, "current_scene_title", new_title)
                obs.obs_data_set_string(settings, "current_scene_category", new_category)
                _last_selected_scene = target_scene
            elif not _is_importing_settings:
                # Empty strings are intentional edits (clear the field), not a
                # signal to retain the previously saved value.
                save_scene_data_to_obs(settings, target_scene, new_title, new_category)
        except SceneMapError as error:
            _log_scene_map_error("No se guardó la información de la escena", error)
        current_scene_title    = new_title
        current_scene_category = new_category

    block_if_streaming  = obs.obs_data_get_bool(settings,   "block_if_streaming")
    update_mode        = obs.obs_data_get_int(settings,    "update_mode")
    delay_seconds      = obs.obs_data_get_int(settings,    "delay_seconds")
    broadcaster_id     = obs.obs_data_get_string(settings, "broadcaster_id")
    storage_mode = (
        _plaintext_fallback_allowed(settings),
        _plaintext_fallback_forced(settings),
    )
    if is_first_load or storage_mode != _credential_storage_mode:
        _migrate_credentials(settings)
        _credential_storage_mode = storage_mode
    else:
        settings_credentials = _legacy_credentials_from_settings(settings)
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

    try:
        saved_title, saved_cat = get_scene_data_from_obs(_script_settings, active_scene)
    except (RecursionError, SceneMapError) as error:
        _log_scene_map_error("No se aplicó información porque falló la lectura del mapa de escenas", error)
        return
    final_title = f"{saved_title if saved_title else clean_scene_name(active_scene)}{global_suffix}"
    final_category = saved_cat if saved_cat else clean_scene_name(active_scene)
    
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] Aplicando info para escena '{active_scene}' -> Título: '{final_title}' | Categoría: '{final_category}'")

    category_to_update = final_category if update_category else ""
    _start_stream_info_worker(final_title, category_to_update)


def _run_stream_info_update(title, category):
    try:
        if not _http_stop_event.is_set():
            update_stream_info_helix(title, category)
    finally:
        with _http_workers_lock:
            _http_workers.discard(threading.current_thread())


def _start_stream_info_worker(title, category):
    worker = threading.Thread(
        target=_run_stream_info_update,
        args=(title, category),
        daemon=True,
    )
    with _http_workers_lock:
        _http_workers.add(worker)
    try:
        worker.start()
    except Exception:
        with _http_workers_lock:
            _http_workers.discard(worker)
        raise

def get_game_id_by_name(game_name, headers):
    if _http_stop_event.is_set():
        return None
    if not game_name or game_name.lower() == "juego": return None
    url = f"https://api.twitch.tv/helix/games?name={urllib.parse.quote(game_name)}"
    req = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            games = res_data.get("data", [])
            if games and games[0].get("id"):
                return games[0]["id"]
            _queue_obs_log(
                obs.LOG_INFO,
                f"[{PLUGIN_NAME}] [AVISO] Twitch no encontró la categoría exacta '{game_name}'; se actualizará solo el título.",
            )
    except Exception as e:
        _queue_obs_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error buscando categoría: {e}")
    return None

def get_broadcaster_id_automatically(headers):
    if _http_stop_event.is_set():
        return None
    url = "https://api.twitch.tv/helix/users"
    req = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            users = res_data.get("data", [])
            if users and users[0].get("id"):
                return users[0]["id"]
            _queue_obs_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Twitch no devolvió un ID de canal para este token.")
    except Exception as e:
        _queue_obs_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error obteniendo Broadcaster ID: {e}")
    return None

def refresh_twitch_token():
    global twitch_oauth_token, twitch_refresh_token, twitch_client_id, twitch_client_secret
    global _credential_validation_status
    
    if _http_stop_event.is_set():
        return False
    if not twitch_refresh_token:
        _credential_validation_status = "No hay refresh token para renovar el access token."
        _queue_main_thread_action("credential_status")
        return False

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
                    _queue_main_thread_action("refresh_timestamp", int(time.time()))
                    _persist_runtime_credentials()
                    _credential_validation_status = "Token renovado; el access token aún no se ha validado."
                    _queue_main_thread_action("credential_status")
                    return True
        except Exception as e:
            _credential_validation_status = "Falló la renovación del token; revisa el log."
            _queue_obs_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error API oficial: {e}")
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
                    _queue_main_thread_action("refresh_timestamp", int(time.time()))
                    _persist_runtime_credentials()
                    _credential_validation_status = "Token renovado; el access token aún no se ha validado."
                    _queue_main_thread_action("credential_status")
                    return True
        except Exception as e:
            _credential_validation_status = "Falló la renovación del token; revisa el log."
            _queue_obs_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error API gen: {e}")
    _queue_main_thread_action("credential_status")
    return False

def update_stream_info_helix(title, category):
    global twitch_oauth_token, twitch_refresh_token
    if _http_stop_event.is_set():
        return
    if not twitch_client_id or not twitch_oauth_token:
        _queue_obs_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] [ERROR] No se actualizó el stream: falta Client ID o Access Token. Revisa las credenciales guardadas.",
        )
        return

    access_token = twitch_oauth_token.strip()
    if access_token.lower().startswith("oauth:"):
        access_token = access_token[6:]
    headers = {"Client-ID": twitch_client_id.strip(), "Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    current_broadcaster_id = broadcaster_id or get_broadcaster_id_automatically(headers)
    if not current_broadcaster_id:
        _queue_obs_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] [ERROR] No se actualizó el stream: no se pudo obtener Broadcaster ID. Comprueba el token y que el Client ID coincida.",
        )
        return
    if _http_stop_event.is_set(): return

    game_id = get_game_id_by_name(category, headers) if category else None
    if _http_stop_event.is_set():
        return
    url = f"https://api.twitch.tv/helix/channels?broadcaster_id={current_broadcaster_id}"
    body = {}
    if title: body["title"] = title
    if game_id: body["game_id"] = game_id

    if not body: return

    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='PATCH')
    if _http_stop_event.is_set():
        return

    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as response:
            if response.status in (204, 200):
                _queue_obs_log(obs.LOG_INFO, f"✓ [{PLUGIN_NAME}] ¡Información actualizada!")
                return
            _queue_obs_log(
                obs.LOG_INFO,
                f"[{PLUGIN_NAME}] [ERROR] Twitch respondió HTTP {response.status} al actualizar la información.",
            )
    except urllib.error.HTTPError as he:
        if he.code == 401 and not _http_stop_event.is_set() and refresh_twitch_token():
            if _http_stop_event.is_set():
                return
            headers["Authorization"] = f"Bearer {twitch_oauth_token.strip()}"
            req_retry = urllib.request.Request(url, data=data, headers=headers, method='PATCH')
            try:
                with urllib.request.urlopen(req_retry, timeout=HTTP_TIMEOUT_SECONDS) as res_retry:
                    if res_retry.status in (204, 200):
                        _queue_obs_log(obs.LOG_INFO, f"✓ [{PLUGIN_NAME}] ¡Información actualizada tras refrescar el token!")
                        return
            except urllib.error.HTTPError as retry_error:
                detail = _safe_error(retry_error.read().decode("utf-8", errors="replace"))
                _queue_obs_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Reintento HTTP {retry_error.code}: {detail or retry_error.reason}")
            except urllib.error.URLError as retry_error:
                _queue_obs_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Fallo de red en el reintento: {retry_error}")
            except TimeoutError as retry_error:
                _queue_obs_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Timeout en el reintento: {retry_error}")
            except OSError as retry_error:
                _queue_obs_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error de socket en el reintento: {retry_error}")
        else:
            detail = _safe_error(he.read().decode("utf-8", errors="replace"))
            _queue_obs_log(
                obs.LOG_INFO,
                f"[{PLUGIN_NAME}] [ERROR] Twitch respondió HTTP {he.code}: {detail or he.reason}",
            )
            if he.code == 401 and not _http_stop_event.is_set():
                _queue_obs_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Twitch rechazó la autorización; revisa el scope channel:manage:broadcast y la cuenta del token.")
    except urllib.error.URLError as error:
        _queue_obs_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Fallo de red actualizando la información: {error}")
    except TimeoutError as error:
        _queue_obs_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Timeout actualizando la información: {error}")
    except OSError as error:
        _queue_obs_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error de socket actualizando la información: {error}")

def on_apply_clicked(properties, property):
    if not enabled:
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] AplicaciÃ³n manual cancelada: activa el plugin primero.")
        return True

    scene_name = (
        obs.obs_data_get_string(_script_settings, "selected_scene")
        if _script_settings else ""
    ) or get_active_scene_name()
    if not scene_name:
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] No hay una escena seleccionada para aplicar.")
        return True

    # Use the values shown in the editor. The active scene's saved map can be
    # stale (or unreadable), and manual Apply must not silently replace edits.
    title = obs.obs_data_get_string(_script_settings, "current_scene_title") if _script_settings else ""
    category = obs.obs_data_get_string(_script_settings, "current_scene_category") if _script_settings else ""
    try:
        save_scene_data_to_obs(_script_settings, scene_name, title, category)
    except (RecursionError, SceneMapError) as error:
        _log_scene_map_error("No se pudieron guardar los campos antes de aplicar; se enviarán los valores visibles", error)

    final_title = f"{title if title else clean_scene_name(scene_name)}{global_suffix}"
    final_category = category if category else clean_scene_name(scene_name)
    obs.script_log(
        obs.LOG_INFO,
        f"[{PLUGIN_NAME}] AplicaciÃ³n manual para escena '{scene_name}' -> TÃ­tulo: '{final_title}' | CategorÃ­a: '{final_category}'",
    )
    _start_stream_info_worker(final_title, final_category)
    return True

def on_export_settings(props, prop):
    settings_dir = obs.obs_data_get_string(_script_settings, "settings_path") or os.path.join(os.path.expanduser("~"), "Desktop")
    export_path = os.path.join(settings_dir, SETTINGS_FILENAME)
    try:
        config_data = {
            "enabled": enabled, "block_if_streaming": block_if_streaming, "update_mode": update_mode,
            "delay_seconds": delay_seconds, "global_suffix": global_suffix, "twitch_scopes": twitch_scopes,
            "broadcaster_id": broadcaster_id, "scene_map": _read_scene_map(_script_settings)
        }
        with open(export_path, "w", encoding="utf-8") as f: json.dump(config_data, f, indent=4, ensure_ascii=False)
        obs.script_log(
            obs.LOG_INFO,
            f"[{PLUGIN_NAME}] ✓ Ajustes exportados sin secretos a: {export_path}"
        )
    except Exception as e: obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error exportar: {_safe_error(e)}")
    return True

def on_import_settings(props, prop):
    global _is_importing_settings, _credential_migration_pending, _credential_backend_status
    settings_dir = obs.obs_data_get_string(_script_settings, "settings_path") or os.path.join(os.path.expanduser("~"), "Desktop")
    import_path = os.path.join(settings_dir, SETTINGS_FILENAME)
    if not os.path.exists(import_path):
        obs.script_log(obs.LOG_WARNING, f"[{PLUGIN_NAME}] No se encontró ajustes en: {import_path}")
        return True
    try:
        with open(import_path, "r", encoding="utf-8") as f: config_data = json.load(f)
        if not isinstance(config_data, dict):
            raise ValueError("El archivo de ajustes debe contener un objeto JSON")
        imported_scene_map_json = None
        if "scene_map" in config_data:
            imported_scene_map = config_data["scene_map"]
            if not isinstance(imported_scene_map, dict):
                raise ValueError("El mapa de escenas importado no es un objeto JSON")
            imported_scene_map_json = json.dumps(imported_scene_map, ensure_ascii=False)

        target_settings = _script_settings or obs.obs_data_create()
        obs.obs_data_set_bool(target_settings, "enabled", config_data.get("enabled", True))
        obs.obs_data_set_bool(target_settings, "block_if_streaming", config_data.get("block_if_streaming", False))
        obs.obs_data_set_int(target_settings, "update_mode", config_data.get("update_mode", 1))
        obs.obs_data_set_int(target_settings, "delay_seconds", config_data.get("delay_seconds", 60))
        obs.obs_data_set_string(target_settings, "global_suffix", config_data.get("global_suffix", ". No commentary. Bring your own music."))
        imported_credentials = _credentials_from_legacy_config(config_data)
        credentials_migrated = False
        if any(imported_credentials.values()):
            credentials_migrated = _migrate_credentials(
                target_settings,
                imported_credentials,
                preserve_legacy=False,
            )
            if credentials_migrated:
                obs.script_log(
                    obs.LOG_INFO,
                    f"[{PLUGIN_NAME}] Credenciales importadas y verificadas en {_credential_backend_status}."
                )
            else:
                _credential_backend_status += (
                    f"; credenciales no copiadas a OBS; conserva el archivo de importación: {import_path}."
                )
                _update_credential_status()
                obs.script_log(
                    obs.LOG_INFO,
                    f"[{PLUGIN_NAME}] [AVISO] Persistencia de credenciales importadas pendiente; "
                    f"se conserva el JSON en {import_path}."
                )
        obs.obs_data_set_string(target_settings, "twitch_scopes", config_data.get("twitch_scopes", "channel:manage:broadcast user:read:chat"))
        obs.obs_data_set_string(target_settings, "broadcaster_id", config_data.get("broadcaster_id", ""))
        if imported_scene_map_json is not None:
            obs.obs_data_set_string(
                target_settings,
                "scene_map_json",
                imported_scene_map_json,
            )
            obs.obs_data_set_string(target_settings, "scene_map", "")
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
        if any(imported_credentials.values()) and not credentials_migrated and not _credential_conflict_detected:
            _set_runtime_credentials(imported_credentials)
            _credential_migration_pending = True
            _update_credential_status()
        if props: obs.obs_properties_apply_settings(props, target_settings)
        if not _script_settings: obs.obs_data_release(target_settings)
        obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] ✓ Ajustes importados correctamente.")
    except Exception as e: obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [ERROR] Error importar: {_safe_error(e)}")
    return True

def handle_scene_changed():
    global _last_selected_scene
    active_scene = get_active_scene_name()
    t_saved, c_saved = get_scene_data_from_obs(_script_settings, active_scene)
    if _script_settings:
        obs.obs_data_set_string(_script_settings, "selected_scene", active_scene)
        obs.obs_data_set_string(_script_settings, "current_scene_title", t_saved)
        obs.obs_data_set_string(_script_settings, "current_scene_category", c_saved)
        _last_selected_scene = active_scene
        if _scene_ui_properties:
            obs.obs_properties_apply_settings(_scene_ui_properties, _script_settings)
    if update_mode == 1:
        obs.timer_remove(execute_stream_info_update)
        execute_stream_info_update()
    elif update_mode == 2:
        obs.timer_remove(execute_stream_info_update)
        obs.timer_add(execute_stream_info_update, delay_seconds * 1000)

def on_event(event):
    if not enabled: return
    if event == obs.OBS_FRONTEND_EVENT_SCENE_CHANGED: handle_scene_changed()


def _set_credential_validation_result(status, log_message):
    global _credential_validation_status
    _credential_validation_status = status
    _update_credential_status()
    obs.script_log(obs.LOG_INFO, f"[{PLUGIN_NAME}] [Check] {log_message}")


def _format_token_expiration(expires_in):
    if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or expires_in < 0:
        return "vencimiento no informado"
    remaining = int(expires_in)
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds = divmod(remaining, 60)
    parts = []
    if days:
        parts.append(f"{days} d")
    if hours or days:
        parts.append(f"{hours} h")
    if minutes or hours or days:
        parts.append(f"{minutes} min")
    if not parts:
        parts.append(f"{seconds} s")
    return "vence en " + " ".join(parts)


def check_obs_twitch_integration():
    if not twitch_oauth_token:
        _set_credential_validation_result(
            "No hay access token para comprobar.",
            "No hay access token configurado.",
        )
        return False
    _credential_validation_status = "Comprobando access token con Twitch..."
    _update_credential_status()
    headers = {"Authorization": _oauth_authorization_header(twitch_oauth_token)}
    url = "https://id.twitch.tv/oauth2/validate"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers, method="GET"),
            timeout=HTTP_TIMEOUT_SECONDS
        ) as response:
            validation = json.loads(response.read().decode("utf-8"))
            if not isinstance(validation, dict):
                raise ValueError("La respuesta de validación no es un objeto JSON.")
            validated_client_id = validation.get("client_id")
            if not isinstance(validated_client_id, str) or not validated_client_id:
                raise ValueError("La respuesta de validación no incluye un Client ID.")
            expiration = _format_token_expiration(validation.get("expires_in"))
            if not twitch_client_id or not twitch_client_id.strip():
                _set_credential_validation_result(
                    "Token válido, pero falta configurar el Client ID.",
                    "Token VÁLIDO, pero falta configurar el Client ID.",
                )
                return False
            if twitch_client_id and validated_client_id != twitch_client_id.strip():
                _set_credential_validation_result(
                    f"Access token válido, pero el Client ID no coincide; {expiration}.",
                    f"Token VÁLIDO, pero el Client ID configurado no coincide; {expiration}.",
                )
                return False
            scopes = validation.get("scopes", [])
            if not isinstance(scopes, list):
                raise ValueError("Twitch no devolvió la lista de permisos del token.")
            if "channel:manage:broadcast" not in scopes:
                _set_credential_validation_result(
                    "Token válido, pero le falta el permiso channel:manage:broadcast.",
                    "Token VÁLIDO, pero falta el permiso requerido channel:manage:broadcast.",
                )
                return False
            token_user_id = validation.get("user_id")
            if broadcaster_id and token_user_id and broadcaster_id.strip() != token_user_id:
                _set_credential_validation_result(
                    "Token válido, pero Broadcaster ID no coincide con la cuenta del token.",
                    "Token VÁLIDO, pero Broadcaster ID no coincide con user_id del token.",
                )
                return False
            _set_credential_validation_result(
                f"Access token válido según Twitch; {expiration}.",
                f"✓ Token VÁLIDO; {expiration}.",
            )
            return True
    except urllib.error.HTTPError as he:
        if he.code == 401:
            _set_credential_validation_result(
                "Access token rechazado por Twitch (HTTP 401); prueba a renovarlo.",
                "✗ Token rechazado por Twitch (HTTP 401); prueba a renovarlo.",
            )
        else:
            _set_credential_validation_result(
                f"No se pudo comprobar el token: error HTTP {he.code}.",
                f"✗ Error HTTP {he.code} comprobando el token.",
            )
        return False
    except (ValueError, UnicodeDecodeError) as error:
        detail = _safe_error(error)
        _set_credential_validation_result(
            "Twitch devolvió una respuesta de validación no válida.",
            f"✗ Respuesta de validación no válida: {detail}",
        )
        return False
    except urllib.error.URLError as error:
        _set_credential_validation_result(
            "Falló la comprobación por un error de conexión.",
            f"✗ Error de conexión comprobando el token: {_safe_error(error)}",
        )
        return False
    except TimeoutError as error:
        _set_credential_validation_result(
            "Falló la comprobación por timeout.",
            f"✗ Timeout comprobando el token: {_safe_error(error)}",
        )
        return False
    except OSError as error:
        _set_credential_validation_result(
            "Falló la comprobación por un error de conexión.",
            f"✗ Error de conexión comprobando el token: {_safe_error(error)}",
        )
        return False
    return False

def script_load(settings):
    global _script_settings
    _script_settings = settings
    obs.timer_add(_process_main_thread_actions, 100)
    obs.obs_frontend_add_event_callback(on_event)
    obs.script_log(obs.LOG_INFO, f"{PLUGIN_NAME} cargado.")

def script_unload():
    _http_stop_event.set()
    with _http_workers_lock:
        workers = list(_http_workers)
    shutdown_deadline = time.monotonic() + HTTP_TIMEOUT_SECONDS + 2
    for worker in workers:
        worker.join(timeout=max(0, shutdown_deadline - time.monotonic()))
    _process_main_thread_actions()
    obs.timer_remove(execute_stream_info_update)
    obs.timer_remove(run_proactive_refresh)
    obs.timer_remove(_process_main_thread_actions)
    obs.obs_frontend_remove_event_callback(on_event)
    obs.script_log(obs.LOG_INFO, f"{PLUGIN_NAME} descargado.")
    if any(worker.is_alive() for worker in workers):
        obs.script_log(
            obs.LOG_WARNING,
            f"[{PLUGIN_NAME}] Una llamada de red sigue finalizando; no volverá a llamar a OBS.",
        )
