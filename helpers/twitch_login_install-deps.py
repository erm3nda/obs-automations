import sys
import subprocess
import importlib.util
import os

def check_and_install():
    print("=========================================================")
    print("  VERIFICACIÓN E INSTALACIÓN DE DEPENDENCIAS (Playwright)")
    print("=========================================================")
    
    packages = ["playwright"]
    missing = []
    
    for pkg in packages:
        spec = importlib.util.find_spec(pkg)
        if spec is None:
            missing.append(pkg)
            
    if missing:
        print(f"Faltan las siguientes librerías: {missing}")
        print("Instalando vía pip...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
            subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing)
            print("Instalando navegadores de Playwright...")
            subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
            print("✓ ¡Dependencias instaladas con éxito!")
        except Exception as e:
            print(f"❌ Error al instalar dependencias: {e}")
            sys.exit(1)
    else:
        print("✓ Todas las dependencias requeridas ya están instaladas.")

if __name__ == "__main__":
    check_and_install()
