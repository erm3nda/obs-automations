import sys
import importlib.util
import subprocess

def main():
    packages = {
        "playwright": None,
        "cv2": "opencv-python",
    }
    missing = []

    for module, pip_name in packages.items():
        if importlib.util.find_spec(module) is None:
            missing.append(pip_name)

    if not missing:
        print("✓ Todas las dependencias requeridas ya están instaladas.")
        return 0

    print(f"Faltan las siguientes librerías: {missing}")
    print("Instalando vía pip...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing)
        print("✓ ¡Dependencias instaladas con éxito!")
        try:
            subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
            print("✓ Navegadores de Playwright instalados.")
        except Exception as e:
            print(f"⚠ No se pudieron instalar navegadores de Playwright: {e}")
        return 0
    except Exception as e:
        print(f"❌ Error al instalar dependencias: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())