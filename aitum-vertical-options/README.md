# Aitum Vertical Options

Script independiente para conservar la gestión de Aitum Vertical sin mezclarla con las rutas de grabación horizontal.

## Funciones

- Sincroniza la escena vertical con la escena horizontal de OBS cuando cambia.
- Puede mover los archivos verticales guardados a la carpeta de la escena correspondiente.
- Puede limpiar archivos demasiado pequeños o completamente negros usando OpenCV.
- Puede auto-iniciar grabación vertical y backtrack, desactivado por defecto.
- Incluye activación global y exportación/importación de ajustes.

Las opciones de auto-inicio se mantienen por compatibilidad, pero se recomienda dejarlas desactivadas: cerrar OBS con backtrack activo puede provocar un crash.

## Instalación

Ejecuta `install.ps1` como administrador o usa el instalador maestro. Después, en OBS: **Herramientas > Scripts > [+]** y selecciona `aitum-vertical-options.py`.

Requiere Aitum Vertical y OBS WebSocket en el puerto local predeterminado `4455` para sincronizar escenas y recibir eventos de archivos.