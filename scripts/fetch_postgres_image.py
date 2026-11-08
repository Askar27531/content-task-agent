r"""Fallback for Docker daemon registry connectivity: fetch public ECR mirror on host.

Verifies every compressed blob digest and config diff_id, writes a Docker load archive.
Does not change daemon settings, connect to an existing database or run a container.
Usage: .venv\Scripts\python scripts/fetch_postgres_image.py
       docker load --input .runtime/postgres-17-alpine.tar
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "https://public.ecr.aws"
REPOSITORY = "docker/library/postgres"
TAG = "17-alpine"
OUTPUT = ROOT / ".runtime/image-cache"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=httpx.Timeout(30, read=90), follow_redirects=True) as client:
        response = client.get(REGISTRY + "/token/", params={"service": "public.ecr.aws", "scope": f"repository:{REPOSITORY}:pull"})
        response.raise_for_status()
        headers = {"Authorization": "Bearer " + response.json()["token"], "Accept": "application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json"}
        response = client.get(f"{REGISTRY}/v2/{REPOSITORY}/manifests/{TAG}", headers=headers)
        response.raise_for_status()
        index = response.json()
        manifest_digest = next(m["digest"] for m in index["manifests"] if m.get("platform", {}).get("architecture") == "amd64" and m["platform"]["os"] == "linux")
        response = client.get(f"{REGISTRY}/v2/{REPOSITORY}/manifests/{manifest_digest}", headers=headers)
        response.raise_for_status()
        if "sha256:" + hashlib.sha256(response.content).hexdigest() != manifest_digest:
            raise ValueError("Manifest checksum mismatch")
        manifest = response.json()

        def blob(descriptor):
            digest = descriptor["digest"].split(":", 1)[1]
            target = OUTPUT / (digest + ".blob")
            if target.exists() and hashlib.file_digest(target.open("rb"), "sha256").hexdigest() == digest:
                return target
            if descriptor["size"] > 8 * 1024 * 1024:
                chunk_size = 2 * 1024 * 1024
                count = (descriptor["size"] + chunk_size - 1) // chunk_size
                def download_part(part):
                    begin = part * chunk_size
                    end = min(descriptor["size"] - 1, begin + chunk_size - 1)
                    part_file = OUTPUT / f"{digest}.{part}.part"
                    if part_file.exists() and part_file.stat().st_size == end - begin + 1:
                        return part_file
                    for attempt in range(4):
                        try:
                            response = client.get(f"{REGISTRY}/v2/{REPOSITORY}/blobs/{descriptor['digest']}", headers={**headers, "Range": f"bytes={begin}-{end}"})
                            response.raise_for_status()
                            if response.status_code != 206 or len(response.content) != end - begin + 1:
                                raise ValueError("Registry did not honor range")
                            part_file.write_bytes(response.content)
                            print(f"{digest[:12]}: part {part + 1}/{count}", flush=True)
                            return part_file
                        except (httpx.HTTPError, ValueError):
                            if attempt == 3:
                                raise
                    raise RuntimeError("Part unavailable")
                with ThreadPoolExecutor(max_workers=16) as pool:
                    parts = list(pool.map(download_part, range(count)))
                with target.open("wb") as handle:
                    for part in parts:
                        handle.write(part.read_bytes())
                if hashlib.file_digest(target.open("rb"), "sha256").hexdigest() != digest:
                    raise ValueError("Combined blob checksum mismatch")
                print("verified", digest[:12], flush=True)
                return target
            for attempt in range(4):
                try:
                    check = hashlib.sha256(); size = 0; last = time.monotonic()
                    with client.stream("GET", f"{REGISTRY}/v2/{REPOSITORY}/blobs/{descriptor['digest']}", headers=headers) as stream:
                        stream.raise_for_status()
                        with target.open("wb") as handle:
                            for chunk in stream.iter_bytes():
                                handle.write(chunk); check.update(chunk); size += len(chunk)
                                if time.monotonic() - last >= 10:
                                    print(f"{digest[:12]}: {size / 1048576:.1f}/{descriptor['size'] / 1048576:.1f} MiB", flush=True)
                                    last = time.monotonic()
                    if check.hexdigest() != digest or size != descriptor["size"]:
                        raise ValueError("Blob checksum or size mismatch")
                    print("verified", digest[:12], flush=True)
                    return target
                except (httpx.HTTPError, ValueError):
                    if attempt == 3:
                        raise
                    print("retry", digest[:12], flush=True)

        config_blob = blob(manifest["config"])
        config = json.loads(config_blob.read_text(encoding="utf-8"))
        layer_paths = []
        for descriptor, diff_id in zip(manifest["layers"], config["rootfs"]["diff_ids"], strict=True):
            source = blob(descriptor)
            target = OUTPUT / (diff_id.split(":", 1)[1] + ".tar")
            if not target.exists():
                check = hashlib.sha256()
                with gzip.open(source, "rb") as compressed, target.open("wb") as raw:
                    while chunk := compressed.read(1024 * 1024):
                        raw.write(chunk); check.update(chunk)
                if "sha256:" + check.hexdigest() != diff_id:
                    target.unlink()
                    raise ValueError("Uncompressed layer checksum mismatch")
            layer_paths.append(target)
        config_name = manifest["config"]["digest"].split(":", 1)[1] + ".json"
        archive = ROOT / ".runtime/postgres-17-alpine.tar"
        with tarfile.open(archive, "w") as tar:
            tar.add(config_blob, arcname=config_name)
            for path in layer_paths:
                tar.add(path, arcname=path.name)
            metadata = json.dumps([{"Config": config_name, "RepoTags": ["postgres:17-alpine"], "Layers": [p.name for p in layer_paths]}]).encode()
            member = tarfile.TarInfo("manifest.json"); member.size = len(metadata)
            tar.addfile(member, io.BytesIO(metadata))
        (ROOT / ".runtime/postgres-image-provenance.json").write_text(json.dumps({"source": REGISTRY, "repository": REPOSITORY, "tag": TAG, "manifest_digest": manifest_digest}, indent=2), encoding="utf-8")
        print("Archive ready:", archive, flush=True)


if __name__ == "__main__":
    main()
