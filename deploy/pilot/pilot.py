"""Install and operate a single-company San1C pilot. Requires Python 3.10+ and Docker Compose."""

import argparse
import csv
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ARCHIVE = "database.dump"


def fail(detail):
    raise SystemExit(detail)


def slug(name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", name):
        fail("Use 1–40 lowercase ASCII letters, digits and hyphens.")
    return name


def company_dir(company):
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    folder = (base / "San1C" / "pilot" / slug(company)).resolve()
    if ROOT in folder.parents or folder == ROOT:
        fail("The private company directory must be outside the checkout.")
    return folder


def protect(folder):
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        line = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], encoding="utf-8", errors="replace").strip()
        sid = next(csv.reader([line]))[-1]
        if not re.fullmatch(r"S-1-[0-9-]+", sid):
            fail("Cannot determine owner of the private directory.")
        subprocess.run(["icacls", str(folder), "/inheritance:r", "/grant:r",
                        f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"], check=True, stdout=subprocess.DEVNULL)
    else:
        folder.chmod(0o700)


@contextmanager
def operator_lock(folder):
    # Kernel lock is released even when a CLI process is interrupted.
    with (folder / "operator.lock").open("a+b") as file:
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, value):
    temp = path.with_name(path.name + ".new")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def make_password():
    password, salt = secrets.token_urlsafe(24), secrets.token_hex(16)
    hash_value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()
    return password, f"pbkdf2_sha256:600000:{salt}:{hash_value}"


def query_path(source):
    relative = Path(read_json(source)["query_file"])
    if relative.is_absolute() or ".." in relative.parts:
        fail("Query template path must be relative to the profile.")
    query = source.parent.resolve() / relative
    if source.parent.resolve() not in query.resolve(strict=True).parents:
        fail("Query template must be inside the profile directory.")
    return query


def snapshot_profile(source, folder):
    query = query_path(source)
    folder.mkdir(exist_ok=False)
    target = folder / query.relative_to(source.parent.resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(query, target)
    shutil.copyfile(source, folder / "profile.json")


def initialize(args, folder):
    if (folder / "instance.json").exists() or (folder / "credentials.json").exists():
        fail("This company is already initialized.")
    host = args.host.lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or not 1 <= args.port <= 65535:
        fail("Invalid HTTPS hostname or port.")
    bind = ipaddress.IPv4Address(args.bind)
    if bool(args.certificate) != bool(args.private_key):
        fail("Supply the full certificate chain and its private key together.")
    if not args.certificate and (not bind.is_loopback or host not in {"localhost", "127.0.0.1"}):
        fail("For a company network provide --certificate, --private-key and its DNS hostname.")
    certs = folder / "certs"
    certs.mkdir(exist_ok=True)
    if args.certificate:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.certificate, args.private_key)
        shutil.copyfile(args.certificate, certs / "fullchain.pem")
        shutil.copyfile(args.private_key, certs / "key.pem")
        if args.ca:
            shutil.copyfile(args.ca, folder / "ca.pem")
    tls = "tls /srv/certs/fullchain.pem /srv/certs/key.pem" if args.certificate else "tls internal"
    (folder / "Caddyfile").write_text(
        "{\n auto_https disable_redirects\n skip_install_trust\n default_sni " + host + "\n}\n"
        + host + " {\n " + tls + "\n reverse_proxy app:8000\n}\n", encoding="utf-8")
    profile = folder / ("profile-" + secrets.token_hex(4))
    snapshot_profile(args.profile.resolve(strict=True), profile)
    keys = {"upload_token": secrets.token_urlsafe(32)}
    for role in ("owner", "admin"):
        keys[role + "_password"], keys[role + "_hash"] = make_password()
    write_json(folder / "credentials.json", keys)
    cfg = dict(company=args.company, name=args.name, timezone=args.timezone, host=host, port=args.port,
               bind=str(bind), internal=not args.certificate, profile=profile.name, database="san1c", image=None,
               recovery_for=slug(args.recovery_for) if args.recovery_for else None)
    write_json(folder / "instance.json", cfg)
    print(f"Private configuration: {folder}\nHTTPS: https://{host}:{args.port}")


def compose(folder, cfg, *cmd, stdin=None, stdout=None, capture=False):
    keys = read_json(folder / "credentials.json")
    overrides = {
        "PILOT_IMAGE": cfg["image"], "PILOT_DATABASE": cfg["database"], "PILOT_BIND": cfg["bind"],
        "PILOT_PORT": str(cfg["port"]), "PILOT_PROFILE": str(folder / cfg["profile"]),
        "PILOT_CADDYFILE": str(folder / "Caddyfile"), "PILOT_CERTS": str(folder / "certs"),
        "COMPANY_NAME": cfg["name"], "COMPANY_TIMEZONE": cfg["timezone"],
        "ADMIN_PASSWORD_HASH": keys["admin_hash"], "OWNER_PASSWORD_HASH": keys["owner_hash"],
        "UPLOAD_TOKEN_SHA256": hashlib.sha256(keys["upload_token"].encode()).hexdigest() if keys.get("upload_token") else "",
    }
    # Ignore the repository's dotenv; per-company hashes travel to Compose via
    # its environment. Neither plaintext passwords nor the upload token do.
    empty = folder / "compose.env"
    empty.touch(exist_ok=True)
    command = ["docker", "compose", "--project-name", "san1c-pilot-" + cfg["company"],
               "--env-file", str(empty), "-f", str(ROOT / "compose.yaml"),
               "-f", str(HERE / "compose.pilot.yaml"), *cmd]
    env = dict(os.environ, **overrides)
    result = subprocess.run(command, check=True, cwd=ROOT, env=env, stdin=stdin,
                            stdout=subprocess.PIPE if capture else stdout)
    return result.stdout


def image_id(image):
    result = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], capture_output=True)
    return result.stdout.decode().strip() if result.returncode == 0 else None


