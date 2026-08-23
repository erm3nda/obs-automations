import sys
import os
import json
import time
import traceback
import asyncio
from playwright.async_api import async_playwright

def get_paths():
    home_dir = os.path.expanduser("~")
    base_dir = os.path.join(home_dir, ".obs_automations")
    try:
        os.makedirs(base_dir, exist_ok=True)
    except Exception:
        pass
    profile_dir = os.path.join(base_dir, "playwright_profile")
    tokens_file = os.path.join(base_dir, "tokens.json")
    crash_log = os.path.join(base_dir, "crash.log")
    return profile_dir, tokens_file, crash_log

async def run_login():
    profile_dir, _, _ = get_paths()
    print("=========================================================")
    print("  PLAYWRIGHT TWITCH LOGIN - obs-automations")
    print("=========================================================")
    print(f"Usando perfil en: {profile_dir}")
    print("Abriendo el navegador de login (Visible)...")
    print("Por favor, inicia sesión en tu cuenta de Twitch, resuelve el 2FA")
    print("y cuando estés en tu página de inicio de Twitch (o panel),")
    print("CIERRA LA VENTANA DEL NAVEGADOR para terminar.")
    print("=========================================================")
    
    chrome_path = os.environ.get("CHROME_PATH")
    launch_args = {
        "user_data_dir": profile_dir,
        "headless": False,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "ignore_default_args": ["--enable-automation"],
        "args": ["--disable-blink-features=AutomationControlled"]
    }
    if chrome_path and os.path.exists(chrome_path):
        launch_args["executable_path"] = chrome_path
        print(f"Usando ejecutable de Chrome personalizado: {chrome_path}")
    
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(**launch_args)
        page = await context.new_page()
        # Ocultar navigator.webdriver en JS
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        await page.goto("https://twitch.tv/login")
        
        # Esperar a que el usuario cierre el navegador
        while len(context.pages) > 0:
            try:
                await asyncio.sleep(0.5)
            except KeyboardInterrupt:
                break
        await context.close()
    print("Paso de Login finalizado.")

