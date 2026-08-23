# Mejores Pendientes para record-paths-by-scene

## 1. Detección automática del ejecutable / juego activo (NVIDIA App/GeForce Experience compatibility)
- **Idea:** En lugar de depender únicamente del nombre de la escena en OBS o del mapa estático `SCENE_ALIASES`, inspeccionar las fuentes de la escena activa (específicamente la fuente de tipo **Game Capture** / **Captura de Juego** o **Window Capture** / **Captura de Ventana**).
- **Objetivo:** Extraer el nombre del ejecutable del juego en ejecución (ejemplo `bfv.exe` -> `Battlefield V` o el título de la ventana) para nombrar la carpeta de salida exactamente como lo hace NVIDIA ShadowPlay / App.
- **Beneficio:** Evita duplicar carpetas para un mismo juego cuando se graba a través de OBS o NVIDIA, alineando las rutas de guardado sin requerir alias manuales para cada juego.

## 2. Soporte de mapeo dinámico desde la UI de OBS
- Permitir configurar alias o patrones de coincidencia de nombres directamente en las propiedades del script en la interfaz de OBS Studio, en lugar de modificar el diccionario `SCENE_ALIASES` en el propio código Python.