def build(cfg, release):
    tag = f"san1c-pilot-{cfg['company']}:{slug(release)}"
    if not image_id(tag):
        subprocess.run(["docker", "build", "--label", "org.opencontainers.image.version=" + release,
                        "--tag", tag, str(ROOT)], check=True)
    return tag


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def web_client(folder):
    ca = folder / "ca.pem"
    context = ssl.create_default_context(cafile=str(ca) if ca.exists() else None)
    return build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect(), HTTPCookieProcessor(CookieJar()))


def request(client, cfg, path, data=None):
    payload = json.dumps(data).encode() if data is not None else None
    req = Request(f"https://{cfg['host']}:{cfg['port']}{path}", data=payload, headers={"Content-Type": "application/json"})
    with client.open(req, timeout=20) as reply:
        return json.load(reply)


def start(folder, cfg):
    compose(folder, cfg, "up", "-d", "--wait", "--no-build")
    for i in range(20):
        try:
            if cfg["internal"]:
                pem = compose(folder, cfg, "exec", "-T", "https", "cat", "/data/caddy/pki/authorities/local/root.crt", capture=True)
                ssl.PEM_cert_to_DER_cert(pem.decode("ascii"))
                (folder / "ca.pem").write_bytes(pem)
            if request(web_client(folder), cfg, "/api/health")["status"] == "ok":
                return
        except (ValueError, OSError, subprocess.CalledProcessError):
            pass
        time.sleep(1)
    fail("HTTPS health check failed; check the certificate chain, DNS and listener.")


def backup(folder, cfg, with_image=False):
    target = folder / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target.mkdir(parents=True)
    archive = target / ARCHIVE
    with archive.open("xb") as out:
        compose(folder, cfg, "exec", "-T", "db", "pg_dump", "-U", "san1c", "-d", cfg["database"],
                "--format=custom", "--no-owner", "--no-acl", "--exclude-table-data=public.web_sessions", stdout=out)
    with archive.open("rb") as source:
        compose(folder, cfg, "exec", "-T", "db", "pg_restore", "--list", stdin=source, stdout=subprocess.DEVNULL)
    snapshot_profile(folder / cfg["profile"] / "profile.json", target / "profile")
    manifest = {"company": cfg.get("recovery_for") or cfg["company"], "source_instance": cfg["company"],
                "name": cfg["name"], "timezone": cfg["timezone"],
                "image": cfg["image"], "image_id": image_id(cfg["image"]), "sha256": digest(archive),
                "profile_sha256": digest(target / "profile" / "profile.json"),
                "query_sha256": digest(query_path(target / "profile" / "profile.json"))}
    if with_image:
        image_file = target / "image.tar"
        subprocess.run(["docker", "image", "save", "-o", str(image_file), cfg["image"]], check=True)
        manifest["image_sha256"] = digest(image_file)
    write_json(target / "manifest.json", manifest)
    print(f"Backup: {target}", flush=True)
    return target