async def run_smart_auth():
    profile_dir, tokens_file, _ = get_paths()
    print("=========================================================")
    print("  PLAYWRIGHT TWITCH SMART AUTH - obs-automations")
    print("=========================================================")

    scopes = "channel:manage:broadcast%20user:read:chat"
    if len(sys.argv) > 2:
        raw_scopes = sys.argv[2].strip()
        scopes = "%20".join([s.strip() for s in raw_scopes.split() if s.strip()])

    chrome_path = os.environ.get("CHROME_PATH")
    is_visible = "--visible" in sys.argv
    
    # 1. Intentar primero de forma Headless para ver si ya hay sesión guardada
    launch_args = {
        "user_data_dir": profile_dir,
        "headless": not is_visible,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "ignore_default_args": ["--enable-automation"],
        "args": ["--disable-blink-features=AutomationControlled"]
    }
    if chrome_path and os.path.exists(chrome_path):
        launch_args["executable_path"] = chrome_path

    client_id = "gp762nuuoqcoxypju8c569th9wz7q5"
    auth_url = f"https://id.twitch.tv/oauth2/authorize?response_type=code&client_id={client_id}&redirect_uri=https://twitchtokengenerator.com&scope={scopes}"

    print("Comprobando sesión guardada en Twitch...")
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(**launch_args)
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        await page.goto(auth_url)
        await page.wait_for_timeout(3000)

        # Si redirige a login, la sesión no existe o caducó -> Abrir navegador visible para login
        if "twitch.tv" in page.url and ("login" in page.url or "passport" in page.url):
            await context.close()
            print("⚠ No hay sesión activa en Twitch. Abriendo navegador visible para inicio de sesión...")
            print("Por favor, inicia sesión, resuelve el 2FA y CIERRA LA VENTANA cuando estés listo.")
            
            launch_args["headless"] = False
            async with async_playwright() as p2:
                context2 = await p2.chromium.launch_persistent_context(**launch_args)
                page2 = await context2.new_page()
                await page2.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                await page2.goto("https://twitch.tv/login")
                while len(context2.pages) > 0:
                    try:
                        await asyncio.sleep(0.5)
                    except KeyboardInterrupt:
                        break
                await context2.close()

            # Reintentar en modo headless tras el login manual
            print("Reintentando generación automática de tokens con la nueva sesión...")
            launch_args["headless"] = True
            context = await p.chromium.launch_persistent_context(**launch_args)
            page = await context.new_page()
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            await page.goto(auth_url)
            await page.wait_for_timeout(3000)

        # Autorizar si aparece el botón de pasaporte
        if "twitch.tv" in page.url:
            try:
                auth_button = page.locator('button:has-text("Autorizar"), button:has-text("Authorize"), [data-a-target="passport-authorize-button"]')
                if await auth_button.count() > 0:
                    await auth_button.click()
                    print("✓ Botón de autorizar pulsado automáticamente.")
            except Exception as e:
                print(f"No se pudo autorizar automáticamente: {e}")

        # Esperar redirección a twitchtokengenerator.com
        print("Esperando redirección final a TwitchTokenGenerator...")
        try:
            await page.wait_for_url("**/twitchtokengenerator.com**", timeout=30000)
        except Exception:
            print("❌ ERROR: Tiempo de espera agotado esperando la redirección.")
            await context.close()
            sys.exit(1)

        # Extraer tokens
        print("Extrayendo tokens...")
        try:
            await page.wait_for_selector('xpath=//*[contains(text(), "ACCESS TOKEN") or contains(text(), "Access Token")]/following::input[1]', timeout=10000)
            access_token = await page.locator('xpath=//*[contains(text(), "ACCESS TOKEN") or contains(text(), "Access Token")]/following::input[1]').input_value()
            refresh_token = await page.locator('xpath=//*[contains(text(), "REFRESH TOKEN") or contains(text(), "Refresh Token")]/following::input[1]').input_value()
            client_id_extracted = await page.locator('xpath=//*[contains(text(), "CLIENT ID") or contains(text(), "Client Id")]/following::input[1]').input_value()
            
            if access_token and refresh_token:
                if os.path.exists(tokens_file):
                    try:
                        os.remove(tokens_file)
                    except Exception:
                        pass
                data = {
                    "twitch_oauth_token": access_token.strip(),
                    "twitch_refresh_token": refresh_token.strip(),
                    "twitch_client_id": client_id_extracted.strip() if client_id_extracted else client_id
                }
                with open(tokens_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
                print("=========================================================")
                print("✓ ¡TOKENS GENERADOS Y GUARDADOS CON ÉXITO!")
                print("=========================================================")
            else:
                print("❌ ERROR: No se pudieron leer los valores de los tokens.")
                sys.exit(1)
        except Exception as e:
            print(f"❌ ERROR al extraer los tokens: {e}")
            sys.exit(1)
        
        await context.close()
    
    # Eliminar archivo de tokens anterior si existe
    if os.path.exists(tokens_file):
        try:
            os.remove(tokens_file)
        except Exception:
            pass

    chrome_path = os.environ.get("CHROME_PATH")
    launch_args = {
        "user_data_dir": profile_dir,
        "headless": True,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "ignore_default_args": ["--enable-automation"],
        "args": ["--disable-blink-features=AutomationControlled"]
    }
    if chrome_path and os.path.exists(chrome_path):
        launch_args["executable_path"] = chrome_path
        print(f"Usando ejecutable de Chrome personalizado (Headless): {chrome_path}")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(**launch_args)
        page = await context.new_page()
        # Ocultar navigator.webdriver en JS
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        # URL de autorización con el Client ID oficial de TwitchTokenGenerator
        client_id = "gp762nuuoqcoxypju8c569th9wz7q5"
        auth_url = f"https://id.twitch.tv/oauth2/authorize?response_type=code&client_id={client_id}&redirect_uri=https://twitchtokengenerator.com&scope={scopes}"
        
        print("Accediendo a la autorización de Twitch de forma invisible...")
        await page.goto(auth_url)
        
        # Esperar un momento a que resuelva la página
        await page.wait_for_timeout(3000)
        
        # Detectar si nos redirige a la página de login (sesión caducada)
        if "twitch.tv" in page.url and ("login" in page.url or "passport" in page.url):
            print("❌ ERROR: El inicio de sesión en Twitch ha caducado o no se ha realizado.")
            print("           No se puede generar el token de forma invisible.")
            print("           Por favor, usa la opción '1. Iniciar Sesión en Twitch (Playwright)' en OBS.")
            await context.close()
            sys.exit(1)
            
        if "twitch.tv" in page.url:
            print("Página de autorización detectada. Intentando autorizar automáticamente...")
            try:
                # Buscar el botón de autorizar (Twitch Passport)
                auth_button = page.locator('button:has-text("Autorizar"), button:has-text("Authorize"), [data-a-target="passport-authorize-button"]')
                if await auth_button.count() > 0:
                    await auth_button.click()
                    print("✓ Botón de autorizar pulsado automáticamente.")
            except Exception as e:
                print(f"No se pudo autorizar automáticamente: {e}")

        # Esperar redirección a twitchtokengenerator.com
        print("Esperando redirección final a TwitchTokenGenerator...")
        try:
            await page.wait_for_url("**/twitchtokengenerator.com**", timeout=30000)
        except Exception:
            print("❌ ERROR: Tiempo de espera agotado esperando la redirección a twitchtokengenerator.com.")
            print("           Posiblemente necesites volver a loguearte en Twitch (Paso 1).")
            await context.close()
            sys.exit(1)

        # Extraer tokens
        print("Extrayendo tokens...")
        try:
            await page.wait_for_selector('xpath=//*[contains(text(), "ACCESS TOKEN") or contains(text(), "Access Token")]/following::input[1]', timeout=10000)
            access_token = await page.locator('xpath=//*[contains(text(), "ACCESS TOKEN") or contains(text(), "Access Token")]/following::input[1]').input_value()
            refresh_token = await page.locator('xpath=//*[contains(text(), "REFRESH TOKEN") or contains(text(), "Refresh Token")]/following::input[1]').input_value()
            client_id_extracted = await page.locator('xpath=//*[contains(text(), "CLIENT ID") or contains(text(), "Client Id")]/following::input[1]').input_value()
            
            if access_token and refresh_token:
                if os.path.exists(tokens_file):
                    try:
                        os.remove(tokens_file)
                    except Exception:
                        pass
                data = {
                    "twitch_oauth_token": access_token.strip(),
                    "twitch_refresh_token": refresh_token.strip(),
                    "twitch_client_id": client_id_extracted.strip() if client_id_extracted else client_id
                }
                with open(tokens_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
                print("=========================================================")
                print("✓ ¡TOKENS GENERADOS Y GUARDADOS CON ÉXITO!")
                print("=========================================================")
            else:
                print("❌ ERROR: No se pudieron leer los valores de los tokens en la web.")
                sys.exit(1)
        except Exception as e:
            print(f"❌ ERROR al extraer los tokens de la web: {e}")
            sys.exit(1)
        
        await context.close()

if __name__ == "__main__":
    try:
        if "--login" in sys.argv:
            asyncio.run(run_login())
        else:
            asyncio.run(run_smart_auth())
            
    except Exception as e:
        # En caso de crash completo, escribir en crash.log para depuración
        _, _, crash_log = get_paths()
        try:
            with open(crash_log, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        print(f"\n❌ CRITICAL CRASH: {e}")
        print("Detalles guardados en crash.log. La ventana se cerrará en 10 segundos...")
        time.sleep(10)