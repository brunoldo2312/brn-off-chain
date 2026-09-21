"""
Carteira ECDSA secp256k1 + backup criptografado (AES-256-GCM + Argon2id).
"""
import os
import json
import base64
import hashlib
import secrets

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    from argon2.low_level import hash_secret_raw, Type
    _HAS_ARGON2 = True
except ImportError:
    _HAS_ARGON2 = False


class WalletManager:

    # ── Geração de par de chaves ─────────────────────────
    @staticmethod
    def generate_keypair():
        priv = ec.generate_private_key(ec.SECP256K1())
        sk_bytes = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pk_bytes = priv.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
        sk_hex = sk_bytes.hex()
        pk_hex = pk_bytes.hex()

        address = WalletManager.address_from_public_key(pk_hex)

        return sk_hex, pk_hex, address

    @staticmethod
    def address_from_public_key(pk_hex: str) -> str:
        digest = hashlib.sha256(bytes.fromhex(pk_hex)).hexdigest()
        return "brn1" + digest[:40]

    # ── Assinatura ───────────────────────────────────────
    @staticmethod
    def sign_transaction(sk_hex: str, payload: dict) -> str:
        priv = ec.derive_private_key(
            int(sk_hex, 16), ec.SECP256K1()
        )
        message = json.dumps(payload, sort_keys=True).encode()
        sig = priv.sign(message, ec.ECDSA(hashes.SHA256()))
        return sig.hex()

    @staticmethod
    def verify_signature(pk_hex: str, payload: dict, signature_hex: str) -> bool:
        try:
            pub = ec.EllipticCurvePublicKey.from_encoded_point(
                ec.SECP256K1(), bytes.fromhex(pk_hex)
            )
            message = json.dumps(payload, sort_keys=True).encode()
            pub.verify(
                bytes.fromhex(signature_hex),
                message,
                ec.ECDSA(hashes.SHA256()),
            )
            return True
        except Exception:
            return False

    # ── Backup criptografado ─────────────────────────────
    @staticmethod
    def _derive_key(password: str, salt: bytes) -> bytes:
        if _HAS_ARGON2:
            return hash_secret_raw(
                secret=password.encode(),
                salt=salt,
                time_cost=3,
                memory_cost=65536,
                parallelism=4,
                hash_len=32,
                type=Type.ID,
            )
        # fallback PBKDF2 se argon2 indisponível
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, 200_000, dklen=32
        )

    @staticmethod
    def encrypt_wallet(payload: bytes, password: str) -> bytes:
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)
        key = WalletManager._derive_key(password, salt)
        blob = AESGCM(key).encrypt(nonce, payload, None)
        return b"BRNW" + salt + nonce + blob

    @staticmethod
    def decrypt_wallet(data: bytes, password: str) -> bytes:
        if not data.startswith(b"BRNW"):
            raise ValueError("Formato de carteira inválido.")
        salt  = data[4:20]
        nonce = data[20:32]
        blob  = data[32:]
        key = WalletManager._derive_key(password, salt)
        return AESGCM(key).decrypt(nonce, blob, None)

    # ── Identidade persistente do nó ─────────────────────
    @staticmethod
    def load_node_identity(path: str = "data/node_identity.json"):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)

        sk, pk, address = WalletManager.generate_keypair()
        ident = {
            "address":          address,
            "public_key":       pk,
            "spend_secret_key": sk,
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ident, f, indent=2)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        print(f"[wallet] identidade nova salva em {path}")
        return ident