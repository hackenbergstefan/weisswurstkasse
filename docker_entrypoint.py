import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

if __name__ == "__main__":
    import django
    from django.core.management import call_command

    django.setup()
    mode = sys.argv[1] if len(sys.argv) > 1 else "web"
    if mode == "web":
        call_command("migrate", interactive=False)
        call_command("generate_events")
        os.execv(
            "/app/.venv/bin/gunicorn",
            [
                "gunicorn",
                "config.wsgi:application",
                "--bind",
                "0.0.0.0:8000",
                "--workers",
                "2",
                "--timeout",
                "120",
            ],
        )
    elif mode == "worker":
        call_command("worker")
    else:
        call_command(mode, *sys.argv[2:])
