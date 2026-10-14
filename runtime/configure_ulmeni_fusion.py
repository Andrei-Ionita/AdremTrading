"""Stage Ulmeni secrets in Railway using hidden input and stdin, never argv."""
import getpass
import json
import shutil
import subprocess


def main():
    railway = shutil.which("railway")
    username = getpass.getpass("FusionSolar username (hidden): ")
    password = getpass.getpass("FusionSolar password (hidden): ")
    if not username or not password or not railway:
        raise RuntimeError("Missing configuration")
    for key, value in (("ULMENI_FUSIONSOLAR_USERNAME", username),
                       ("ULMENI_FUSIONSOLAR_PASSWORD", password)):
        subprocess.run([
            railway, "variables", "--project", "119836b2-1c9a-424f-a190-177750a338e4",
            "--environment", "14a08f03-4191-4378-932b-eaf12cc649be",
            "--service", "d69a5990-a6ba-4b75-9bc9-6f94b6c4ba3d",
            "--set-from-stdin", key, "--skip-deploys",
        ], input=value, text=True, capture_output=True, check=True, timeout=60)
        print(json.dumps({"variable": key, "status": "staged_without_deploy"}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__}))
        raise SystemExit(1)
