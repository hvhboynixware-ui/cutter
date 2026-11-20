# -*- coding: utf-8 -*-
"""E2E-проверка приложения по HTTP: загружаем файл, ждём задачу, смотрим клипы."""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"
ASSETS = Path(__file__).resolve().parent.parent / ".test-assets"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(path, payload):
    data = json.dumps(payload).encode()
    request = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def upload_file(path: Path, fields: dict):
    boundary = "----cuttertest"
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
        f"Content-Type: video/mp4\r\n\r\n".encode()
        + path.read_bytes()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    request = urllib.request.Request(
        BASE + "/api/analyze",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    health = get("/api/health")
    print("health:", health["ok"], "browser:", health["browser"], "models:", len(health["models"]))

    source = ASSETS / "demo_source.mp4"
    assert source.is_file(), f"нет тестового видео {source}"
    job = upload_file(source, {"clips": 3, "style": "classic", "min_dur": 8, "max_dur": 20})
    job_id = job["job_id"]
    print("job:", job_id)

    deadline = time.time() + 600
    last = ""
    while time.time() < deadline:
        detail = get(f"/api/jobs/{job_id}")
        line = f"{detail['status']} {detail['progress']:.0f}% {detail.get('stage','')} {detail.get('message','')}"
        if line != last:
            print(" ", line)
            last = line
        if detail["status"] in {"done", "error", "cancelled"}:
            break
        time.sleep(1.5)

    detail = get(f"/api/jobs/{job_id}")
    if detail["status"] != "done":
        print("FAILED:", detail.get("error"))
        print("\n".join(detail.get("logs", [])[-15:]))
        return 1

    clips = detail["result"]["clips"]
    print(f"клипов: {len(clips)}")
    for clip in clips:
        print(f"  {clip['id']} {clip['duration']}с score={clip['score']} {clip['title'][:40]}")
        assert clip["video_url"].startswith(f"/media/{job_id}/clips/")
        assert clip["preview_url"].startswith(f"/media/{job_id}/previews/")

    # файлы реально отдаются
    for kind, name in (("clips", clips[0]["file"]), ("previews", clips[0]["preview"])):
        url = f"{BASE}/media/{job_id}/{kind}/{name}"
        with urllib.request.urlopen(url, timeout=30) as response:
            body = response.read()
        print(f"  {kind}/{name}: {len(body)} байт, {response.headers.get('content-type')}")

    # список клипов отдельным эндпоинтом
    listed = get(f"/api/clips?job_id={job_id}")
    assert len(listed["clips"]) == len(clips)
    print("clips endpoint ок")

    # защита от обхода путей
    try:
        urllib.request.urlopen(f"{BASE}/media/{job_id}/clips/..%2F..%2Fsettings.json", timeout=10)
        print("ПРОБЛЕМА: отдал файл за пределами папки")
        return 1
    except urllib.error.HTTPError as exc:
        print("traversal закрыт:", exc.code)

    print("E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
