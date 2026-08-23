# Twitch Event Actions

Plugin unificado de OBS para automatizar acciones basadas en eventos en tiempo real de Twitch.

## Características

- **Motor de eventos**: Basado en un listener de IRC conectado a tu canal de Twitch.
- **Acciones configurables**:
  - **Chat**: Reacciona a mensajes de chat mediante:
    - **Texto**: Muestra el mensaje en una fuente de texto de OBS.
    - **Texto multilinea**: Acumula y formatea mensajes de chat en un panel único.
    - **Mostrar/Ocultar**: Activa fuentes o escenas específicas al recibir eventos.
- **Autenticación simplificada**: Soporte para autenticación inteligente mediante Playwright o generación manual de tokens.
- **Gestión de ajustes**: Permite importar y exportar configuraciones completas entre perfiles de OBS.

## Instalación

Ejecuta `install.ps1` como Administrador o usa `install-all.bat` desde la raíz del repositorio.

Luego, en OBS: **Herramientas → Scripts → [+] → twitch-event-actions.py**.

## Configuración inicial

1. Asegúrate de configurar tus credenciales de Twitch en la pestaña de configuración (se recomienda usar el botón "Detectar ID y Canal" tras haber autenticado).
2. Selecciona la fuente de destino para las acciones (Chat, Suscripciones, etc.) en los paneles correspondientes.
3. Puedes realizar pruebas de funcionalidad utilizando el botón "Probar Acción Chat".