def sql(folder, cfg, statement):
    return compose(folder, cfg, "exec", "-T", "db", "psql", "-XAtq", "-v", "ON_ERROR_STOP=1",
                   "-U", "san1c", "-d", cfg["database"], "-c", statement, capture=True).decode().strip()


def recovery_binding(current, manifest, from_company):
    donor = manifest["company"]
    binding = current.get("recovery_for")
    own_recovery_point = manifest.get("source_instance") == current["company"] and donor == binding
    if donor != current["company"] and not own_recovery_point:
        if from_company != donor or (binding != donor and (binding or current["image"])):
            fail("Foreign-company backup. Use a fresh recovery instance and explicitly name --from-company.")
        binding = donor
    elif from_company and from_company != donor:
        fail("The backup does not belong to the specified source company.")
    return binding


def restore(folder, current, directory, from_company=None):
    manifest = read_json(directory / "manifest.json")
    binding = recovery_binding(current, manifest, from_company)
    archive = directory / ARCHIVE
    if digest(archive) != manifest["sha256"]:
        fail("Database dump checksum mismatch.")
    profile = directory / "profile" / "profile.json"
    if digest(profile) != manifest.get("profile_sha256") or digest(query_path(profile)) != manifest.get("query_sha256"):
        fail("Profile checksum mismatch.")
    if image_id(manifest["image"]) != manifest["image_id"] and (directory / "image.tar").is_file():
        if digest(directory / "image.tar") != manifest.get("image_sha256"):
            fail("Saved image checksum mismatch.")
        subprocess.run(["docker", "image", "load", "-i", str(directory / "image.tar")], check=True)
    if not manifest["image_id"] or image_id(manifest["image"]) != manifest["image_id"]:
        fail("Restore the original tagged image from the trusted release archive first.")
    suffix = secrets.token_hex(6)
    cfg = dict(current, image=manifest["image"], database="san1c_r_" + suffix, profile="profile-" + suffix,
               name=manifest["name"], timezone=manifest["timezone"], recovery_for=binding)
    snapshot_profile(directory / "profile" / "profile.json", folder / cfg["profile"])
    # Restore to a NEW database. Existing data remain intact while pg_restore,
    # migrations and offline token invalidation are checked.
    compose(folder, cfg, "up", "-d", "--wait", "db")
    compose(folder, cfg, "exec", "-T", "db", "createdb", "-U", "san1c", cfg["database"])
    with archive.open("rb") as source:
        compose(folder, cfg, "exec", "-T", "db", "pg_restore", "--single-transaction", "--exit-on-error",
                "--no-owner", "--no-acl", "-U", "san1c", "-d", cfg["database"], stdin=source)
    compose(folder, cfg, "run", "--rm", "--no-deps", "app", "alembic", "upgrade", "head")
    scrubbed = sql(folder, cfg,
        "BEGIN; DELETE FROM web_sessions; UPDATE source_access SET managed=true, token_hash=NULL,"
        "last_check_at=NULL,last_check_status=NULL WHERE id=1 RETURNING id; COMMIT;")
    if scrubbed != "1":
        fail("Missing source control row; restored database was not activated.")
    compose(folder, cfg, "stop", "https", "app")
    write_json(folder / "instance.json", cfg)
    try:
        start(folder, cfg)
    except BaseException:
        write_json(folder / "instance.json", current)
        if current["image"]:
            start(folder, current)
        raise
    keys = read_json(folder / "credentials.json")
    keys["upload_token"] = None
    write_json(folder / "credentials.json", keys)
    print(f"Restored {cfg['database']}. Upload disabled. Use rotate-upload after verification.")


