"""TLS utilities for fake cTrader server."""

from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from twisted.internet import ssl


def make_self_signed_context() -> ssl.CertificateOptions:
    """Generate a self-signed certificate and return Twisted CertificateOptions.

    Returns:
        CertificateOptions with a self-signed cert valid for 1 day.
    """
    backend = default_backend()

    # Generate RSA-2048 key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=backend,
    )

    # Create a self-signed certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "localhost"),
    ])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key, hashes.SHA256(), backend)
    )

    # Serialize key and certificate to PEM format
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    # Combine both into a single PEM file (cert + key)
    combined_pem = cert_pem + private_pem

    # Load with Twisted's PrivateCertificate and get CertificateOptions
    private_cert = ssl.PrivateCertificate.loadPEM(combined_pem)
    return private_cert.options()
