"""
Входна точка за PyInstaller - НЕ се стартира с 'streamlit run', а директно
като .exe. Стартира Streamlit сървъра програмно и отваря браузъра сам.
"""
import os
import sys


def resource_path(relative_path):
    """При PyInstaller --onedir бъндъла, файловете добавени с --add-data
    седят до самия .exe (в _internal папката) - това връща правилния път
    независимо дали приложението е замразено (.exe) или се стартира като
    обикновен .py скрипт."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


if __name__ == "__main__":
    from streamlit.web import cli as stcli

    app_path = resource_path("app.py")
    sys.argv = [
        "streamlit",
        "run",
        app_path,
        "--global.developmentMode=false",
        "--server.headless=false",
    ]
    sys.exit(stcli.main())
