"""WebAuthn/Passkey support for the local Universe Manager web UI.

Only public credential material is kept here.  Private keys and biometric data
never leave the user's authenticator and are never written by this module.
"""

import base64
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_AUTH_PATH = ROOT / "data" / "local" / "universe-auth.json"
CHALLENGE_TTL_SECONDS = 300


class AuthError(ValueError):
    """A user-safe authentication or configuration error."""


def _now():
    return int(time.time())


def _options(options):
    return json.loads(options_to_json(options))


class CredentialStore:
    """Atomic storage for WebAuthn public credential metadata only."""

    def __init__(self, path=DEFAULT_AUTH_PATH):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return {"version": 1, "credentials": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AuthError("인증 정보 파일을 읽을 수 없습니다.") from exc
        if not isinstance(data, dict) or not isinstance(data.get("credentials"), list):
            raise AuthError("인증 정보 파일 형식이 올바르지 않습니다.")
        return data

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(prefix=".universe-auth-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def credentials(self):
        return self.load()["credentials"]

    def find(self, credential_id):
        return next((item for item in self.credentials() if item.get("credentialId") == credential_id), None)

    def add(self, verified):
        data = self.load()
        credential_id = bytes_to_base64url(verified.credential_id)
        if any(item.get("credentialId") == credential_id for item in data["credentials"]):
            raise AuthError("이미 등록된 인증 수단입니다.")
        data["credentials"].append(
            {
                "credentialId": credential_id,
                "publicKey": bytes_to_base64url(verified.credential_public_key),
                "signCount": verified.sign_count,
                "createdAt": _now(),
            }
        )
        self.save(data)

    def update_sign_count(self, credential_id, sign_count):
        data = self.load()
        for item in data["credentials"]:
            if item.get("credentialId") == credential_id:
                item["signCount"] = sign_count
                self.save(data)
                return
        raise AuthError("등록된 인증 수단을 찾을 수 없습니다.")


class WebAuthnService:
    """Challenge lifecycle and verification; cryptography stays in py_webauthn."""

    def __init__(self, rp_id=None, origin=None, store=None, now=_now):
        self.rp_id = rp_id or os.environ.get("UNIVERSE_WEBAUTHN_RP_ID")
        self.origin = origin or os.environ.get("UNIVERSE_WEBAUTHN_ORIGIN")
        self.store = store or CredentialStore()
        self.now = now
        self.challenges = {}

    @property
    def configured(self):
        return bool(self.rp_id and self.origin and self.origin.startswith("https://"))

    def status(self):
        return {
            "configured": self.configured,
            "hasCredential": bool(self.store.credentials()),
        }

    def _require_configured(self):
        if not self.configured:
            raise AuthError("Face ID 로그인은 Tailscale Serve HTTPS 주소와 RP ID/Origin 설정이 필요합니다.")

    def _remember(self, kind, challenge, extra=None):
        ceremony_id = secrets.token_urlsafe(24)
        self.challenges[ceremony_id] = {"kind": kind, "challenge": challenge, "createdAt": self.now(), **(extra or {})}
        return ceremony_id

    def _take(self, ceremony_id, kind):
        record = self.challenges.pop(ceremony_id, None)
        if not record or record["kind"] != kind or self.now() - record["createdAt"] > CHALLENGE_TTL_SECONDS:
            raise AuthError("인증 요청이 만료되었습니다. 다시 시도하세요.")
        return record

    def registration_options(self):
        self._require_configured()
        challenge = secrets.token_bytes(32)
        exclude = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(item["credentialId"])) for item in self.store.credentials()]
        options = generate_registration_options(
            rp_id=self.rp_id,
            rp_name="Universe Manager",
            user_name="universe-admin",
            user_display_name="Universe Manager 관리자",
            user_id=b"universe-manager-admin",
            challenge=challenge,
            exclude_credentials=exclude or None,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        return {"ceremonyId": self._remember("registration", challenge), "publicKey": _options(options)}

    def finish_registration(self, ceremony_id, credential):
        self._require_configured()
        record = self._take(ceremony_id, "registration")
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=record["challenge"],
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                require_user_verification=True,
            )
            self.store.add(verified)
        except AuthError:
            raise
        except Exception as exc:
            raise AuthError("Face ID 등록을 확인할 수 없습니다. 주소와 인증 수단을 확인하세요.") from exc
        return {"ok": True}

    def authentication_options(self):
        self._require_configured()
        credentials = self.store.credentials()
        if not credentials:
            raise AuthError("등록된 Face ID 로그인 수단이 없습니다. 관리자에게 등록을 요청하세요.")
        challenge = secrets.token_bytes(32)
        options = generate_authentication_options(
            rp_id=self.rp_id,
            challenge=challenge,
            allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(item["credentialId"])) for item in credentials],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return {"ceremonyId": self._remember("authentication", challenge), "publicKey": _options(options)}

    def finish_authentication(self, ceremony_id, credential):
        self._require_configured()
        record = self._take(ceremony_id, "authentication")
        credential_id = credential.get("id") if isinstance(credential, dict) else None
        stored = self.store.find(credential_id)
        if not stored:
            raise AuthError("등록되지 않은 인증 수단입니다.")
        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=record["challenge"],
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                credential_public_key=base64url_to_bytes(stored["publicKey"]),
                credential_current_sign_count=int(stored.get("signCount", 0)),
                require_user_verification=True,
            )
            self.store.update_sign_count(credential_id, verified.new_sign_count)
        except AuthError:
            raise
        except Exception as exc:
            raise AuthError("Face ID 인증을 확인할 수 없습니다. 인증이 취소되었거나 주소가 올바르지 않습니다.") from exc
        return {"ok": True}
