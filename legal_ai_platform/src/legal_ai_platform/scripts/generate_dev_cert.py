"""Generate a self-signed TLS certificate for local / LAN HTTP/2 development.

Usage:
    python -m legal_ai_platform.scripts.generate_dev_cert
    python -m legal_ai_platform.scripts.generate_dev_cert --san IP:192.168.1.42 DNS:myhost.local
"""

from __future__ import annotations

import argparse
import ipaddress
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PLATFORM_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_CERT_DIR = _PLATFORM_ROOT / "certs"
_DEFAULT_CERT = _DEFAULT_CERT_DIR / "dev-cert.pem"
_DEFAULT_KEY = _DEFAULT_CERT_DIR / "dev-key.pem"

_DEFAULT_SANS = ["DNS:localhost", "IP:127.0.0.1"]


def _build_openssl_config(sans: list[str]) -> str:
    dns_entries: list[str] = []
    ip_entries: list[str] = []
    for entry in sans:
        if entry.startswith("DNS:"):
            dns_entries.append(entry[4:])
        elif entry.startswith("IP:"):
            ip_entries.append(entry[3:])
    dns_block = "\n".join(f"DNS.{i + 1} = {name}" for i, name in enumerate(dns_entries))
    ip_block = "\n".join(f"IP.{i + 1} = {addr}" for i, addr in enumerate(ip_entries))
    alt_block = "\n".join(filter(None, [dns_block, ip_block]))
    return f"""[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = legal-ai-gateway

[v3_req]
subjectAltName = @alt_names

[alt_names]
{alt_block}
"""


def _generate_with_openssl(
    cert_path: Path,
    key_path: Path,
    sans: list[str],
    days: int,
) -> None:
    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("openssl-not-found")

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    config_text = _build_openssl_config(sans)
    config_path = cert_path.parent / "openssl-san.cnf"
    config_path.write_text(config_text, encoding="utf-8")

    cmd = [
        openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key_path),
        "-out",
        str(cert_path),
        "-days",
        str(days),
        "-config",
        str(config_path),
        "-extensions",
        "v3_req",
    ]
    subprocess.run(cmd, check=True)
    config_path.unlink(missing_ok=True)


def _parse_sans(sans: list[str]) -> list:
    from cryptography import x509

    entries: list = []
    for entry in sans:
        if entry.startswith("DNS:"):
            entries.append(x509.DNSName(entry[4:]))
        elif entry.startswith("IP:"):
            entries.append(x509.IPAddress(ipaddress.ip_address(entry[3:])))
    return entries


def _generate_with_cryptography(
    cert_path: Path,
    key_path: Path,
    sans: list[str],
    days: int,
) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "legal-ai-gateway")]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName(_parse_sans(sans)),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def generate_cert(
    cert_path: Path,
    key_path: Path,
    sans: list[str],
    days: int = 825,
) -> None:
    try:
        _generate_with_openssl(cert_path, key_path, sans, days)
    except RuntimeError:
        _generate_with_cryptography(cert_path, key_path, sans, days)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a self-signed TLS cert for HTTP/2 gateway development.",
    )
    parser.add_argument(
        "--cert",
        type=Path,
        default=_DEFAULT_CERT,
        help=f"Certificate output path (default: {_DEFAULT_CERT})",
    )
    parser.add_argument(
        "--key",
        type=Path,
        default=_DEFAULT_KEY,
        help=f"Private key output path (default: {_DEFAULT_KEY})",
    )
    parser.add_argument(
        "--san",
        action="append",
        default=[],
        metavar="TYPE:VALUE",
        help="Subject Alternative Name, e.g. IP:192.168.1.10 or DNS:gateway.local",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=825,
        help="Certificate validity in days (default: 825)",
    )
    args = parser.parse_args()

    sans = _DEFAULT_SANS + args.san
    generate_cert(args.cert.resolve(), args.key.resolve(), sans, days=args.days)

    print(f"Wrote certificate: {args.cert.resolve()}")
    print(f"Wrote private key:  {args.key.resolve()}")
    print(f"SubjectAltName:     {', '.join(sans)}")
    print()
    print("Set in legal_ai_platform/.env:")
    print("  PLATFORM_HTTP2=true")
    print(f"  PLATFORM_SSL_CERTFILE={args.cert.as_posix()}")
    print(f"  PLATFORM_SSL_KEYFILE={args.key.as_posix()}")
    print()
    print("Start the gateway:")
    print("  legal-ai-gateway")
    print()
    print("Java clients must trust this certificate (import into truststore or disable")
    print("verification only in development). Use https://HOST:8080 as the base URL.")


if __name__ == "__main__":
    main()