def source_action(folder, cfg, enable):
    keys = read_json(folder / "credentials.json")
    client = web_client(folder)
    request(client, cfg, "/api/auth/login", {"role": "admin", "password": keys["admin_password"]})
    try:
        action = "rotate" if enable else "disable"
        reply = request(client, cfg, "/api/live/source/" + action, {})
        keys["upload_token"] = reply["upload_token"] if enable else None
        write_json(folder / "credentials.json", keys)
    finally:
        request(client, cfg, "/api/auth/logout", {})
    print("Upload token replaced. Update the 1C processor." if enable else "Upload disabled.")


def operate(args, folder):
    config_file = folder / "instance.json"
    cfg = read_json(config_file)
    if args.action == "up":
        if not cfg["image"]:
            cfg["image"] = build(cfg, args.release or "initial")
            write_json(config_file, cfg)
        elif args.release and cfg["image"] != f"san1c-pilot-{cfg['company']}:{slug(args.release)}":
            fail("Use upgrade for a new release of a running company.")
        start(folder, cfg)
    elif args.action == "backup":
        backup(folder, cfg, args.with_image)
    elif args.action in ("restore", "rollback"):
        restore(folder, cfg, args.backup.resolve(strict=True), args.from_company)
    elif args.action == "upgrade":
        new_tag = build(cfg, args.release)
        if new_tag == cfg["image"]:
            fail("Choose a new release tag.")
        next_cfg = dict(cfg, image=new_tag)
        if args.profile:
            profile = folder / ("profile-" + secrets.token_hex(6))
            snapshot_profile(args.profile.resolve(strict=True), profile)
            next_cfg["profile"] = profile.name
        # A closed ingestion window makes the pre-update copy a rollback point.
        compose(folder, cfg, "stop", "https", "app")
        archive = None
        try:
            archive = backup(folder, cfg)
            write_json(config_file, next_cfg)
            start(folder, next_cfg)
        except BaseException:
            if archive is None:
                start(folder, cfg)
            else:
                restore(folder, cfg, archive)
            raise
        print(f"Rollback point: {archive}")
    elif args.action in ("disable", "rotate-upload"):
        source_action(folder, cfg, args.action == "rotate-upload")
    elif args.action == "rotate-password":
        keys = read_json(folder / "credentials.json")
        keys[args.role + "_password"], keys[args.role + "_hash"] = make_password()
        write_json(folder / "credentials.json", keys)
        start(folder, cfg)
        print(f"Password rotated: {args.role}")
    elif args.action == "stop":
        compose(folder, cfg, "stop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("company", help="Unique company key, e.g. almaty-store")
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("init")
    init.add_argument("--name", required=True)
    init.add_argument("--profile", type=Path, required=True)
    init.add_argument("--timezone", default="Asia/Almaty")
    init.add_argument("--host", default="localhost")
    init.add_argument("--bind", default="127.0.0.1")
    init.add_argument("--port", type=int, default=8443)
    init.add_argument("--certificate", type=Path)
    init.add_argument("--private-key", type=Path)
    init.add_argument("--ca", type=Path)
    init.add_argument("--recovery-for", help="Designate this separate recovery instance for backups of one company.")
    commands.add_parser("up").add_argument("--release")
    commands.add_parser("backup").add_argument("--with-image", action="store_true")
    for cmd in ("restore", "rollback"):
        recovery = commands.add_parser(cmd)
        recovery.add_argument("backup", type=Path)
        recovery.add_argument("--from-company", help="Explicitly name the donor on a separate recovery instance.")
    upgrade = commands.add_parser("upgrade")
    upgrade.add_argument("release")
    upgrade.add_argument("--profile", type=Path)
    commands.add_parser("rotate-password").add_argument("role", choices=["owner", "admin"])
    for cmd in ("disable", "rotate-upload", "stop"):
        commands.add_parser(cmd)
    args = parser.parse_args()
    folder = company_dir(args.company)
    if args.action == "init":
        protect(folder)
    elif not (folder / "instance.json").is_file():
        fail("Initialize the per-company instance first.")
    with operator_lock(folder):
        if args.action == "init":
            initialize(args, folder)
        else:
            operate(args, folder)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
